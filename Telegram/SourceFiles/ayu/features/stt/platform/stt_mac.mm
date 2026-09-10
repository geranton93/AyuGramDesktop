// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#include "ayu/features/stt/platform/stt_mac.h"

#include "base/debug_log.h"
#include "crl/crl_on_main.h"

#if defined(HAVE_WHISPER)
#include "ayu/features/stt/whisper_service.h"
#endif

#import <AVFoundation/AVFoundation.h>
#import <Foundation/Foundation.h>
#import <Speech/Speech.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <new>
#include <vector>

namespace {

constexpr int kTargetSampleRate = 16000;
constexpr int kMaxActiveJobs = 2;
constexpr qsizetype kMaxResultCharacters = 1024 * 1024;
constexpr auto kAuthorizationTimeoutSeconds = 30;
constexpr auto kRecognitionTimeoutSeconds = 90;
std::atomic<int> ActiveJobs = 0;

void InvokeCallbackSafely(
		const std::function<void(QString)> &callback,
		QString text) {
	if (!callback) {
		return;
	}
	try {
		callback(std::move(text));
	} catch (...) {
		LOG(("SFSpeech callback failed"));
	}
}

[[nodiscard]] bool ReserveJob() {
	auto current = ActiveJobs.load(std::memory_order_relaxed);
	while (current < kMaxActiveJobs) {
		if (ActiveJobs.compare_exchange_weak(
				current,
				current + 1,
				std::memory_order_acq_rel,
				std::memory_order_relaxed)) {
			return true;
		}
	}
	return false;
}

API_AVAILABLE(macos(10.15))
void RunRecognitionPass(
			SFSpeechRecognizer *recognizer,
			std::shared_ptr<std::vector<float>> pcm,
			std::function<void(QString)> complete) {
	if (!recognizer || !pcm || pcm->empty()
		|| pcm->size() > std::numeric_limits<AVAudioFrameCount>::max()) {
		complete(QString());
		return;
	}
	if (@available(macOS 10.15, *)) {
		if (!recognizer.supportsOnDeviceRecognition) {
			complete(QString());
			return;
		}
	} else {
		complete(QString());
		return;
	}

	AVAudioFormat *format = [[AVAudioFormat alloc]
		initWithCommonFormat:AVAudioPCMFormatFloat32
		sampleRate:kTargetSampleRate
		channels:1
		interleaved:NO];
	if (!format) {
		complete(QString());
		return;
	}

	const auto frameCount = static_cast<AVAudioFrameCount>(pcm->size());
	AVAudioPCMBuffer *buffer = [[AVAudioPCMBuffer alloc]
		initWithPCMFormat:format
		frameCapacity:frameCount];
	if (!buffer || !buffer.floatChannelData
		|| !buffer.floatChannelData[0]) {
		complete(QString());
		return;
	}
	buffer.frameLength = frameCount;
	memcpy(
		buffer.floatChannelData[0],
		pcm->data(),
		pcm->size() * sizeof(float));

	SFSpeechAudioBufferRecognitionRequest *request =
		[[SFSpeechAudioBufferRecognitionRequest alloc] init];
	if (!request) {
		complete(QString());
		return;
	}
	request.shouldReportPartialResults = NO;
	if (@available(macOS 10.15, *)) {
		request.requiresOnDeviceRecognition = YES;
	}

	std::shared_ptr<std::atomic_bool> passFinished;
	try {
		passFinished = std::make_shared<std::atomic_bool>(false);
	} catch (const std::bad_alloc &) {
		complete(QString());
		return;
	}
	__block SFSpeechRecognitionTask *task = nil;
	void (^finish)(QString) = ^(QString text) {
		if (passFinished->exchange(true, std::memory_order_acq_rel)) {
			return;
		}
		if (task) {
			[task cancel];
			task = nil;
		}
		complete(std::move(text));
	};

	task = [recognizer recognitionTaskWithRequest:request
		resultHandler:^(SFSpeechRecognitionResult *result, NSError *error) {
			try {
				if (!request) {
					finish(QString());
					return;
				}
				if (error) {
					LOG(("SFSpeech on-device error: %1").arg(
						QString::fromNSString(error.localizedDescription)));
					finish(QString());
					return;
				}
				if (result && result.isFinal) {
					const auto transcription
						= result.bestTranscription.formattedString;
					if (!transcription
						|| [transcription length] > kMaxResultCharacters) {
						finish(QString());
						return;
					}
					finish(QString::fromNSString(transcription));
				}
			} catch (...) {
				finish(QString());
			}
		}];
	if (!task) {
		finish(QString());
		return;
	}

	[request appendAudioPCMBuffer:buffer];
	[request endAudio];
	dispatch_after(
		dispatch_time(
			DISPATCH_TIME_NOW,
			static_cast<int64_t>(kRecognitionTimeoutSeconds)
				* NSEC_PER_SEC),
		dispatch_get_main_queue(),
		^{ finish(QString()); });
}

} // namespace

