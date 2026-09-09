// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/utils/rc_manager.h"

#include "ayu/utils/rc_config.h"
#include "base/unixtime.h"
#include "core/update_keys.h"

#include <QTimer>

namespace {

constexpr auto kPrimaryUrl = "https://update.ayugram.one/rc/current/desktop2";
constexpr auto kExteraUrl = "https://api.exteragram.app/api/v1/profiles/compact";
constexpr auto kFetchTimeout = 15 * 1000;

// Without this, every single cold start re-paid the full kFetchTimeout
// against the primary endpoint before falling back, even for a user whose
// primary endpoint has been unreachable for a while. Persist the last
// failure time and skip straight to the fallback for kFallbackStickyPeriod
// afterwards, so this only gets re-paid periodically rather than on every
// launch -- not forever, in case the primary endpoint recovers.
const auto kFallbackMarkerPath = QString("./tdata/rc_fallback_since");
constexpr auto kFallbackStickyPeriod = 24 * 60 * 60; // 1 day, in seconds

bool ReadFallbackMarker() {
	QFile file(kFallbackMarkerPath);
	if (!file.open(QIODevice::ReadOnly)) {
		return false;
	}
	bool ok = false;
	const auto since = file.readAll().trimmed().toLongLong(&ok);
	if (!ok) {
		return false;
	}
	return (base::unixtime::now() - since) < kFallbackStickyPeriod;
}

void WriteFallbackMarker() {
	QFile file(kFallbackMarkerPath);
	if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
		file.write(QByteArray::number(qint64(base::unixtime::now())));
	}
}

void ClearFallbackMarker() {
	QFile::remove(kFallbackMarkerPath);
}

}

std::unordered_set<ID> default_developers = {
	139303278, 168769611, 668557709, 880708503, 963080346, 1156270028, 1282540315, 1348136086, 1374434073, 1752394339,
	1773117711, 2135966128, 5079320635, 5118627360, 5184725450, 5330087923, 5800413909, 6007644928, 7380551229,
	7738913005, 7818249287, 8083933640, 8512951856
};

std::unordered_set<ID> default_channels = {
	1172503281, 1434550607, 1524581881, 1559501352, 1571726392, 1632728092, 1725670701, 1754537498, 1794457129,
	1815864846, 1877362358, 1905581924, 1947958814, 1976430343, 2130395384, 2331068091, 2401498637, 2562664432,
	2564770112, 2685666919, 3116497667, 3212977677, 3572293253
};

void RCManager::start() {
	DEBUG_LOG(("RCManager: starting"));
	_manager = std::make_unique<QNetworkAccessManager>();

	if (ReadFallbackMarker()) {
		LOG(("RCManager: primary endpoint recently failed, starting on extera fallback endpoint"));
		_useExteraFallback = true;
	}

	makeRequest();

	_timer = new QTimer(this);
	connect(_timer, &QTimer::timeout, this, &RCManager::makeRequest);
	_timer->start(60 * 60 * 1000); // 1 hour
}

void RCManager::makeRequest() {
	_retryAttempted = false;
	sendRequest();
}

void RCManager::sendRequest() {
	if (!_manager) {
		return;
	}

	const auto url = QString::fromLatin1(_useExteraFallback ? kExteraUrl : kPrimaryUrl);
	LOG(("RCManager: requesting map"));

	_response.clear();
	_response.reserve(Ayu::RemoteConfig::kMaxResponseSize + 1);
	_responseTooLarge = false;
	clearSentRequest();

	auto request = QNetworkRequest(QUrl(url));
	request.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::NoLessSafeRedirectPolicy);
	request.setRawHeader("Accept", "application/json");
	request.setTransferTimeout(kFetchTimeout);
	_reply = _manager->get(request);
	_reply->setReadBufferSize(Ayu::RemoteConfig::kMaxResponseSize + 1);
	connect(_reply,
			&QNetworkReply::metaDataChanged,
			this,
			[=]
			{
				checkResponseLength();
			});
	connect(_reply,
			&QNetworkReply::readyRead,
			this,
			[=]
			{
				readResponse();
			});
	connect(_reply,
			&QNetworkReply::finished,
			this,
			[=]
			{
				gotResponse();
			});
	connect(_reply,
			&QNetworkReply::errorOccurred,
			this,
			[=](auto e)
			{
				gotFailure(e);
			});
}

