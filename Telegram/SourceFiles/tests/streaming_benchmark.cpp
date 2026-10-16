/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#include "media/streaming/media_streaming_player.h"
#include "media/streaming/media_streaming_reader.h"

#include "base/concurrent_timer.h"
#include "storage/cache/storage_cache_database.h"
#include "storage/cache/storage_cache_types.h"
#include "storage/storage_encryption.h"
#include "ui/platform/ui_platform_utility.h"

#include <crl/crl_on_main.h>

#include <QtCore/QCoreApplication>
#include <QtCore/QElapsedTimer>
#include <QtCore/QEventLoop>
#include <QtCore/QFile>
#include <QtCore/QHash>
#include <QtCore/QJsonArray>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QMetaObject>
#include <QtCore/QThread>
#include <QtCore/QSet>
#include <QtCore/QTimer>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <sys/resource.h>
#include <vector>

namespace {

using namespace Media::Streaming;

constexpr auto kLoaderKeyHigh = uint64(0x4159554752414DULL);
constexpr auto kLoaderKeyLow = uint64(0x42454E43484D4152ULL);
constexpr auto kTimeoutMs = 120000;
constexpr auto kMinFixtureBytes = 10LL * 1024 * 1024;
constexpr auto kMaxFixtureBytes = 256LL * 1024 * 1024;
constexpr auto kEventLoopProbeIntervalMs = 10;
constexpr auto kPrefetchProbeWindowMs = 6500;
constexpr auto kEarlyStopWindowMs = 2000;
constexpr auto kInitialStatsWaitMs = 5000;
constexpr auto kStatsCheckIntervalMs = 1000;
constexpr auto kReliableNetworkEstimatePartCount = 3;

enum class Profile {
	Cold,
	Warm,
};

enum class RunMode {
	Startup,
	Playback,
	EarlyStop,
	Prime,
	PrefetchProbe,
};

enum class LoaderBehavior {
	NetworkShaped,
	MemorySource,
	CacheOnly,
};

struct NetworkShape {
	int latencyMs = 80;
	int bandwidthBytesPerSecond = 512 * 1024;
	int maxConcurrentRangeRequests = 1;
};

struct LoaderTimelineEvent {
	QString phase;
	int64 offset = 0;
	qint64 atMs = 0;
};

struct VideoWaitInterval {
	qint64 startMs = 0;
	qint64 endMs = -1;
};

struct VideoStateEvent {
	QString phase;
	qint64 atMs = 0;
	crl::time position = Media::kTimeUnknown;
	crl::time receivedTill = Media::kTimeUnknown;
	bool waiting = false;
};

[[nodiscard]] bool NetworkShapeFitsTimeout(NetworkShape shape) {
	if (shape.latencyMs < 0
		|| shape.latencyMs >= kTimeoutMs
		|| shape.bandwidthBytesPerSecond <= 0
		|| shape.maxConcurrentRangeRequests <= 0
		|| shape.maxConcurrentRangeRequests > 64) {
		return false;
	}
	const auto transferMs = (Loader::kPartSize * 1000LL
		+ shape.bandwidthBytesPerSecond - 1)
		/ shape.bandwidthBytesPerSecond;
	return transferMs < kTimeoutMs - shape.latencyMs;
}

struct Result {
	QString error;
	QString profile;
	QString mode;
	QString cacheSemantics;
	QString loaderSemantics;
	qint64 firstReadyMs = -1;
	qint64 firstImageMs = -1;
	qint64 firstVideoProgressMs = -1;
	qint64 firstNetworkRequestMs = -1;
	qint64 speedEstimateMs = -1;
	qint64 speedEstimateCompletedParts = -1;
	qint64 prefetchProbeWindowMs = -1;
	qint64 earlyStopAfterImageMs = -1;
	qint64 networkTimingEarlyCompletions = 0;
	qint64 imagePrepareMs = -1;
	qint64 firstImageBytes = -1;
	qint64 firstImageNetworkBytes = -1;
	qint64 networkBytesReceived = 0;
	qint64 bytesDeliveredToReader = 0;
	qint64 requestedBytes = 0;
	qint64 requestedParts = 0;
	qint64 maxOutstandingRangeRequests = 0;
	qint64 stallCount = 0;
	qint64 stallMs = 0;
	qint64 loopLagMaxMs = 0;
	qint64 loopLagP95Ms = 0;
	qint64 wallMs = 0;
	qint64 peakRssMiB = 0;
	qint64 fixtureBytes = 0;
	qint64 latencyMs = 0;
	qint64 bandwidthBytesPerSecond = 0;
	qint64 maxConcurrentRangeRequests = 0;
	bool cacheFull = false;
	bool earlyStopTriggered = false;
	bool traceTimeline = false;
	std::vector<LoaderTimelineEvent> loaderTimeline;
	std::vector<VideoWaitInterval> videoWaitIntervals;
	std::vector<VideoStateEvent> videoStateTimeline;
};

[[nodiscard]] Storage::EncryptionKey TestCacheEncryptionKey() {
	auto data = bytes::vector(Storage::EncryptionKey::kSize);
	for (auto i = size_type(); i != data.size(); ++i) {
		data[i] = static_cast<bytes::type>(
			(i * 37U + 11U) & 0xFFU);
	}
	return Storage::EncryptionKey(std::move(data));
}

struct CacheOpenResult {
	std::atomic_bool finished = false;
	Storage::Cache::Error error = Storage::Cache::Error::NoError();
};

[[nodiscard]] bool WaitForCacheOperation(
		const std::atomic_bool &finished,
		QString *error,
		QString timeoutMessage) {
	auto loop = QEventLoop();
	auto poll = QTimer();
	poll.setInterval(kEventLoopProbeIntervalMs);
	QObject::connect(&poll, &QTimer::timeout, &loop, [&] {
		if (finished.load(std::memory_order_acquire)) {
			loop.quit();
		}
	});
	QTimer::singleShot(kTimeoutMs, &loop, [&] {
		if (!finished.load(std::memory_order_acquire)) {
			*error = std::move(timeoutMessage);
		}
		loop.quit();
	});
	if (!finished.load(std::memory_order_acquire)) {
		poll.start();
		loop.exec();
		poll.stop();
	}
	return error->isEmpty();
}

[[nodiscard]] bool ProcessMainQueueCallback(int timeoutMs) {
	auto invoked = std::make_shared<std::atomic_bool>(false);
	crl::on_main([invoked] {
		invoked->store(true, std::memory_order_release);
	});
	if (invoked->load(std::memory_order_acquire)) {
		return true;
	}
	auto loop = QEventLoop();
	QTimer::singleShot(timeoutMs, &loop, &QEventLoop::quit);
	loop.exec();
	return invoked->load(std::memory_order_acquire);
}

void QueueMainCallbackOnQt(void (*callable)(void*), void *argument) {
	auto application = QCoreApplication::instance();
	Expects(application != nullptr);
	const auto invoked = QMetaObject::invokeMethod(
		application,
		[callable, argument] { callable(argument); },
		Qt::QueuedConnection);
	Expects(invoked);
}

[[nodiscard]] bool OpenCache(
		not_null<Storage::Cache::Database*> cache,
		QString *error) {
	auto state = std::make_shared<CacheOpenResult>();
	cache->open(TestCacheEncryptionKey(), [state](
				Storage::Cache::Error result) {
		state->error = std::move(result);
		state->finished.store(true, std::memory_order_release);
	});
	if (!WaitForCacheOperation(
			state->finished,
			error,
			u"cache open timed out"_q)) {
		return false;
	}
	if (state->error.type != Storage::Cache::Error::Type::None) {
		*error = u"cache open failed (%1)"_q.arg(int(state->error.type));
	}
	return error->isEmpty();
}

[[nodiscard]] bool CloseCache(
		std::shared_ptr<Storage::Cache::Database> cache,
		QString *error) {
	auto finished = std::make_shared<std::atomic_bool>(false);
	cache->close([cache, finished] {
		cache->waitForCleaner([finished] {
			finished->store(true, std::memory_order_release);
		});
	});
	return WaitForCacheOperation(
		*finished,
		error,
		u"cache close timed out"_q);
}

[[nodiscard]] std::optional<Profile> ParseProfile(
		const QString &value) {
	if (value == u"cold"_q) {
		return Profile::Cold;
	} else if (value == u"warm"_q) {
		return Profile::Warm;
	}
	return std::nullopt;
}

[[nodiscard]] std::optional<RunMode> ParseMode(const QString &value) {
	if (value == u"startup"_q) {
		return RunMode::Startup;
	} else if (value == u"playback"_q) {
		return RunMode::Playback;
	} else if (value == u"early-stop"_q) {
		return RunMode::EarlyStop;
	} else if (value == u"prime"_q) {
		return RunMode::Prime;
	} else if (value == u"prefetch-probe"_q) {
		return RunMode::PrefetchProbe;
	}
	return std::nullopt;
}

[[nodiscard]] QString CacheSemantics(Profile profile) {
	return (profile == Profile::Cold)
		? u"empty_persistent_telegram_cache_db"_q
		: u"reopened_persistent_telegram_cache_db"_q;
}

[[nodiscard]] QString LoaderSemantics(LoaderBehavior behavior) {
	switch (behavior) {
	case LoaderBehavior::NetworkShaped:
		return u"offline_fixture_network_shaped"_q;
	case LoaderBehavior::MemorySource:
		return u"offline_fixture_cache_prime"_q;
	case LoaderBehavior::CacheOnly:
		return u"cache_only_fail_on_miss"_q;
	}
	Unexpected("Unknown benchmark loader behavior.");
}

class ShapedLoader final
: public QObject
, public Loader {
public:
	ShapedLoader(
		QByteArray bytes,
		LoaderBehavior behavior,
		QElapsedTimer *clock,
		NetworkShape networkShape,
		bool traceTimeline)
	: _bytes(std::move(bytes))
	, _behavior(behavior)
	, _clock(clock)
	, _networkShape(networkShape)
	, _traceTimeline(traceTimeline)
	, _timelineStartedAt(std::chrono::steady_clock::now())
	, _networkTimer(this)
	, _statsTimer(this) {
	_networkTimer.setSingleShot(true);
	_networkTimer.setTimerType(Qt::PreciseTimer);
	_statsTimer.setSingleShot(true);
	_statsTimer.setTimerType(Qt::PreciseTimer);
	QObject::connect(
		&_networkTimer,
		&QTimer::timeout,
		this,
		[this] { deliverNetworkRange(); });
	QObject::connect(
		&_statsTimer,
		&QTimer::timeout,
		this,
		[this] {
			if (_completedNetworkParts >= kReliableNetworkEstimatePartCount) {
				publishNetworkEstimate();
			}
		});
	}

	Storage::Cache::Key baseCacheKey() const override {
		return { kLoaderKeyHigh, kLoaderKeyLow };
	}

	int64 size() const override {
		return _bytes.size();
	}

	void load(int64 offset) override {
		recordTimelineEvent(u"request"_q, offset);
		++_requestedParts;
		if (offset >= 0 && offset < size()) {
			_requestedBytes.fetch_add(
				std::min<int64>(Loader::kPartSize, size() - offset),
				std::memory_order_relaxed);
		}
		QMetaObject::invokeMethod(this, [this, offset] {
			if (_stopped.load(std::memory_order_acquire)) {
				return;
			}
			if (_behavior == LoaderBehavior::CacheOnly) {
				_parts.fire({ LoadedPart::kFailedOffset, {} });
			} else {
				schedule(offset);
			}
		}, Qt::QueuedConnection);
	}

	void cancel(int64 offset) override {
		recordTimelineEvent(u"cancel_requested"_q, offset);
		QMetaObject::invokeMethod(this, [this, offset] {
			cancelOnMain(offset);
		}, Qt::QueuedConnection);
	}

	void resetPriorities() override {
	}

	void setPriority(int priority) override {
	}

	void stop() override {
		_stopped.store(true, std::memory_order_release);
		QMetaObject::invokeMethod(this, [this] {
			stopOnMain();
		}, Qt::QueuedConnection);
	}

	void stopAndWait() {
		_stopped.store(true, std::memory_order_release);
		if (QThread::currentThread() == thread()) {
			stopOnMain();
			return;
		}
		const auto invoked = QMetaObject::invokeMethod(
			this,
			[this] { stopOnMain(); },
			Qt::BlockingQueuedConnection);
		Expects(invoked);
	}

	void tryRemoveFromQueue() override {
	}

	rpl::producer<LoadedPart> parts() const override {
		return _parts.events();
	}

	rpl::producer<SpeedEstimate> speedEstimate() const override {
		return _speedEstimate.events();
	}

	void attachDownloader(
			not_null<Storage::StreamedFileDownloader*> downloader) override {
		Unexpected("Downloader attached to the benchmark loader.");
	}

	void clearAttachedDownloader() override {
		Unexpected("Downloader detached from the benchmark loader.");
	}

	[[nodiscard]] qint64 networkBytesReceived() const {
		return _networkBytesReceived.load(std::memory_order_relaxed);
	}

	[[nodiscard]] qint64 networkBytesReceivedAtNow() {
		if (QThread::currentThread() == thread()) {
			accountPartialNetworkBytes();
			return networkBytesReceived();
		}
		auto result = qint64(0);
		const auto invoked = QMetaObject::invokeMethod(
			this,
			[this, &result] {
				accountPartialNetworkBytes();
				result = networkBytesReceived();
			},
			Qt::BlockingQueuedConnection);
		Expects(invoked);
		return result;
	}

	[[nodiscard]] qint64 bytesDeliveredToReader() const {
		return _bytesDeliveredToReader.load(std::memory_order_relaxed);
	}

	[[nodiscard]] qint64 requestedParts() const {
		return _requestedParts.load(std::memory_order_relaxed);
	}

	[[nodiscard]] qint64 maxOutstandingRangeRequests() const {
		return _maxOutstandingRangeRequests;
	}

	[[nodiscard]] qint64 requestedBytes() const {
		return _requestedBytes.load(std::memory_order_relaxed);
	}

	[[nodiscard]] qint64 speedEstimateMs() const {
		return _speedEstimateMs;
	}

	[[nodiscard]] qint64 firstNetworkRequestMs() const {
		return _firstRequestAtMs;
	}

	[[nodiscard]] qint64 speedEstimateCompletedParts() const {
		return _speedEstimateCompletedParts;
	}

	[[nodiscard]] qint64 networkTimingEarlyCompletions() const {
		return _networkTimingEarlyCompletions;
	}

	[[nodiscard]] qint64 latencyMs() const {
		return (_behavior == LoaderBehavior::NetworkShaped)
			? _networkShape.latencyMs
			: 0;
	}

	[[nodiscard]] qint64 bandwidthBytesPerSecond() const {
		return (_behavior == LoaderBehavior::NetworkShaped)
			? _networkShape.bandwidthBytesPerSecond
			: 0;
	}

	[[nodiscard]] qint64 maxConcurrentRangeRequests() const {
		return (_behavior == LoaderBehavior::NetworkShaped)
			? _networkShape.maxConcurrentRangeRequests
			: 0;
	}

	[[nodiscard]] qint64 traceNowMs() const {
		return std::chrono::duration_cast<std::chrono::milliseconds>(
			std::chrono::steady_clock::now() - _timelineStartedAt
		).count();
	}

	[[nodiscard]] std::vector<LoaderTimelineEvent> timeline() const {
		const auto guard = std::lock_guard(_timelineMutex);
		return _timeline;
	}

private:
	void recordTimelineEvent(QString phase, int64 offset) {
		if (!_traceTimeline) {
			return;
		}
		const auto event = LoaderTimelineEvent{
			std::move(phase),
			offset,
			traceNowMs(),
		};
		const auto guard = std::lock_guard(_timelineMutex);
		_timeline.push_back(event);
	}

	void schedule(int64 offset) {
		if (_stopped.load(std::memory_order_acquire)
			|| offset < 0
			|| offset >= size()
			|| _pending.contains(offset)) {
			return;
		}
		_pending.insert(offset);
		recordTimelineEvent(u"scheduled"_q, offset);
		_maxOutstandingRangeRequests = std::max<qint64>(
			_maxOutstandingRangeRequests,
			_pending.size());
		const auto bytes = _bytes.mid(
			offset,
			int(Loader::kPartSize));
		if (_behavior == LoaderBehavior::NetworkShaped) {
			if (_firstRequestAtMs < 0) {
				_firstRequestAtMs = _clock->elapsed();
			}
			_pendingBytes.insert(offset, bytes);
			_networkQueue.push_back(offset);
			scheduleNetworkQueue();
		} else {
			scheduleLocalDelivery(offset, bytes);
		}
	}

	void scheduleLocalDelivery(int64 offset, QByteArray bytes) {
		const auto generation = ++_generation[offset];
		QTimer::singleShot(0, this, [
				this,
				offset,
				bytes = std::move(bytes),
				generation] {
			if (_stopped.load(std::memory_order_acquire)
				|| _generation.value(offset) != generation
				|| !_pending.remove(offset)) {
				return;
			}
			_bytesDeliveredToReader.fetch_add(
				bytes.size(),
				std::memory_order_relaxed);
			recordTimelineEvent(u"range_delivered"_q, offset);
			_parts.fire({ offset, bytes });
		});
	}

	void scheduleNetworkQueue() {
		if (_stopped.load(std::memory_order_acquire)) {
			return;
		}
		while (!_networkQueue.empty()
			&& _inFlightNetworkRequests
				< _networkShape.maxConcurrentRangeRequests) {
			const auto offset = _networkQueue.front();
			_networkQueue.erase(_networkQueue.begin());
			if (!_pending.contains(offset)
				|| !_pendingBytes.contains(offset)) {
				continue;
			}
			++_inFlightNetworkRequests;
			recordTimelineEvent(u"latency_wait_started"_q, offset);
			_waitingForLatency.insert(offset);
			const auto generation = _generation.value(offset);
			const auto latencyStartedAt = std::chrono::steady_clock::now();
			QTimer::singleShot(latencyMs(), Qt::PreciseTimer, this, [
					this,
					offset,
					generation,
					latencyStartedAt] {
				if (_stopped.load(std::memory_order_acquire)
					|| _generation.value(offset) != generation
					|| !_pending.contains(offset)
					|| !_waitingForLatency.remove(offset)) {
					return;
				}
				if (std::chrono::steady_clock::now() - latencyStartedAt
					< std::chrono::milliseconds(latencyMs())) {
					++_networkTimingEarlyCompletions;
				}
				recordTimelineEvent(u"latency_elapsed"_q, offset);
				_readyForTransfer.push_back(offset);
				startNextNetworkRange();
			});
		}
	}

	void startNextNetworkRange() {
		if (_stopped.load(std::memory_order_acquire)
			|| _activeNetworkOffset) {
			return;
		}
		while (!_readyForTransfer.empty()) {
			const auto offset = _readyForTransfer.front();
			_readyForTransfer.erase(_readyForTransfer.begin());
			if (!_pending.contains(offset)
				|| !_pendingBytes.contains(offset)) {
				continue;
			}
			const auto bytes = _pendingBytes.constFind(offset);
			const auto bandwidth = bandwidthBytesPerSecond();
			const auto transferMs = std::max<qint64>(
				1,
				(bytes->size() * 1000LL + bandwidth - 1) / bandwidth);
			_activeNetworkOffset = offset;
			_activeNetworkBytesAccounted = 0;
			_activeTransferStartsAt = _clock->elapsed();
			_activeTransferStartedAtSteady = std::chrono::steady_clock::now();
			_activeTransferDurationMs = transferMs;
			recordTimelineEvent(u"transfer_started"_q, offset);
			_networkTimer.start(int(std::clamp<qint64>(
				transferMs,
				1,
				kTimeoutMs)));
			return;
		}
	}

	void deliverNetworkRange() {
		if (_stopped.load(std::memory_order_acquire)
			|| !_activeNetworkOffset) {
			return;
		}
		const auto offset = *_activeNetworkOffset;
		if (!_pending.remove(offset)) {
			return;
		}
		if (std::chrono::steady_clock::now() - _activeTransferStartedAtSteady
			< std::chrono::milliseconds(_activeTransferDurationMs)) {
			++_networkTimingEarlyCompletions;
		}
		const auto bytes = _pendingBytes.take(offset);
		_networkBytesReceived.fetch_add(
			std::max<qint64>(
				0,
				bytes.size() - _activeNetworkBytesAccounted),
			std::memory_order_relaxed);
		_activeNetworkOffset.reset();
		_activeTransferStartsAt = -1;
		_activeTransferStartedAtSteady = {};
		_activeTransferDurationMs = 0;
		_activeNetworkBytesAccounted = 0;
		--_inFlightNetworkRequests;
		_bytesDeliveredToReader.fetch_add(
			bytes.size(),
			std::memory_order_relaxed);
		recordTimelineEvent(u"range_delivered"_q, offset);
		++_completedNetworkParts;
		scheduleNetworkEstimateCheck();
		_parts.fire({ offset, bytes });
		startNextNetworkRange();
		scheduleNetworkQueue();
	}

	void accountPartialNetworkBytes() {
		if (!_activeNetworkOffset || _activeTransferStartsAt < 0) {
			return;
		}
		const auto now = _clock->elapsed();
		const auto elapsed = std::max<qint64>(
			0,
			now - _activeTransferStartsAt);
		const auto bandwidth = bandwidthBytesPerSecond();
		const auto bytes = _pendingBytes.value(
			*_activeNetworkOffset);
		const auto received = std::min<qint64>(
			bytes.size(),
			elapsed * bandwidth / 1000);
		const auto delta = received - _activeNetworkBytesAccounted;
		if (delta > 0) {
			_networkBytesReceived.fetch_add(
				delta,
				std::memory_order_relaxed);
			_activeNetworkBytesAccounted = received;
		}
	}

	void publishNetworkEstimate() {
		if (_speedEstimateSent
			|| _completedNetworkParts < kReliableNetworkEstimatePartCount) {
			return;
		}
		_speedEstimateSent = true;
		_speedEstimateMs = _clock->elapsed();
		_speedEstimateCompletedParts = _completedNetworkParts;
		_speedEstimate.fire({
			.bytesPerSecond = int(bandwidthBytesPerSecond()),
			.unreliable = false,
			.latencyMs = int(latencyMs()),
		});
	}

	void scheduleNetworkEstimateCheck() {
		if (_speedEstimateSent || _statsTimer.isActive()) {
			return;
		}
		const auto untilInitialCheck = _firstRequestAtMs
			+ kInitialStatsWaitMs
			- _clock->elapsed();
		_statsTimer.start(int(std::max<qint64>(
			kStatsCheckIntervalMs,
			untilInitialCheck)));
	}

	void stopOnMain() {
		accountPartialNetworkBytes();
		_networkTimer.stop();
		_statsTimer.stop();
		_activeNetworkOffset.reset();
		_activeTransferStartsAt = -1;
		_activeTransferStartedAtSteady = {};
		_activeTransferDurationMs = 0;
		_activeNetworkBytesAccounted = 0;
		_inFlightNetworkRequests = 0;
		_waitingForLatency.clear();
		_readyForTransfer.clear();
		for (const auto offset : _pending) {
			++_generation[offset];
		}
		_pending.clear();
		_pendingBytes.clear();
		_networkQueue.clear();
	}

	void cancelOnMain(int64 offset) {
		if (_stopped.load(std::memory_order_acquire)) {
			return;
		}
		++_generation[offset];
		if (!_pending.remove(offset)) {
			return;
		}
		recordTimelineEvent(u"range_cancelled"_q, offset);
		auto releasedRequest = false;
		if (_waitingForLatency.remove(offset)) {
			releasedRequest = true;
		}
		if (std::erase(_readyForTransfer, offset)) {
			releasedRequest = true;
		}
		if (_activeNetworkOffset == offset) {
			accountPartialNetworkBytes();
			_networkTimer.stop();
			_activeNetworkOffset.reset();
			_activeTransferStartsAt = -1;
			_activeTransferStartedAtSteady = {};
			_activeTransferDurationMs = 0;
			_activeNetworkBytesAccounted = 0;
			releasedRequest = true;
		}
		if (releasedRequest) {
			--_inFlightNetworkRequests;
		}
		std::erase(_networkQueue, offset);
		_pendingBytes.remove(offset);
		if (_behavior == LoaderBehavior::NetworkShaped) {
			startNextNetworkRange();
			scheduleNetworkQueue();
		}
	}

	const QByteArray _bytes;
	const LoaderBehavior _behavior;
	QElapsedTimer *_clock = nullptr;
	const NetworkShape _networkShape;
	const bool _traceTimeline = false;
	const std::chrono::steady_clock::time_point _timelineStartedAt;
	QTimer _networkTimer;
	QTimer _statsTimer;
	rpl::event_stream<LoadedPart> _parts;
	rpl::event_stream<SpeedEstimate> _speedEstimate;
	QSet<int64> _pending;
	QHash<int64, uint64> _generation;
	QHash<int64, QByteArray> _pendingBytes;
	std::vector<int64> _networkQueue;
	QSet<int64> _waitingForLatency;
	std::vector<int64> _readyForTransfer;
	int _inFlightNetworkRequests = 0;
	std::optional<int64> _activeNetworkOffset;
	qint64 _activeTransferStartsAt = -1;
	std::chrono::steady_clock::time_point _activeTransferStartedAtSteady;
	qint64 _activeTransferDurationMs = 0;
	qint64 _activeNetworkBytesAccounted = 0;
	bool _speedEstimateSent = false;
	qint64 _firstRequestAtMs = -1;
	qint64 _completedNetworkParts = 0;
	qint64 _speedEstimateMs = -1;
	qint64 _speedEstimateCompletedParts = -1;
	qint64 _networkTimingEarlyCompletions = 0;
	std::atomic_bool _stopped = false;
	std::atomic<qint64> _networkBytesReceived = 0;
	std::atomic<qint64> _bytesDeliveredToReader = 0;
	std::atomic<qint64> _requestedBytes = 0;
	std::atomic<qint64> _requestedParts = 0;
	qint64 _maxOutstandingRangeRequests = 0;
	mutable std::mutex _timelineMutex;
	std::vector<LoaderTimelineEvent> _timeline;

};

[[nodiscard]] QJsonValue NumberOrNull(qint64 value) {
	return (value < 0) ? QJsonValue(QJsonValue::Null) : QJsonValue(value);
}

[[nodiscard]] qint64 PeakRssMiB() {
	auto usage = rusage();
	if (getrusage(RUSAGE_SELF, &usage)) {
		return -1;
	}
	return qint64(usage.ru_maxrss / (1024 * 1024));
}

[[nodiscard]] Result Run(
		QByteArray fixture,
		Profile profile,
		RunMode mode,
		LoaderBehavior behavior,
		NetworkShape networkShape,
		bool traceTimeline,
		not_null<Storage::Cache::Database*> cache) {
	auto result = Result();
	result.profile = (profile == Profile::Cold)
		? u"cold"_q
		: u"warm"_q;
	switch (mode) {
	case RunMode::Startup: result.mode = u"startup"_q; break;
	case RunMode::Playback: result.mode = u"playback"_q; break;
	case RunMode::EarlyStop: result.mode = u"early-stop"_q; break;
	case RunMode::Prime: result.mode = u"prime"_q; break;
	case RunMode::PrefetchProbe: result.mode = u"prefetch-probe"_q; break;
	}
	result.cacheSemantics = CacheSemantics(profile);
	result.loaderSemantics = LoaderSemantics(behavior);
	result.fixtureBytes = fixture.size();
	result.traceTimeline = traceTimeline;

	auto clock = QElapsedTimer();
	clock.start();
	auto loader = std::make_unique<ShapedLoader>(
		std::move(fixture),
		behavior,
		&clock,
		networkShape,
		traceTimeline);
	auto loaderView = loader.get();
	auto reader = std::make_shared<Reader>(
		std::move(loader),
		cache);
	auto player = Player(reader);
	auto lifetime = rpl::lifetime();
	auto loop = QEventLoop();
	auto stopTimer = QTimer();
	stopTimer.setSingleShot(true);
	stopTimer.setTimerType(Qt::PreciseTimer);
	auto prefetchProbeStartedAtMs = qint64(-1);
	auto waiting = false;
	auto waitingStartedAt = qint64(0);
	auto videoPosition = Media::kTimeUnknown;
	auto videoReceivedTill = Media::kTimeUnknown;
	auto stopScheduled = false;
	auto completed = false;
	auto finishedPlayback = false;
	const auto recordVideoState = [&](QString phase) {
		if (!traceTimeline) {
			return;
		}
		result.videoStateTimeline.push_back({
			std::move(phase),
			loaderView->traceNowMs(),
			videoPosition,
			videoReceivedTill,
			waiting,
		});
	};
	const auto closeWaitingInterval = [&](qint64 now) {
		if (!waiting) {
			return;
		}
		result.stallMs += now - waitingStartedAt;
		if (traceTimeline) {
			Expects(!result.videoWaitIntervals.empty());
			result.videoWaitIntervals.back().endMs = loaderView->traceNowMs();
		}
		waiting = false;
	};
	QObject::connect(&stopTimer, &QTimer::timeout, &loop, [&] {
		const auto stoppedAtMs = clock.elapsed();
		if (mode == RunMode::PrefetchProbe
			&& prefetchProbeStartedAtMs >= 0) {
			result.prefetchProbeWindowMs =
				stoppedAtMs - prefetchProbeStartedAtMs;
		}
		if (mode == RunMode::EarlyStop && result.firstImageMs >= 0) {
			result.earlyStopAfterImageMs =
				stoppedAtMs - result.firstImageMs;
			result.earlyStopTriggered = true;
		}
		closeWaitingInterval(stoppedAtMs);
		player.stop();
		completed = true;
		loop.quit();
	});

	const auto stopAtBoundary = [&] {
		if ((mode != RunMode::Startup
			&& mode != RunMode::PrefetchProbe
			&& mode != RunMode::EarlyStop)
			|| stopScheduled) {
			return;
		}
		stopScheduled = true;
		const auto delay = (mode == RunMode::Startup)
			? 0
			: (mode == RunMode::EarlyStop)
			? kEarlyStopWindowMs
			: kPrefetchProbeWindowMs;
		if (mode == RunMode::PrefetchProbe) {
			prefetchProbeStartedAtMs = clock.elapsed();
		}
		stopTimer.start(delay);
	};

	player.updates() | rpl::on_next_error(
		[&](Update &&update) {
			if (const auto info = std::get_if<Information>(&update.data)) {
				videoPosition = info->video.state.position;
				videoReceivedTill = info->video.state.receivedTill;
				recordVideoState(u"information"_q);
				if (result.firstReadyMs < 0 && info->video.state.duration > 0) {
					result.firstReadyMs = clock.elapsed();
					auto request = FrameRequest::NonStrict();
					request.resize = QSize(640, 360);
					request.outer = request.resize;
					const auto started = clock.elapsed();
					const auto image = player.frame(request);
					result.imagePrepareMs = clock.elapsed() - started;
					if (!image.isNull()) {
						result.firstImageMs = clock.elapsed();
						result.firstImageBytes =
							loaderView->bytesDeliveredToReader();
						result.firstImageNetworkBytes =
							loaderView->networkBytesReceivedAtNow();
					} else {
						result.error = u"decoded image is null"_q;
					}
					stopAtBoundary();
				}
			} else if (const auto received = std::get_if<PreloadedVideo>(
						&update.data)) {
				videoReceivedTill = received->till;
				recordVideoState(u"received_till"_q);
			} else if (const auto played = std::get_if<UpdateVideo>(
						&update.data)) {
				videoPosition = played->position;
				if (result.firstVideoProgressMs < 0
					&& played->position > 0) {
					result.firstVideoProgressMs = clock.elapsed();
				}
				recordVideoState(u"position"_q);
			} else if (const auto state = std::get_if<WaitingForData>(
					&update.data)) {
				const auto now = clock.elapsed();
				if (state->waiting && !waiting) {
					waiting = true;
					waitingStartedAt = now;
					++result.stallCount;
					if (traceTimeline) {
						result.videoWaitIntervals.push_back({
							loaderView->traceNowMs(),
							-1,
						});
					}
				} else if (!state->waiting && waiting) {
					closeWaitingInterval(now);
				}
				recordVideoState(u"waiting"_q);
			} else if (std::holds_alternative<Finished>(update.data)) {
				finishedPlayback = true;
				closeWaitingInterval(clock.elapsed());
				completed = true;
				QTimer::singleShot(0, &loop, &QEventLoop::quit);
			}
		},
		[&](Error &&error) {
			result.error = u"streaming error %1"_q.arg(int(error));
			completed = true;
			QTimer::singleShot(0, &loop, &QEventLoop::quit);
		},
		lifetime);

	auto loopLagSamples = std::vector<qint64>();
	auto probe = QTimer();
	probe.setInterval(kEventLoopProbeIntervalMs);
	auto nextProbeAt = qint64(kEventLoopProbeIntervalMs);
	QObject::connect(&probe, &QTimer::timeout, &loop, [&] {
		const auto now = clock.elapsed();
		const auto lag = std::max<qint64>(0, now - nextProbeAt);
		loopLagSamples.push_back(lag);
		result.loopLagMaxMs = std::max(result.loopLagMaxMs, lag);
		nextProbeAt = now + kEventLoopProbeIntervalMs;
		if (now >= kTimeoutMs && !completed) {
			result.error = u"benchmark timed out"_q;
			completed = true;
			loop.quit();
		}
	});
	probe.start();

	auto options = PlaybackOptions();
	options.mode = Mode::Video;
	options.speed = 1.;
	options.hwAllowed = false;
	options.syncVideoByAudio = false;
	player.play(options);
	loop.exec();

	probe.stop();
	lifetime.destroy();
	player.stop();
	loaderView->stopAndWait();
	result.cacheFull = reader->fullInCache();
	closeWaitingInterval(clock.elapsed());
	if (traceTimeline) {
		result.loaderTimeline = loaderView->timeline();
	}
	if (!loopLagSamples.empty()) {
		std::ranges::sort(loopLagSamples);
		const auto index = size_t(std::ceil(
			0.95 * loopLagSamples.size())) - 1;
		result.loopLagP95Ms = loopLagSamples[index];
	}
	result.wallMs = clock.elapsed();
	result.networkBytesReceived = loaderView->networkBytesReceived();
	result.bytesDeliveredToReader =
		loaderView->bytesDeliveredToReader();
	result.requestedBytes = loaderView->requestedBytes();
	result.requestedParts = loaderView->requestedParts();
	result.maxOutstandingRangeRequests =
		loaderView->maxOutstandingRangeRequests();
	result.firstNetworkRequestMs = loaderView->firstNetworkRequestMs();
	result.speedEstimateMs = loaderView->speedEstimateMs();
	result.speedEstimateCompletedParts =
		loaderView->speedEstimateCompletedParts();
	result.networkTimingEarlyCompletions =
		loaderView->networkTimingEarlyCompletions();
	result.latencyMs = loaderView->latencyMs();
	result.bandwidthBytesPerSecond = loaderView->bandwidthBytesPerSecond();
	result.maxConcurrentRangeRequests =
		loaderView->maxConcurrentRangeRequests();
	result.peakRssMiB = PeakRssMiB();
	if (result.error.isEmpty() && result.firstImageMs < 0) {
		result.error = u"no decoded image was produced"_q;
	} else if (result.error.isEmpty()
		&& (mode == RunMode::Playback || mode == RunMode::Prime)
		&& !finishedPlayback) {
		result.error = u"playback did not finish"_q;
	} else if (result.error.isEmpty()
		&& mode == RunMode::EarlyStop
		&& !result.earlyStopTriggered) {
		result.error = u"early-stop timer did not stop playback"_q;
	}
	return result;
}

[[nodiscard]] QJsonObject ToJson(const Result &result) {
	auto object = QJsonObject();
	object.insert(u"profile"_q, result.profile);
	object.insert(u"mode"_q, result.mode);
	object.insert(u"cache_semantics"_q, result.cacheSemantics);
	object.insert(u"loader_semantics"_q, result.loaderSemantics);
	object.insert(u"fixture_bytes"_q, result.fixtureBytes);
	object.insert(u"network_latency_ms"_q, result.latencyMs);
	object.insert(
		u"network_bandwidth_bytes_per_second"_q,
		result.bandwidthBytesPerSecond);
	object.insert(
		u"network_max_concurrent_range_requests"_q,
		result.maxConcurrentRangeRequests);
	object.insert(u"first_ready_ms"_q, NumberOrNull(result.firstReadyMs));
	object.insert(
		u"first_decoded_image_ms"_q,
		NumberOrNull(result.firstImageMs));
	object.insert(
		u"first_video_progress_ms"_q,
		NumberOrNull(result.firstVideoProgressMs));
	object.insert(
		u"first_network_request_ms"_q,
		NumberOrNull(result.firstNetworkRequestMs));
	object.insert(
		u"speed_estimate_ms"_q,
		NumberOrNull(result.speedEstimateMs));
	object.insert(
		u"speed_estimate_completed_parts"_q,
		NumberOrNull(result.speedEstimateCompletedParts));
	object.insert(
		u"prefetch_probe_window_ms"_q,
		NumberOrNull(result.prefetchProbeWindowMs));
	object.insert(
		u"early_stop_after_image_ms"_q,
		NumberOrNull(result.earlyStopAfterImageMs));
	object.insert(
		u"early_stop_triggered"_q,
		result.earlyStopTriggered);
	object.insert(
		u"network_timing_early_completions"_q,
		result.networkTimingEarlyCompletions);
	object.insert(
		u"image_prepare_ms"_q,
		NumberOrNull(result.imagePrepareMs));
	object.insert(
		u"bytes_delivered_at_first_image"_q,
		NumberOrNull(result.firstImageBytes));
	object.insert(
		u"network_bytes_at_first_image"_q,
		NumberOrNull(result.firstImageNetworkBytes));
	object.insert(u"network_bytes_received"_q, result.networkBytesReceived);
	object.insert(
		u"bytes_delivered_to_reader"_q,
		result.bytesDeliveredToReader);
	object.insert(u"bytes_requested"_q, result.requestedBytes);
	object.insert(u"range_requests"_q, result.requestedParts);
	object.insert(
		u"max_outstanding_range_requests"_q,
		result.maxOutstandingRangeRequests);
	object.insert(u"cache_full"_q, result.cacheFull);
	object.insert(u"video_wait_count"_q, result.stallCount);
	object.insert(u"video_wait_ms"_q, result.stallMs);
	object.insert(u"event_loop_lag_p95_ms"_q, result.loopLagP95Ms);
	object.insert(u"event_loop_lag_max_ms"_q, result.loopLagMaxMs);
	object.insert(u"wall_ms"_q, result.wallMs);
	object.insert(u"peak_rss_mib"_q, result.peakRssMiB);
	object.insert(u"error"_q, result.error);
	if (result.traceTimeline) {
		auto events = result.loaderTimeline;
		std::stable_sort(
			events.begin(),
			events.end(),
			[](const auto &a, const auto &b) {
				return a.atMs < b.atMs;
			});
		auto rangeTimeline = QJsonArray();
		for (const auto &event : events) {
			auto value = QJsonObject();
			value.insert(u"phase"_q, event.phase);
			value.insert(u"offset"_q, event.offset);
			value.insert(u"at_ms"_q, event.atMs);
			rangeTimeline.push_back(value);
		}
		object.insert(u"range_timeline"_q, rangeTimeline);

		auto videoWaitIntervals = QJsonArray();
		for (const auto &interval : result.videoWaitIntervals) {
			auto value = QJsonObject();
			value.insert(u"start_ms"_q, interval.startMs);
			value.insert(u"end_ms"_q, interval.endMs);
			value.insert(
				u"duration_ms"_q,
				interval.endMs - interval.startMs);
			videoWaitIntervals.push_back(value);
		}
		object.insert(u"video_wait_intervals"_q, videoWaitIntervals);

		auto videoStateTimeline = QJsonArray();
		for (const auto &event : result.videoStateTimeline) {
			const auto position = (event.position == Media::kTimeUnknown)
				? QJsonValue(QJsonValue::Null)
				: QJsonValue(qint64(event.position));
			const auto receivedTill = (
				event.receivedTill == Media::kTimeUnknown)
				? QJsonValue(QJsonValue::Null)
				: QJsonValue(qint64(event.receivedTill));
			const auto buffer = (event.position == Media::kTimeUnknown
				|| event.receivedTill == Media::kTimeUnknown)
				? QJsonValue(QJsonValue::Null)
				: QJsonValue(qint64(event.receivedTill - event.position));
			auto value = QJsonObject();
			value.insert(u"phase"_q, event.phase);
			value.insert(u"at_ms"_q, event.atMs);
			value.insert(u"position_ms"_q, position);
			value.insert(u"received_till_ms"_q, receivedTill);
			value.insert(u"buffer_ms"_q, buffer);
			value.insert(u"waiting"_q, event.waiting);
			videoStateTimeline.push_back(value);
		}
		object.insert(u"video_state_timeline"_q, videoStateTimeline);
	}
	return object;
}

} // namespace

