// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/ayu_lang.h"

#include "core/application.h"
#include "core/core_settings.h"
#include "lang/lang_instance.h"
#include "storage/localstorage.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonObject>
#include <QRegularExpression>
#include <QSaveFile>
#include <QtNetwork/QNetworkProxy>

#include <gsl/gsl>

#include <optional>
#include <vector>

// hard-coded languages
std::map<QString, QString> langMapping = {
	{"pt-br", "pt"},
	{"zh-hans-beta", "zh-hans"},
	{"zh-hant-beta", "zh-hant"},
	{"zh-hans-raw", "zh-hans"},
	{"zh-hant-raw", "zh-hant"},
};

constexpr auto postfixes = {
	"zero",
	"one",
	"two",
	"few",
	"many",
	"other"
};

namespace {

constexpr auto kMaxLanguageJsonBytes = qint64(4 * 1024 * 1024);
constexpr auto kMaxLanguageKeys = 4096;
constexpr auto kMaxLanguageIdBytes = 64;
constexpr auto kMaxLanguageKeyBytes = 128;
constexpr auto kMaxLanguageValueBytes = 32 * 1024;
constexpr auto kLanguageTransferTimeoutMs = 10000;
constexpr auto kLanguageCacheSchemaVersion = 1;

const auto kLanguageCacheSchemaKey = u"_ayu_schema"_q;
const auto kLanguageCacheLanguageKey = u"_ayu_language"_q;
const auto kLanguageCacheValuesKey = u"_ayu_values"_q;

struct LanguageEntry {
	QByteArray key;
	QByteArray value;
};

[[nodiscard]] QString NormalizeLanguage(QString id) {
	id = id.toLower();
	if (id.toUtf8().size() > kMaxLanguageIdBytes) {
		return QString();
	}
	if (const auto i = langMapping.find(id); i != langMapping.end()) {
		id = i->second;
	}
	static const auto valid = QRegularExpression(u"^[a-z0-9_-]+$"_q);
	return valid.match(id).hasMatch() ? id : QString();
}

[[nodiscard]] bool IsLanguageKey(const QString &key) {
	for (const auto ch : key) {
		if (!((ch >= u'a' && ch <= u'z')
			|| (ch >= u'A' && ch <= u'Z')
			|| (ch >= u'0' && ch <= u'9')
			|| ch == u'_')) {
			return false;
		}
	}
	return true;
}

[[nodiscard]] std::optional<std::vector<LanguageEntry>> NormalizeEntries(
		const QJsonObject &object) {
	if (object.size() > kMaxLanguageKeys) {
		return std::nullopt;
	}

	auto result = std::vector<LanguageEntry>();
	result.reserve(object.size());
	for (auto i = object.begin(); i != object.end(); ++i) {
		const auto brokenKey = i.key();
		const auto keyBytes = brokenKey.toUtf8();
		if (keyBytes.size() > kMaxLanguageKeyBytes
			|| !IsLanguageKey(brokenKey)) {
			return std::nullopt;
		}
		if (!i.value().isString()) {
			return std::nullopt;
		}

		auto key = u"ayu_"_q + brokenKey;
		if (key.endsWith(u"_Android"_q)) {
			continue;
		}
		for (const auto postfix : postfixes) {
			const auto suffix = u"_"_q + QString::fromLatin1(postfix);
			if (key.endsWith(suffix)) {
				key.replace(suffix, u"#"_q + QString::fromLatin1(postfix));
				break;
			}
		}
		if (key.endsWith(u"_PC"_q)) {
			key.chop(3);
		}

		auto value = i.value().toString().replace(u"&amp;"_q, u"&"_q);
		if (value.contains(u"%1$d"_q)
			&& !value.contains(u"%2$d"_q)) {
			value.replace(u"%1$d"_q, u"{count}"_q);
		} else if (value.contains(u"%1$d"_q)
			&& value.contains(u"%2$d"_q)) {
			value.replace(u"%1$d"_q, u"{count1}"_q);
			value.replace(u"%2$d"_q, u"{count2}"_q);
		} else if (value.contains(u"%1$s"_q)
			&& !value.contains(u"%2$s"_q)) {
			value.replace(u"%1$s"_q, u"{item}"_q);
		} else if (value.contains(u"%1$s"_q)
			&& value.contains(u"%2$s"_q)) {
			value.replace(u"%1$s"_q, u"{item1}"_q);
			value.replace(u"%2$s"_q, u"{item2}"_q);
		}

		const auto valueBytes = value.toUtf8();
		if (valueBytes.size() > kMaxLanguageValueBytes) {
			return std::nullopt;
		} else if (!valueBytes.isEmpty()) {
			result.push_back({ key.toUtf8(), valueBytes });
		}
	}
	return result;
}

[[nodiscard]] std::optional<QJsonDocument> ParseLanguageJson(
		const QByteArray &data) {
	if (data.isEmpty() || data.size() > kMaxLanguageJsonBytes) {
		return std::nullopt;
	}
	QJsonParseError error;
	const auto document = QJsonDocument::fromJson(data, &error);
	if (error.error != QJsonParseError::NoError
		|| !document.isObject()
		|| !NormalizeEntries(document.object())) {
		return std::nullopt;
	}
	return document;
}

[[nodiscard]] std::optional<QJsonDocument> ReadCachedLanguage(
		const QByteArray &data,
		const QString &languageId) {
	if (data.isEmpty() || data.size() > kMaxLanguageJsonBytes) {
		return std::nullopt;
	}
	QJsonParseError error;
	const auto document = QJsonDocument::fromJson(data, &error);
	if (error.error != QJsonParseError::NoError || !document.isObject()) {
		return std::nullopt;
	}

	const auto object = document.object();
	if (!object.contains(kLanguageCacheSchemaKey)) {
		return NormalizeEntries(object)
			? std::optional(document)
			: std::nullopt;
	}
	const auto schema = object.value(kLanguageCacheSchemaKey);
	if (!schema.isDouble()
		|| schema.toDouble() != kLanguageCacheSchemaVersion
		|| object.value(kLanguageCacheLanguageKey).toString() != languageId) {
		return std::nullopt;
	}
	const auto values = object.value(kLanguageCacheValuesKey);
	if (!values.isObject() || !NormalizeEntries(values.toObject())) {
		return std::nullopt;
	}
	return QJsonDocument(values.toObject());
}

} // namespace

