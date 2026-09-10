// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#include "ayu/features/stt/whisper_service.h"

#include "base/debug_log.h"
#include "core/application.h"
#include "crl/crl_on_main.h"

#include "whisper.h"
#include "gsl/util"

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libswresample/swresample.h>
}

#include <QtCore/QCryptographicHash>
#include <QtCore/QFile>
#include <QtCore/QFileInfo>
#include <QtCore/QThread>

#include <algorithm>
#include <chrono>
#include <climits>
#include <cstdint>
#include <limits>
#include <memory>
#include <new>
#include <utility>

namespace Ayu::STT {
namespace {

constexpr int kTargetSampleRate = 16000;
constexpr int kMaxAudioSeconds = 15 * 60;
constexpr auto kMaxPcmSamples = std::size_t(kTargetSampleRate)
	* kMaxAudioSeconds;
constexpr qint64 kMaxInputFileBytes = 256 * 1024 * 1024;
constexpr int kMaxSegments = 8192;
constexpr int kMaxResultCharacters = 1 * 1024 * 1024;
constexpr int kMaxSegmentCharacters = 256 * 1024;
constexpr int kMaxWorkerThreads = 8;
constexpr int kMaxActiveJobs = 2;
constexpr crl::time kCtxIdleTimeoutMs = 3 * 60000;
constexpr qint64 kModelHashChunkSize = 1024 * 1024;

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

void InvokeCallbackSafely(const std::function<void(QString)> &callback) {
	if (!callback) {
		return;
	}
	try {
		callback(QString());
	} catch (...) {
		LOG(("WhisperService: failure callback failed"));
	}
}

[[nodiscard]] bool VerifyModelFile(
		const QString &path,
		qint64 expectedSize,
		const QString &expectedSha256) {
	try {
		if (expectedSize <= 0 || expectedSha256.size() != 64) {
			return false;
		}
		for (const auto ch : expectedSha256) {
			if (!((ch >= u'0' && ch <= u'9')
				|| (ch >= u'a' && ch <= u'f')
				|| (ch >= u'A' && ch <= u'F'))) {
				return false;
			}
		}
		const auto expected = QByteArray::fromHex(
			expectedSha256.toLatin1());
		if (expected.size() != QCryptographicHash::hashLength(
			QCryptographicHash::Sha256)) {
			return false;
		}
		const auto info = QFileInfo(path);
		if (!info.isFile() || info.size() != expectedSize) {
			return false;
		}
		QFile file(path);
		if (!file.open(QIODevice::ReadOnly)) {
			return false;
		}

		auto hash = QCryptographicHash(QCryptographicHash::Sha256);
		qint64 read = 0;
		while (!file.atEnd()) {
			const auto chunk = file.read(kModelHashChunkSize);
			if (chunk.isEmpty()) {
				return file.error() == QFileDevice::NoError
					&& read == expectedSize
					&& hash.result() == expected;
			}
			if (read > expectedSize - chunk.size()) {
				return false;
			}
			hash.addData(chunk);
			read += chunk.size();
		}
		return read == expectedSize && hash.result() == expected;
	} catch (const std::bad_alloc &) {
		return false;
	}
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

	_freeInFlight = true;
	QThread *thread = nullptr;
	try {
		thread = QThread::create([context] {
			whisper_free(context);
		});
	} catch (...) {
		whisper_free(context);
		_freeInFlight = false;
		if (Core::Quitting()) {
			Core::App().quitPreventFinished();
		}
		return;
	}
	QObject::connect(thread, &QThread::finished, thread, [this, thread] {
		thread->deleteLater();
		_freeInFlight = false;
		if (Core::Quitting()) {
			LOG(("WhisperService doesn't prevent quit any more."));
			Core::App().quitPreventFinished();
		}
	});
	thread->start();
}

void WhisperService::scheduleFreeContext() {
	crl::on_main([this] {
		if (!_freeInFlight && !_activeJobs.load(std::memory_order_acquire)) {
			_idleTimer.callOnce(kCtxIdleTimeoutMs);
		}
	});
}

void WhisperService::freeContext() {
	if (_freeInFlight || _activeJobs.load(std::memory_order_acquire)) {
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
	if (_freeInFlight
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

std::vector<float> WhisperService::decodeAudioToPcm(const QString &filePath) {
	const auto fileInfo = QFileInfo(filePath);
	if (!fileInfo.isFile()
		|| fileInfo.size() <= 0
		|| fileInfo.size() > kMaxInputFileBytes) {
		LOG(("WhisperService: input file is missing or too large: %1").arg(
			filePath));
		return {};
	}

	const auto pathUtf8 = filePath.toUtf8();
	AVFormatContext *formatCtx = nullptr;
	if (avformat_open_input(
			&formatCtx,
			pathUtf8.constData(),
			nullptr,
			nullptr) < 0) {
		LOG(("WhisperService: failed to open file: %1").arg(filePath));
		return {};
	}
	const auto closeFormat = gsl::finally([&] {
		avformat_close_input(&formatCtx);
	});

	if (avformat_find_stream_info(formatCtx, nullptr) < 0
		|| formatCtx->nb_streams == 0) {
		return {};
	}
	if (formatCtx->duration != AV_NOPTS_VALUE
		&& formatCtx->duration > int64_t(kMaxAudioSeconds) * AV_TIME_BASE) {
		LOG(("WhisperService: audio duration exceeds the limit: %1").arg(
			filePath));
		return {};
	}

	int audioStreamIndex = -1;
	for (unsigned i = 0; i < formatCtx->nb_streams; ++i) {
		const auto stream = formatCtx->streams[i];
		if (stream && stream->codecpar
			&& stream->codecpar->codec_type == AVMEDIA_TYPE_AUDIO) {
			audioStreamIndex = (i <= static_cast<unsigned>(INT_MAX))
				? static_cast<int>(i)
				: -1;
			break;
		}
	}
	if (audioStreamIndex < 0) {
		LOG(("WhisperService: no audio stream in: %1").arg(filePath));
		return {};
	}

	const auto *codecParams = formatCtx->streams[audioStreamIndex]->codecpar;
	if (!codecParams
		|| codecParams->sample_rate <= 0
		|| codecParams->sample_rate > 384000
		|| codecParams->ch_layout.nb_channels <= 0
		|| codecParams->ch_layout.nb_channels > 32) {
		return {};
	}
	const auto codec = avcodec_find_decoder(codecParams->codec_id);
	if (!codec) {
		return {};
	}

	AVCodecContext *codecCtx = avcodec_alloc_context3(codec);
	if (!codecCtx) {
		return {};
	}
	const auto closeCodec = gsl::finally([&] {
		avcodec_free_context(&codecCtx);
	});
	if (avcodec_parameters_to_context(codecCtx, codecParams) < 0
		|| avcodec_open2(codecCtx, codec, nullptr) < 0
		|| codecCtx->sample_rate <= 0
		|| codecCtx->ch_layout.nb_channels <= 0) {
		return {};
	}

	constexpr AVChannelLayout targetLayout = AV_CHANNEL_LAYOUT_MONO;
	SwrContext *swrCtx = nullptr;
	if (swr_alloc_set_opts2(
			&swrCtx,
			&targetLayout,
			AV_SAMPLE_FMT_FLT,
			kTargetSampleRate,
			&codecCtx->ch_layout,
			codecCtx->sample_fmt,
			codecCtx->sample_rate,
			0,
			nullptr) < 0
		|| !swrCtx
		|| swr_init(swrCtx) < 0) {
		swr_free(&swrCtx);
		return {};
	}
	const auto closeSwr = gsl::finally([&] { swr_free(&swrCtx); });

	AVPacket *packet = av_packet_alloc();
	AVFrame *frame = av_frame_alloc();
	if (!packet || !frame) {
		av_packet_free(&packet);
		av_frame_free(&frame);
		return {};
	}
	const auto freePacket = gsl::finally([&] { av_packet_free(&packet); });
	const auto freeFrame = gsl::finally([&] { av_frame_free(&frame); });

	try {
		auto pcmData = std::vector<float>();
		pcmData.reserve(std::min<std::size_t>(
			kMaxPcmSamples,
			std::size_t(kTargetSampleRate) * 30));

		auto convertFrame = [&]() {
			if (!frame || frame->nb_samples <= 0
				|| frame->nb_samples > 10 * kTargetSampleRate) {
				return false;
			}
			const auto delay = swr_get_delay(swrCtx, codecCtx->sample_rate);
			if (delay < 0) {
				return false;
			}
			const auto outputSamples64 = av_rescale_rnd(
				delay + frame->nb_samples,
				kTargetSampleRate,
				codecCtx->sample_rate,
				AV_ROUND_UP);
			if (outputSamples64 <= 0
				|| outputSamples64 > INT_MAX
				|| static_cast<std::size_t>(outputSamples64)
					> kMaxPcmSamples - pcmData.size()) {
				return false;
			}

			const auto outputSamples = static_cast<int>(outputSamples64);
			const auto previousSize = pcmData.size();
			pcmData.resize(previousSize + outputSamples);
			auto *out = reinterpret_cast<uint8_t*>(
				pcmData.data() + previousSize);
			const auto converted = swr_convert(
				swrCtx,
				&out,
				outputSamples,
				const_cast<const uint8_t **>(frame->data),
				frame->nb_samples);
			if (converted < 0 || converted > outputSamples) {
				pcmData.resize(previousSize);
				return false;
			}
			pcmData.resize(previousSize + converted);
			return pcmData.size() < kMaxPcmSamples;
		};

		auto receiveFrames = [&]() {
			for (;;) {
				const auto result = avcodec_receive_frame(codecCtx, frame);
				if (result == AVERROR(EAGAIN) || result == AVERROR_EOF) {
					return true;
				}
				if (result < 0 || !convertFrame()) {
					return false;
				}
				av_frame_unref(frame);
			}
		};

		auto readResult = 0;
		while ((readResult = av_read_frame(formatCtx, packet)) >= 0) {
			if (packet->stream_index == audioStreamIndex) {
				auto sent = avcodec_send_packet(codecCtx, packet);
				if (sent == AVERROR(EAGAIN) && !receiveFrames()) {
					av_packet_unref(packet);
					return {};
				}
				if (sent == AVERROR(EAGAIN)) {
					sent = avcodec_send_packet(codecCtx, packet);
				}
				av_packet_unref(packet);
				if (sent < 0) {
					return {};
				}
				if (!receiveFrames()) {
					return {};
				}
			} else {
				av_packet_unref(packet);
			}
		}
		if (readResult != AVERROR_EOF) {
			return {};
		}

		const auto flush = avcodec_send_packet(codecCtx, nullptr);
		if ((flush < 0 && flush != AVERROR(EAGAIN))
			|| !receiveFrames()) {
			return {};
		}

		const auto delayed = swr_get_out_samples(swrCtx, 0);
		if (delayed < 0) {
			return {};
		}
		if (delayed > 0) {
			if (delayed > INT_MAX
				|| static_cast<std::size_t>(delayed)
					> kMaxPcmSamples - pcmData.size()) {
				return {};
			}
			const auto previousSize = pcmData.size();
			const auto outputSamples = static_cast<int>(delayed);
			pcmData.resize(previousSize + outputSamples);
			auto *out = reinterpret_cast<uint8_t*>(
				pcmData.data() + previousSize);
			const auto converted = swr_convert(
				swrCtx,
				&out,
				outputSamples,
				nullptr,
				0);
			if (converted < 0 || converted > outputSamples) {
				return {};
			}
			pcmData.resize(previousSize + converted);
		}
		return pcmData;
	} catch (const std::bad_alloc &) {
		LOG(("WhisperService: audio buffer allocation failed: %1").arg(
			filePath));
		return {};
	}
}

void WhisperService::transcribeOnDemand(
		const QString &filePath,
		const QString &modelPath,
		const qint64 modelSize,
		const QString &modelSha256,
		const QString &language,
		std::function<void(QString)> callback) {
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
				complete,
				jobFinished] {
			const auto finishGuard = gsl::finally(jobFinished);

			try {
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
				params.detect_language = false;
				params.language = (language.isEmpty() || language == u"auto"_q)
					? nullptr
					: languageUtf8.constData();
				params.translate = false;
				params.print_progress = false;
				params.print_realtime = false;
				params.print_timestamps = false;
				params.single_segment = true;
				params.temperature_inc = 0.2f;
				params.no_speech_thold = 1.0f;
				params.n_threads = WorkerThreadCount();

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