int main(int argc, char *argv[]) {
	auto app = QCoreApplication(argc, argv);
	crl::wrap_main_queue(&QueueMainCallbackOnQt);
	auto mainQueueLifetime = rpl::lifetime();
	crl::on_main_update_requests() | rpl::on_next([] {
		Ui::Platform::DrainMainQueue();
	}, mainQueueLifetime);
	auto timerEnvironment = base::ConcurrentTimerEnvironment();
	if (!ProcessMainQueueCallback(250)) {
		std::cerr << "Qt event loop did not service crl::on_main callback\n";
		return 3;
	}
	if (argc != 4 && argc != 6 && argc != 7) {
		std::cerr << "usage: test_media_streaming_benchmark <cold|warm> "
			"<startup|playback|prime|prefetch-probe> <cache-path> "
			"[<latency-ms> <bandwidth-bytes-per-second> "
			"[<max-concurrent-range-requests>]]\n";
		return 2;
	}
	const auto profile = ParseProfile(QString::fromLocal8Bit(argv[1]));
	const auto mode = ParseMode(QString::fromLocal8Bit(argv[2]));
	const auto traceTimeline = (
		std::getenv("AYU_STREAMING_BENCH_TRACE") != nullptr);
	if (!profile || !mode || (*mode == RunMode::Prime
		&& *profile != Profile::Warm)) {
		std::cerr << "invalid benchmark profile or mode\n";
		return 2;
	}
	auto networkShape = NetworkShape();
	if (argc >= 6) {
		auto latencyOk = false;
		const auto latency = QString::fromLocal8Bit(argv[4]).toInt(
			&latencyOk);
		auto bandwidthOk = false;
		const auto bandwidth = QString::fromLocal8Bit(argv[5]).toInt(
			&bandwidthOk);
		if (!latencyOk || latency < 0 || !bandwidthOk || bandwidth <= 0) {
			std::cerr << "invalid benchmark network shape\n";
			return 2;
		}
		auto maxConcurrentRangeRequests = 1;
		if (argc == 7) {
			auto requestsOk = false;
			maxConcurrentRangeRequests = QString::fromLocal8Bit(argv[6]).toInt(
				&requestsOk);
			if (!requestsOk) {
				std::cerr << "invalid concurrent range request count\n";
				return 2;
			}
		}
		networkShape = {
			latency,
			bandwidth,
			maxConcurrentRangeRequests,
		};
	}
	if (!NetworkShapeFitsTimeout(networkShape)) {
		std::cerr << "network shape exceeds the benchmark timeout budget\n";
		return 2;
	}

	auto fixturePath = qEnvironmentVariable(
		"AYU_STREAMING_BENCHMARK_FIXTURE");
	if (fixturePath.isEmpty()) {
		fixturePath = QString::fromUtf8(
			TDESKTOP_STREAMING_BENCHMARK_FIXTURE);
	}
	auto file = QFile(fixturePath);
	if (!file.open(QIODevice::ReadOnly)) {
		std::cerr << "could not open offline media fixture\n";
		return 2;
	}
	const auto fixtureSize = file.size();
	if (fixtureSize <= kMinFixtureBytes) {
		std::cerr << "fixture must exceed the Reader in-memory header limit\n";
		return 2;
	}
	if (fixtureSize > kMaxFixtureBytes) {
		std::cerr << "offline media fixture exceeds benchmark limit of 256 MiB\n";
		return 2;
	}
	const auto fixture = file.readAll();
	if (fixture.size() != fixtureSize) {
		std::cerr << "could not read complete offline media fixture\n";
		return 2;
	}

	auto cache = std::make_shared<Storage::Cache::Database>(
		QString::fromUtf8(argv[3]),
		Storage::Cache::Database::Settings());
	auto cacheError = QString();
	if (!OpenCache(cache.get(), &cacheError)) {
		std::cerr << cacheError.toStdString() << '\n';
		return 1;
	}
	auto behavior = LoaderBehavior::NetworkShaped;
	if (*mode == RunMode::Prime) {
		behavior = LoaderBehavior::MemorySource;
	} else if (*profile == Profile::Warm) {
		behavior = LoaderBehavior::CacheOnly;
	}
	auto result = Run(
		fixture,
		*profile,
		*mode,
		behavior,
		networkShape,
		traceTimeline,
		cache.get());
	if (*mode == RunMode::Prime && !result.cacheFull && result.error.isEmpty()) {
		result.error = u"cache prime did not read the complete fixture"_q;
	}
	if (!CloseCache(cache, &cacheError) && result.error.isEmpty()) {
		result.error = cacheError;
	}
	std::cout << QJsonDocument(ToJson(result)).toJson(
		QJsonDocument::Compact
	).toStdString() << '\n';
	std::cout.flush();
	std::_Exit(result.error.isEmpty() ? EXIT_SUCCESS : EXIT_FAILURE);
}
