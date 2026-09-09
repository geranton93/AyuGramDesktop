// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/utils/rc_config.h"

#include "core/update_verify.h"

#include <QtCore/QJsonArray>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QJsonParseError>
#include <QtCore/QJsonValue>
#include <QtCore/QRegularExpression>

#include <algorithm>
#include <array>

namespace Ayu::RemoteConfig {
namespace {

constexpr auto kEnvelopeFormat = 1;
constexpr auto kMaxPayloadSize = 192 * 1024;
constexpr auto kMaxArrayItems = 4096;
constexpr auto kMaxBadgeTextSize = 256;
constexpr auto kMaxDonationValueSize = 32;
constexpr auto kClockSkew = 5 * 60;
constexpr auto kMaxLifetime = 30 * 24 * 60 * 60;
constexpr auto kMaxExactJsonInteger = qint64(9007199254740991LL);

void SetError(QString *error, const QString &text) {
	if (error) {
		*error = text;
	}
}

[[nodiscard]] std::optional<qint64> ReadPositiveInteger(
		const QJsonValue &value) {
	if (value.isDouble()) {
		const auto number = value.toDouble();
		if (!(number >= 1.)
			|| number > double(kMaxExactJsonInteger)
			|| double(qint64(number)) != number) {
			return std::nullopt;
		}
		return qint64(number);
	}
	if (!value.isString()) {
		return std::nullopt;
	}
	const auto text = value.toString();
	if (text.isEmpty() || text.size() > 19) {
		return std::nullopt;
	}
	for (const auto character : text) {
		if (character < u'0' || character > u'9') {
			return std::nullopt;
		}
	}
	bool ok = false;
	const auto result = text.toLongLong(&ok);
	return ok && result > 0 ? std::make_optional(result) : std::nullopt;
}

[[nodiscard]] std::optional<QByteArray> DecodeBase64Url(
		const QJsonValue &value,
		int maxSize,
		int exactSize = -1) {
	if (!value.isString()) {
		return std::nullopt;
	}
	const auto decoded = QByteArray::fromBase64Encoding(
		value.toString().toLatin1(),
		QByteArray::Base64UrlEncoding
			| QByteArray::AbortOnBase64DecodingErrors);
	if (!decoded
		|| decoded.decoded.isEmpty()
		|| decoded.decoded.size() > maxSize
		|| (exactSize >= 0 && decoded.decoded.size() != exactSize)) {
		return std::nullopt;
	}
	return decoded.decoded;
}

[[nodiscard]] std::optional<std::unordered_set<qint64>> ReadIdSet(
		const QJsonObject &object,
		const char *name,
		QString *error) {
	const auto value = object.value(QLatin1String(name));
	if (!value.isArray()) {
		SetError(error, QString::fromLatin1("Remote config field '%1' is not an array.").arg(name));
		return std::nullopt;
	}
	const auto array = value.toArray();
	if (array.size() > kMaxArrayItems) {
		SetError(error, QString::fromLatin1("Remote config field '%1' is too large.").arg(name));
		return std::nullopt;
	}
	auto result = std::unordered_set<qint64>();
	result.reserve(size_t(array.size()));
	for (const auto &entry : array) {
		const auto id = ReadPositiveInteger(entry);
		if (!id) {
			SetError(error, QString::fromLatin1("Remote config field '%1' contains an invalid id.").arg(name));
			return std::nullopt;
		}
		result.insert(*id);
	}
	return result;
}

[[nodiscard]] std::optional<QString> ReadDonationValue(
		const QJsonObject &object,
		const char *name,
		const QRegularExpression &pattern,
		QString *error) {
	const auto value = object.value(QLatin1String(name));
	if (!value.isString()) {
		SetError(error, QString::fromLatin1("Remote config field '%1' is not a string.").arg(name));
		return std::nullopt;
	}
	const auto result = value.toString();
	if (result.isEmpty()
		|| result.size() > kMaxDonationValueSize
		|| !pattern.match(result).hasMatch()) {
		SetError(error, QString::fromLatin1("Remote config field '%1' has an invalid value.").arg(name));
		return std::nullopt;
	}
	return result;
}

[[nodiscard]] bool HasOnlySupportedFields(const QJsonObject &object) {
	static constexpr std::array kFields = {
		"format",
		"issued",
		"expires",
		"developers",
		"officialChannels",
		"supporters",
		"supporterChannels",
		"customBadges",
		"donateUsername",
		"donateAmountUsd",
		"donateAmountTon",
		"donateAmountRub",
	};
	for (auto i = object.constBegin(); i != object.constEnd(); ++i) {
		const auto supported = std::any_of(
			kFields.begin(),
			kFields.end(),
			[&](const auto field) { return i.key() == QLatin1String(field); });
		if (!supported) {
			return false;
		}
	}
	return true;
}

} // namespace

std::optional<Data> Parse(
		const QByteArray &response,
		const QByteArray &publicKeyPem,
		qint64 now,
		QString *error) {
	if (response.isEmpty() || response.size() > kMaxResponseSize) {
		SetError(error, QStringLiteral("Remote config response is too large."));
		return std::nullopt;
	}

	auto parseError = QJsonParseError();
	const auto envelope = QJsonDocument::fromJson(response, &parseError);
	if (parseError.error != QJsonParseError::NoError
		|| !envelope.isObject()) {
		SetError(error, QStringLiteral("Could not parse remote config envelope."));
		return std::nullopt;
	}
	const auto envelopeObject = envelope.object();
	if (envelopeObject.size() != 3
		|| !envelopeObject.contains(QLatin1String("format"))
		|| !envelopeObject.contains(QLatin1String("payload"))
		|| !envelopeObject.contains(QLatin1String("signature"))) {
		SetError(error, QStringLiteral("Remote config envelope has unsupported fields."));
		return std::nullopt;
	}
	const auto format = envelopeObject.value(QLatin1String("format"));
	if (!format.isDouble() || format.toDouble() != double(kEnvelopeFormat)) {
		SetError(error, QStringLiteral("Remote config envelope format is unsupported."));
		return std::nullopt;
	}
	const auto payload = DecodeBase64Url(
		envelopeObject.value(QLatin1String("payload")),
		kMaxPayloadSize);
	const auto signature = DecodeBase64Url(
		envelopeObject.value(QLatin1String("signature")),
		64,
		64);
	if (!payload || !signature) {
		SetError(error, QStringLiteral("Remote config envelope encoding is invalid."));
		return std::nullopt;
	}
	if (!Core::Updates::VerifyEd25519Signature(
			*payload,
			*signature,
			publicKeyPem,
			error)) {
		return std::nullopt;
	}

	parseError = QJsonParseError();
	const auto document = QJsonDocument::fromJson(*payload, &parseError);
	if (parseError.error != QJsonParseError::NoError
		|| !document.isObject()) {
		SetError(error, QStringLiteral("Could not parse signed remote config."));
		return std::nullopt;
	}
	const auto object = document.object();
	if (!HasOnlySupportedFields(object)) {
		SetError(error, QStringLiteral("Signed remote config has unsupported fields."));
		return std::nullopt;
	}
	const auto payloadFormat = object.value(QLatin1String("format"));
	if (!payloadFormat.isDouble()
		|| payloadFormat.toDouble() != double(kEnvelopeFormat)) {
		SetError(error, QStringLiteral("Signed remote config format is unsupported."));
		return std::nullopt;
	}
	const auto issued = ReadPositiveInteger(
		object.value(QLatin1String("issued")));
	const auto expires = ReadPositiveInteger(
		object.value(QLatin1String("expires")));
	if (!issued || !expires
		|| *expires <= *issued
		|| *issued > now + kClockSkew
		|| *expires <= now
		|| *expires - *issued > kMaxLifetime) {
		SetError(error, QStringLiteral("Signed remote config validity window is invalid."));
		return std::nullopt;
	}

	auto result = Data();
	if (const auto developers = ReadIdSet(object, "developers", error)) {
		result.developers = *developers;
	} else {
		return std::nullopt;
	}
	if (const auto channels = ReadIdSet(object, "officialChannels", error)) {
		result.officialChannels = *channels;
	} else {
		return std::nullopt;
	}
	if (const auto supporters = ReadIdSet(object, "supporters", error)) {
		result.supporters = *supporters;
	} else {
		return std::nullopt;
	}
	if (const auto channels = ReadIdSet(object, "supporterChannels", error)) {
		result.supporterChannels = *channels;
	} else {
		return std::nullopt;
	}

	const auto customBadgesValue = object.value(QLatin1String("customBadges"));
	if (!customBadgesValue.isArray()
		|| customBadgesValue.toArray().size() > kMaxArrayItems) {
		SetError(error, QStringLiteral("Remote config custom badges are invalid."));
		return std::nullopt;
	}
	for (const auto &entry : customBadgesValue.toArray()) {
		if (!entry.isObject()) {
			SetError(error, QStringLiteral("Remote config custom badge is invalid."));
			return std::nullopt;
		}
		const auto badge = entry.toObject();
		if (badge.size() != 2
			|| !badge.contains(QLatin1String("id"))
			|| !badge.contains(QLatin1String("badge"))) {
			SetError(error, QStringLiteral("Remote config custom badge has unsupported fields."));
			return std::nullopt;
		}
		const auto id = ReadPositiveInteger(badge.value(QLatin1String("id")));
		const auto badgeValue = badge.value(QLatin1String("badge"));
		if (!badgeValue.isObject()) {
			SetError(error, QStringLiteral("Remote config custom badge data is invalid."));
			return std::nullopt;
		}
		const auto badgeData = badgeValue.toObject();
		if (badgeData.size() < 1
			|| badgeData.size() > 2
			|| !badgeData.contains(QLatin1String("documentId"))
			|| (badgeData.size() == 2
				&& !badgeData.contains(QLatin1String("text")))) {
			SetError(error, QStringLiteral("Remote config custom badge data has unsupported fields."));
			return std::nullopt;
		}
		const auto documentId = ReadPositiveInteger(
			badgeData.value(QLatin1String("documentId")));
		if (!id || !documentId) {
			SetError(error, QStringLiteral("Remote config custom badge id is invalid."));
			return std::nullopt;
		}
		const auto text = badgeData.value(QLatin1String("text"));
		if (!text.isUndefined()
			&& (!text.isString() || text.toString().size() > kMaxBadgeTextSize)) {
			SetError(error, QStringLiteral("Remote config custom badge text is invalid."));
			return std::nullopt;
		}
		const auto inserted = result.customBadges.emplace(
			*id,
			CustomBadgeData{
				.documentId = *documentId,
				.text = text.toString(),
			}).second;
		if (!inserted) {
			SetError(error, QStringLiteral("Remote config custom badge is duplicated."));
			return std::nullopt;
		}
	}

	static const auto usernamePattern = QRegularExpression(
		QStringLiteral("^@?[A-Za-z0-9_]{5,32}\\z"));
	static const auto amountPattern = QRegularExpression(
		QStringLiteral("^[0-9]{1,9}(?:\\.[0-9]{1,2})?\\z"));
	const auto username = ReadDonationValue(
		object,
		"donateUsername",
		usernamePattern,
		error);
	const auto usd = ReadDonationValue(
		object,
		"donateAmountUsd",
		amountPattern,
		error);
	const auto ton = ReadDonationValue(
		object,
		"donateAmountTon",
		amountPattern,
		error);
	const auto rub = ReadDonationValue(
		object,
		"donateAmountRub",
		amountPattern,
		error);
	if (!username || !usd || !ton || !rub) {
		return std::nullopt;
	}
	result.donateUsername = *username;
	result.donateAmountUsd = *usd;
	result.donateAmountTon = *ton;
	result.donateAmountRub = *rub;

	return result;
}

} // namespace Ayu::RemoteConfig