bool RCManager::tryRetryWithExteraFallback() {
	if (_retryAttempted || _useExteraFallback) {
		return false;
	}
	LOG(("RCManager: switching to extera fallback endpoint"));
	_useExteraFallback = true;
	_retryAttempted = true;
	WriteFallbackMarker();
	sendRequest();
	return true;
}

bool RCManager::checkResponseLength() {
	if (!_reply) {
		return false;
	}
	const auto header = _reply->header(QNetworkRequest::ContentLengthHeader);
	if (!header.isValid()) {
		return true;
	}
	bool ok = false;
	const auto length = header.toLongLong(&ok);
	if (ok && length >= 0 && length > Ayu::RemoteConfig::kMaxResponseSize) {
		_responseTooLarge = true;
		_reply->abort();
		return false;
	}
	return true;
}

void RCManager::readResponse() {
	if (!_reply || _responseTooLarge || !checkResponseLength()) {
		return;
	}
	const auto remaining = qint64(
		Ayu::RemoteConfig::kMaxResponseSize + 1 - _response.size());
	if (remaining <= 0) {
		_responseTooLarge = true;
		_reply->abort();
		return;
	}
	_response.append(_reply->read(remaining));
	if (_response.size() > Ayu::RemoteConfig::kMaxResponseSize) {
		_responseTooLarge = true;
		_reply->abort();
	}
}

void RCManager::gotResponse() {
	if (!_reply) {
		return;
	}

	readResponse();
	if (_responseTooLarge) {
		LOG(("RCManager: response exceeds the maximum size"));
		clearSentRequest();
		gotFailure(QNetworkReply::UnknownContentError);
		return;
	}
	const auto response = base::take(_response);
	clearSentRequest();

	if (!handleResponse(response)) {
		LOG(("RCManager: Error applying remote config: %1").arg(response.size()));
		gotFailure(QNetworkReply::UnknownContentError);
	}
}

bool RCManager::handleResponse(const QByteArray &response) {
	try {
		return applyResponse(response);
	} catch (...) {
		LOG(("RCManager: Failed to apply response"));
		return false;
	}
}

bool RCManager::applyResponse(const QByteArray &response) {
	auto error = QString();
	const auto config = Ayu::RemoteConfig::Parse(
		response,
		Core::Updates::RcConfigPublicKeyPem(),
		base::unixtime::now(),
		&error);
	if (!config) {
		LOG(("RCManager: rejected remote config: %1").arg(error));
		return false;
	}

	_developers = config->developers;
	_officialChannels = config->officialChannels;
	_supporters = config->supporters;
	_supporterChannels = config->supporterChannels;
	_customBadges.clear();
	for (const auto &[id, badge] : config->customBadges) {
		_customBadges.emplace(
			id,
			CustomBadge{
				.emojiStatusId = EmojiStatusId(badge.documentId),
				.text = badge.text,
			});
	}
	_donateUsername = config->donateUsername;
	_donateAmountUsd = config->donateAmountUsd;
	_donateAmountTon = config->donateAmountTon;
	_donateAmountRub = config->donateAmountRub;

	initialized = true;

	if (!_useExteraFallback) {
		// A successful primary-endpoint response means it has recovered;
		// stop skipping it on future cold starts.
		ClearFallbackMarker();
	}

	LOG(("RCManager: Loaded %1 developers, %2 official channels"
	).arg(_developers.size()).arg(_officialChannels.size()));

	return true;
}

void RCManager::gotFailure(QNetworkReply::NetworkError e) {
	LOG(("RCManager: Error %1").arg(e));
	_response.clear();
	_responseTooLarge = false;
	if (tryRetryWithExteraFallback()) {
		LOG(("RCManager: retrying request with extera fallback endpoint"));
		return;
	}
	LOG(("RCManager: no retry left for failed request"));
	if (const auto reply = base::take(_reply)) {
		reply->deleteLater();
	}
}

void RCManager::clearSentRequest() {
	const auto reply = base::take(_reply);
	_response.clear();
	_responseTooLarge = false;
	if (!reply) {
		return;
	}
	disconnect(reply, &QNetworkReply::finished, nullptr, nullptr);
	disconnect(reply, &QNetworkReply::errorOccurred, nullptr, nullptr);
	disconnect(reply, &QNetworkReply::metaDataChanged, nullptr, nullptr);
	disconnect(reply, &QNetworkReply::readyRead, nullptr, nullptr);
	reply->abort();
	reply->deleteLater();
}

RCManager::~RCManager() {
	clearSentRequest();
	_manager = nullptr;
}
