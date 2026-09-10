// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#include "ayu/features/stt/whisper_service.h"

#include "ayu/features/stt/audio_decoder.h"
#include "ayu/features/stt/download_helper.h"
#include "base/debug_log.h"
#include "core/application.h"
#include "crl/crl_on_main.h"

#include "whisper.h"
#include "gsl/util"

#include <QtCore/QFileInfo>
#include <QtCore/QThread>

#include <algorithm>
#include <chrono>
#include <limits>
#include <memory>
#include <new>
#include <utility>

namespace Ayu::STT {
namespace {

constexpr int kMaxSegments = 8192;
constexpr int kMaxResultCharacters = 1 * 1024 * 1024;
constexpr int kMaxSegmentCharacters = 256 * 1024;
constexpr int kMaxWorkerThreads = 8;
constexpr int kMaxActiveJobs = 1;
constexpr crl::time kCtxIdleTimeoutMs = 3 * 60000;
[[nodiscard]] bool ValidLanguage(const QString &language) {
	if (language.isEmpty() || language == u"auto"_q) {
		return true;
	}
	if (language.size() != 2) {
		return false;
	}
	for (const auto ch : language) {
		if (ch < u'a' || ch > u'z') {
			return false;
		}
	}
	return true;
}

[[nodiscard]] bool ReserveJob(std::atomic<int> &activeJobs) {
	auto current = activeJobs.load(std::memory_order_relaxed);
	while (current < kMaxActiveJobs) {
		if (activeJobs.compare_exchange_weak(
				current,
				current + 1,
				std::memory_order_acq_rel,
				std::memory_order_relaxed)) {
			return true;
		}
	}
	return false;
}

[[nodiscard]] int WorkerThreadCount() {
	return std::clamp(
		QThread::idealThreadCount() - 1,
		1,
		kMaxWorkerThreads);
}

} // namespace

WhisperService &WhisperService::instance() {
	static WhisperService self;
	return self;
}

WhisperService::WhisperService() {
	_idleTimer.setCallback([this] { freeContext(); });
}

void WhisperService::releaseContext(whisper_context *context) {
	if (!context) {
		return;
	}

	_freeInFlight.store(true, std::memory_order_release);
	QThread *thread = nullptr;
	try {
		thread = QThread::create([context] {
			whisper_free(context);
		});
	} catch (...) {
		whisper_free(context);
		_freeInFlight.store(false, std::memory_order_release);
		if (Core::Quitting()) {
			Core::App().quitPreventFinished();
		}
		return;
	}
	QObject::connect(thread, &QThread::finished, thread, [this, thread] {
		thread->deleteLater();
		_freeInFlight.store(false, std::memory_order_release);
		if (Core::Quitting()) {
			LOG(("WhisperService doesn't prevent quit any more."));
			Core::App().quitPreventFinished();
		}
	});
	thread->start();
}

void WhisperService::scheduleFreeContext() {
	crl::on_main([this] {
		if (!_freeInFlight.load(std::memory_order_acquire)
			&& !_activeJobs.load(std::memory_order_acquire)) {
			_idleTimer.callOnce(kCtxIdleTimeoutMs);
		}
	});
}

void WhisperService::freeContext() {
	if (_freeInFlight.load(std::memory_order_acquire)
		|| _activeJobs.load(std::memory_order_acquire)) {
		scheduleFreeContext();
		return;
	}
	if (!_ctxMutex.tryLock()) {
		scheduleFreeContext();
		return;
	}

	const auto context = _cachedCtx;
	_cachedCtx = nullptr;
	_cachedModelPath.clear();
	_ctxMutex.unlock();
	_idleTimer.cancel();
	if (!context) {
		return;
	}

	releaseContext(context);
}

bool WhisperService::isQuitPrevent() {
	if (_freeInFlight.load(std::memory_order_acquire)
		|| _activeJobs.load(std::memory_order_acquire) > 0) {
		return true;
	}
	if (!_ctxMutex.tryLock()) {
		return true;
	}

	const auto context = _cachedCtx;
	_cachedCtx = nullptr;
	_cachedModelPath.clear();
	_ctxMutex.unlock();
	if (!context) {
		return false;
	}

	_idleTimer.cancel();
	releaseContext(context);
	return true;
}

void WhisperService::transcribeOnDemand(
		const QString &filePath,
		const QString &modelPath,
		const qint64 modelSize,
		const QString &modelSha256,
		const QString &language,
		std::function<void(QString)> callback,
		std::function<bool()> cancelled) {
	std::shared_ptr<std::function<void(QString)>> sharedCallback;
	try {
		sharedCallback = std::make_shared<
			std::function<void(QString)>>(callback);
	} catch (const std::bad_alloc &) {
		InvokeCallbackSafely(callback);
		return;
	}
	const auto complete = [sharedCallback](QString result) {
		crl::on_main([sharedCallback, result = std::move(result)]() mutable {
			if (*sharedCallback) {
				auto callback = std::move(*sharedCallback);
				try {
					callback(std::move(result));
				} catch (...) {
					LOG(("WhisperService: transcription callback failed"));
				}
			}
		});
	};

	if (!ReserveJob(_activeJobs)) {
		complete(QString());
		return;
	}
	const auto jobFinished = [this] {
		const auto previous = _activeJobs.fetch_sub(
			1,
			std::memory_order_acq_rel);
		if (previous == 1) {
			scheduleFreeContext();
			crl::on_main([] {
				if (Core::Quitting()) {
					Core::App().quitPreventFinished();
				}
			});
		}
	};

	if (!ValidLanguage(language) || !QFileInfo(filePath).isFile()) {
		jobFinished();
		complete(QString());
		return;
	}

	QThread *thread = nullptr;
	try {
		thread = QThread::create([
				this,
				filePath,
				modelPath,
				modelSize,
				modelSha256,
				language,
				cancelled,
				complete,
				jobFinished]() mutable {
			const auto finishGuard = gsl::finally(jobFinished);

			try {
				if (cancelled && cancelled()) {
					complete(QString());
					return;
				}
				if (!VerifyModelFile(modelPath, modelSize, modelSha256)) {
					complete(QString());
					return;
				}
				QMutexLocker lock(&_ctxMutex);
					const auto pcm = decodeAudioToPcm(filePath);
				if (pcm.empty()) {
					LOG(("WhisperService: failed to decode audio: %1").arg(
						filePath));
					complete(QString());
					return;
				}
				if (cancelled && cancelled()) {
					complete(QString());
					return;
				}

				if (_cachedModelPath != modelPath || !_cachedCtx) {
					if (_cachedCtx) {
						whisper_free(_cachedCtx);
						_cachedCtx = nullptr;
						_cachedModelPath.clear();
					}
					const auto modelUtf8 = modelPath.toUtf8();
					auto contextParams = whisper_context_default_params();
					contextParams.use_gpu = false;
					_cachedCtx = whisper_init_from_file_with_params(
						modelUtf8.constData(),
						contextParams);
					if (!_cachedCtx) {
						LOG(("WhisperService: failed to load model: %1").arg(
							modelPath));
						complete(QString());
						return;
					}
					_cachedModelPath = modelPath;
				}
				const auto languageUtf8 = language.toUtf8();
				auto params = whisper_full_default_params(
					WHISPER_SAMPLING_GREEDY);
				const auto detectLanguage = language.isEmpty()
					|| language == u"auto"_q;
				params.detect_language = detectLanguage;
				params.language = detectLanguage
					? "auto"
					: languageUtf8.constData();
				params.translate = false;
				params.print_progress = false;
				params.print_realtime = false;
				params.print_timestamps = false;
				params.single_segment = false;
				params.temperature_inc = 0.2f;
				params.no_speech_thold = 1.0f;
				params.n_threads = WorkerThreadCount();
				const auto abortCallback = [] (void *data) {
					const auto callback
						= static_cast<const std::function<bool()>*>(data);
					return callback && *callback && (*callback)();
				};
				params.abort_callback = abortCallback;
				params.abort_callback_user_data = &cancelled;

				if (whisper_full(
						_cachedCtx,
						params,
						pcm.data(),
						static_cast<int>(pcm.size())) != 0) {
					LOG(("WhisperService: whisper_full failed"));
					complete(QString());
					return;
				}

				const auto segmentCount = whisper_full_n_segments(_cachedCtx);
				if (segmentCount < 0 || segmentCount > kMaxSegments) {
					complete(QString());
					return;
				}
				auto result = QString();
				for (auto i = 0; i < segmentCount; ++i) {
					const auto text = whisper_full_get_segment_text(_cachedCtx, i);
					if (!text) {
						continue;
					}
					auto segment = QString::fromUtf8(text).trimmed();
					if (segment.size() > kMaxSegmentCharacters) {
						segment.truncate(kMaxSegmentCharacters);
					}
					if (segment.isEmpty()) {
						continue;
					}
					const auto separator = result.isEmpty() ? 0 : 1;
					if (result.size() > kMaxResultCharacters
						- separator
						- segment.size()) {
						result.clear();
						break;
					}
					if (separator) {
						result += u" "_q;
					}
					result += segment;
				}
				complete(std::move(result));
			} catch (const std::bad_alloc &) {
				LOG(("WhisperService: transcription allocation failed"));
				complete(QString());
			} catch (...) {
				LOG(("WhisperService: transcription failed unexpectedly"));
				complete(QString());
			}
			});
	} catch (...) {
		jobFinished();
		complete(QString());
		return;
	}
	QObject::connect(thread, &QThread::finished, thread, &QObject::deleteLater);
	thread->start();
}

} // namespace Ayu::STT