namespace Ayu::STT::Mac {

void transcribeFile(
		const QString &filePath,
		const QString &language,
		std::function<void(QString)> callback) {
	std::shared_ptr<std::function<void(QString)>> sharedCallback;
	try {
		sharedCallback = std::make_shared<
			std::function<void(QString)>>(callback);
	} catch (const std::bad_alloc &) {
		InvokeCallbackSafely(callback, QString());
		return;
	}
	std::shared_ptr<std::atomic_bool> completeOnce;
	try {
		completeOnce = std::make_shared<std::atomic_bool>(false);
	} catch (const std::bad_alloc &) {
		if (*sharedCallback) {
			auto failed = std::move(*sharedCallback);
			InvokeCallbackSafely(failed, QString());
		}
		return;
	}
	const auto complete = [completeOnce, sharedCallback](QString text) {
		crl::on_main([completeOnce, sharedCallback, text = std::move(text)]
			() mutable {
			if (completeOnce->exchange(true, std::memory_order_acq_rel)) {
				return;
			}
			ActiveJobs.fetch_sub(1, std::memory_order_acq_rel);
			if (*sharedCallback) {
				auto callback = std::move(*sharedCallback);
				InvokeCallbackSafely(callback, std::move(text));
			}
		});
	};

	if (!ReserveJob()) {
		crl::on_main([sharedCallback] {
			if (*sharedCallback) {
				auto callback = std::move(*sharedCallback);
				InvokeCallbackSafely(callback, QString());
			}
		});
		return;
	}

	std::shared_ptr<QString> sharedPath;
	std::shared_ptr<QString> sharedLanguage;
	std::shared_ptr<std::atomic_bool> cancelled;
	std::shared_ptr<std::atomic_bool> authorizationFinished;
	try {
		sharedPath = std::make_shared<QString>(filePath);
		sharedLanguage = std::make_shared<QString>(language);
		cancelled = std::make_shared<std::atomic_bool>(false);
		authorizationFinished = std::make_shared<std::atomic_bool>(false);
	} catch (const std::bad_alloc &) {
		ActiveJobs.fetch_sub(1, std::memory_order_acq_rel);
		crl::on_main([sharedCallback] {
			if (*sharedCallback) {
				auto callback = std::move(*sharedCallback);
				InvokeCallbackSafely(callback, QString());
			}
		});
		return;
	}

	const auto finish = [complete, cancelled](QString text) {
		if (cancelled->exchange(true, std::memory_order_acq_rel)) {
			return;
		}
		complete(std::move(text));
	};
	dispatch_after(
		dispatch_time(
			DISPATCH_TIME_NOW,
			static_cast<int64_t>(kAuthorizationTimeoutSeconds)
				* NSEC_PER_SEC),
		dispatch_get_main_queue(),
		^{
			if (!authorizationFinished->load(std::memory_order_acquire)) {
				finish(QString());
			}
		});
	if (@available(macOS 10.15, *)) {
		[SFSpeechRecognizer requestAuthorization:^(
				SFSpeechRecognizerAuthorizationStatus status) {
			authorizationFinished->store(true, std::memory_order_release);
			if (cancelled->load(std::memory_order_acquire)) {
				return;
			}
			if (status != SFSpeechRecognizerAuthorizationStatusAuthorized) {
				finish(QString());
				return;
			}
			dispatch_async(
				dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0),
				^{
				try {
					if (cancelled->load(std::memory_order_acquire)) {
						return;
					}
#if defined(HAVE_WHISPER)
					auto pcm = Ayu::STT::WhisperService::decodeAudioToPcm(
						*sharedPath);
					if (pcm.empty()) {
						finish(QString());
						return;
					}
					std::shared_ptr<std::vector<float>> sharedPcm;
					try {
						sharedPcm = std::make_shared<std::vector<float>>(
							std::move(pcm));
					} catch (const std::bad_alloc &) {
						finish(QString());
						return;
					}
					if (cancelled->load(std::memory_order_acquire)) {
						return;
					}
					NSString *localeId = nil;
					if (sharedLanguage->isEmpty()
						|| *sharedLanguage == u"auto"_q) {
						localeId = [[NSLocale preferredLanguages] firstObject]
							?: @"en-US";
					} else {
						localeId = sharedLanguage->toNSString();
					}
					auto *locale = [[NSLocale alloc]
						initWithLocaleIdentifier:localeId];
					auto *recognizer = [[SFSpeechRecognizer alloc]
						initWithLocale:locale];
					if (!recognizer || !recognizer.available) {
						finish(QString());
						return;
					}
					RunRecognitionPass(recognizer, sharedPcm, finish);
#else
					finish(QString());
#endif
				} catch (...) {
					finish(QString());
				}
				});
		}];
	} else {
		finish(QString());
	}
}

void requestSpeechPermission() {
	if (@available(macOS 10.15, *)) {
		[SFSpeechRecognizer requestAuthorization:^(
				SFSpeechRecognizerAuthorizationStatus) {}];
	}
}

} // namespace Ayu::STT::Mac
