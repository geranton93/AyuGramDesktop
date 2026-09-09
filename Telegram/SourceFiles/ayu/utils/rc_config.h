// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include <QtCore/QByteArray>
#include <QtCore/QtGlobal>
#include <QtCore/QString>

#include <optional>
#include <unordered_map>
#include <unordered_set>

namespace Ayu::RemoteConfig {

inline constexpr auto kMaxResponseSize = 256 * 1024;

struct CustomBadgeData {
	qint64 documentId = 0;
	QString text;
};

struct Data {
	std::unordered_set<qint64> developers;
	std::unordered_set<qint64> officialChannels;
	std::unordered_set<qint64> supporters;
	std::unordered_set<qint64> supporterChannels;
	std::unordered_map<qint64, CustomBadgeData> customBadges;
	QString donateUsername = QString("@ayugramOwner");
	QString donateAmountUsd = QString("5.00");
	QString donateAmountTon = QString("3.50");
	QString donateAmountRub = QString("386");
};

[[nodiscard]] std::optional<Data> Parse(
	const QByteArray &response,
	const QByteArray &publicKeyPem,
	qint64 now,
	QString *error = nullptr);

} // namespace Ayu::RemoteConfig