AyuLanguage *AyuLanguage::instance = nullptr;

AyuLanguage::AyuLanguage() {
	Lang::GetInstance().idChanges() | rpl::on_next([=] {
		syncLanguage();
	}, _lifetime);
	Lang::GetInstance().updated() | rpl::on_next([=] {
		syncLanguage();
	}, _lifetime);
}

void AyuLanguage::init() {
	if (!instance) {
		instance = new AyuLanguage;
	}
	instance->syncLanguage();
}

AyuLanguage *AyuLanguage::currentInstance() {
	return instance;
}

QString AyuLanguage::getCacheDir() const {
	return cWorkingDir() + u"tdata/ayu/languages/"_q;
}

QString AyuLanguage::getCachePath(const QString &langId) const {
	return getCacheDir() + langId + u".json"_q;
}

void AyuLanguage::loadCachedLanguage() {
	for (const auto &id : { _currentLangId, _baseLangId }) {
		if (id.isEmpty() || id == u"en"_q) {
			continue;
		}
		QFile file(getCachePath(id));
		const auto size = QFileInfo(file).size();
		if (size <= 0 || size > kMaxLanguageJsonBytes
			|| !file.open(QIODevice::ReadOnly)) {
			continue;
		}
		const auto data = file.read(kMaxLanguageJsonBytes + 1);
		file.close();
		if (const auto document = ReadCachedLanguage(data, id)) {
			_document = *document;
			LOG(("Loading AyuGram language: %1").arg(id));
			applyLanguageJson(_document);
			return;
		}
		LOG(("Ignoring invalid AyuGram language cache: %1").arg(id));
	}
}

void AyuLanguage::saveCachedLanguage(
		const QJsonDocument &document,
		const QString &langId) {
	if (!NormalizeEntries(document.object())) {
		return;
	}
	const auto object = QJsonObject{
		{ kLanguageCacheSchemaKey, kLanguageCacheSchemaVersion },
		{ kLanguageCacheLanguageKey, langId },
		{ kLanguageCacheValuesKey, document.object() },
	};
	const auto json = QJsonDocument(object).toJson(QJsonDocument::Compact);
	if (json.size() > kMaxLanguageJsonBytes
		|| !QDir().mkpath(getCacheDir())) {
		return;
	}

	QSaveFile file(getCachePath(langId));
	if (file.open(QIODevice::WriteOnly)
		&& file.write(json) == json.size()
		&& file.commit()) {
		LOG(("Cached AyuGram language: %1").arg(langId));
	}
}

void AyuLanguage::syncLanguage() {
	if (_applying) {
		return;
	}
	const auto custom = Lang::GetInstance().isCustom();
	const auto currentId = NormalizeLanguage(Lang::GetInstance().id());
	const auto baseId = NormalizeLanguage(Lang::GetInstance().baseId());
	const auto id = custom
		? QString()
		: !currentId.isEmpty()
		? currentId
		: baseId;
	if (id == _currentLangId && baseId == _baseLangId) {
		if (!custom && !_document.isNull()) {
			applyLanguageJson(_document);
		}
		return;
	}

	++_generation;
	if (_chkReply) {
		const auto reply = _chkReply;
		_chkReply = nullptr;
		reply->abort();
		reply->deleteLater();
	}
	const auto hadOverlay = !_appliedKeys.isEmpty();
	_applying = true;
	clearAppliedLanguage();
	_currentLangId = id;
	_baseLangId = baseId;
	_document = QJsonDocument();
	if (hadOverlay) {
		Lang::GetInstance().notifyUpdated();
	}
	if (id.isEmpty() || id == u"en"_q) {
		_applying = false;
		return;
	}
	_applying = false;
	loadCachedLanguage();
	fetchLanguage(id, _generation);
}

void AyuLanguage::fetchLanguage(
		const QString &id,
		quint64 generation,
		bool mirror) {
	networkManager.setProxy(QNetworkProxy::DefaultProxy);
	if (Core::App().settings().proxy().isEnabled()) {
		const auto proxy = Core::App().settings().proxy().selected();
		if (proxy.type == MTP::ProxyData::Type::Socks5
			|| proxy.type == MTP::ProxyData::Type::Http) {
			networkManager.setProxy(ToNetworkProxy(ToDirectIpProxy(proxy)));
		}
	} else {
		networkManager.setProxy(QNetworkProxy::DefaultProxy);
	}

	const auto url = (mirror
		? u"https://raw.githubusercontent.com/AyuGram/Languages/l10n_main/values/langs/%1/Shared.json"_q
		: u"https://cdn.jsdelivr.net/gh/AyuGram/Languages@l10n_main/values/langs/%1/Shared.json"_q).arg(id);
	auto request = QNetworkRequest(QUrl(url));
	request.setTransferTimeout(kLanguageTransferTimeoutMs);
	const auto reply = networkManager.get(request);
	reply->setReadBufferSize(kMaxLanguageJsonBytes + 1);
	_chkReply = reply;
	connect(reply, &QNetworkReply::finished, this, [=] {
		if (_chkReply != reply || _generation != generation) {
			reply->deleteLater();
			return;
		}
		_chkReply = nullptr;
		const auto data = reply->read(kMaxLanguageJsonBytes + 1);
		const auto document = reply->error() == QNetworkReply::NoError
			? ParseLanguageJson(data)
			: std::optional<QJsonDocument>();
		if (document) {
			_document = *document;
			saveCachedLanguage(*document, id);
			applyLanguageJson(_document);
		} else if (!mirror) {
			fetchLanguage(id, generation, true);
		} else if (!_baseLangId.isEmpty() && id != _baseLangId) {
			fetchLanguage(_baseLangId, generation);
		} else {
			LOG(("AyuGram language unavailable: %1").arg(id));
		}
		reply->deleteLater();
	});
}

void AyuLanguage::clearAppliedLanguage() {
	for (const auto &key : _appliedKeys) {
		Lang::GetInstance().resetValue(key);
	}
	_appliedKeys.clear();
}

void AyuLanguage::applyLanguageJson(const QJsonDocument &document) {
	const auto entries = NormalizeEntries(document.object());
	if (!entries) {
		LOG(("Ignoring invalid AyuGram language document."));
		return;
	}

	const auto wasApplying = _applying;
	_applying = true;
	const auto restoreApplying = gsl::finally([&] {
		_applying = wasApplying;
	});
	clearAppliedLanguage();
	for (const auto &entry : *entries) {
		Lang::GetInstance().resetValue(entry.key);
		Lang::GetInstance().applyValue(entry.key, entry.value);
		_appliedKeys.insert(entry.key);
	}
	Lang::GetInstance().updatePluralRules();
	Lang::GetInstance().notifyUpdated();
}
