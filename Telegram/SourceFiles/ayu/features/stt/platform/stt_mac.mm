// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#include "ayu/features/stt/platform/stt_mac.h"

#include "ayu/features/stt/audio_decoder.h"
#include "base/debug_log.h"
#include "crl/crl_on_main.h"

#import <AVFoundation/AVFoundation.h>
#import <Foundation/Foundation.h>
#import <Speech/Speech.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <deque>
#include <functional>
#include <limits>
#include <memory>
#include <mutex>
#include <new>
#include <vector>

namespace {

constexpr int kTargetSampleRate = 16000;
constexpr int kMaxActiveJobs = 2;
constexpr qsizetype kMaxResultCharacters = 1024 * 1024;
constexpr auto kAuthorizationTimeoutSeconds = 30;
constexpr auto kRecognitionTimeoutSeconds = 90;
constexpr auto kCancellationPollSeconds = 1;
constexpr std::size_t kMaxQueuedJobs = 16;
std::atomic<int> ActiveJobs = 0;
struct PendingJob {
	std::function<void()> start;
	std::function<bool()> cancelled;
	std::function<void()> reject;
};
std::deque<PendingJob> PendingJobs;
std::mutex PendingJobsMutex;

template <typename T>
[[nodiscard]] std::shared_ptr<T> MakeOwnedObject(T *object) {
	return object
		? std::shared_ptr<T>(object, [](T *value) { [value release]; })
		: nullptr;
}

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

void StartNextJob();

void InvokePendingRejections(
		std::vector<std::function<void()>> rejections) {
	for (auto &reject : rejections) {
		if (!reject) {
			continue;
		}
		try {
			reject();
		} catch (...) {
			LOG(("SFSpeech queued job rejection failed"));
		}
	}
}

void PruneCancelledJobs(
		std::vector<std::function<void()>> &rejections) {
	for (auto i = PendingJobs.begin(); i != PendingJobs.end();) {
		auto cancelled = false;
		try {
			cancelled = i->cancelled && i->cancelled();
		} catch (...) {
			cancelled = true;
		}
		if (!cancelled) {
			++i;
			continue;
		}
		if (i->reject) {
			rejections.push_back(std::move(i->reject));
		}
		i = PendingJobs.erase(i);
	}
}

void FinishJob() {
	const auto previous = ActiveJobs.fetch_sub(
		1,
		std::memory_order_acq_rel);
	if (previous <= 0) {
		ActiveJobs.store(0, std::memory_order_release);
		return;
	}
	StartNextJob();
}

void StartNextJob() {
	std::function<void()> next;
	std::vector<std::function<void()>> rejections;
	{
		const auto lock = std::lock_guard(PendingJobsMutex);
		PruneCancelledJobs(rejections);
		if (!PendingJobs.empty() && ReserveJob()) {
			next = std::move(PendingJobs.front().start);
			PendingJobs.pop_front();
		}
	}
	InvokePendingRejections(std::move(rejections));
	if (!next) {
		return;
	}
	try {
		crl::on_main([next = std::move(next)]() mutable {
			try {
				next();
			} catch (...) {
				LOG(("SFSpeech queued job failed to start"));
				FinishJob();
			}
		});
	} catch (...) {
		FinishJob();
	}
}

[[nodiscard]] bool ScheduleJob(
		std::function<void()> job,
		std::function<bool()> cancelled,
		std::function<void()> reject) {
	auto reserved = false;
	auto scheduled = false;
	std::vector<std::function<void()>> rejections;
	try {
		{
			const auto lock = std::lock_guard(PendingJobsMutex);
			PruneCancelledJobs(rejections);
			if (!ReserveJob()) {
				if (PendingJobs.size() < kMaxQueuedJobs) {
					PendingJobs.push_back({
						.start = std::move(job),
						.cancelled = std::move(cancelled),
						.reject = std::move(reject),
					});
					scheduled = true;
				}
			} else {
				reserved = true;
				scheduled = true;
			}
		}
		InvokePendingRejections(std::move(rejections));
		if (!scheduled) {
			if (reject) {
				try {
					reject();
				} catch (...) {
					LOG(("SFSpeech queued job rejection failed"));
				}
			}
			return false;
		}
		if (!reserved) {
			return true;
		}
		crl::on_main([job = std::move(job)]() mutable {
			try {
				job();
			} catch (...) {
				LOG(("SFSpeech job failed to start"));
				FinishJob();
			}
		});
		return true;
	} catch (...) {
		if (reserved) {
			FinishJob();
		}
		if (!scheduled && reject) {
			try {
				reject();
			} catch (...) {
				LOG(("SFSpeech queued job rejection failed"));
			}
		}
		return false;
	}
}

API_AVAILABLE(macos(10.15))
void RunRecognitionPass(
		std::shared_ptr<SFSpeechRecognizer> recognizer,
		std::shared_ptr<std::vector<float>> pcm,
		std::function<void(QString)> complete,
		std::function<bool()> cancelled) {
	if (!recognizer || !pcm || pcm->empty()
		|| pcm->size() > std::numeric_limits<AVAudioFrameCount>::max()) {
		complete(QString());
		return;
	}
	if (cancelled && cancelled()) {
		complete(QString());
		return;
	}
	auto *recognizerObject = recognizer.get();
	if (@available(macOS 10.15, *)) {
		if (![recognizerObject supportsOnDeviceRecognition]) {
			complete(QString());
			return;
		}
	} else {
		complete(QString());
		return;
	}

	const auto format = MakeOwnedObject([[AVAudioFormat alloc]
		initWithCommonFormat:AVAudioPCMFormatFloat32
		sampleRate:kTargetSampleRate
		channels:1
		interleaved:NO]);
	if (!format) {
		complete(QString());
		return;
	}

	const auto frameCount = static_cast<AVAudioFrameCount>(pcm->size());
	const auto buffer = MakeOwnedObject([[AVAudioPCMBuffer alloc]
		initWithPCMFormat:format.get()
		frameCapacity:frameCount]);
	auto *bufferObject = buffer.get();
	if (!bufferObject || !bufferObject.floatChannelData
		|| !bufferObject.floatChannelData[0]) {
		complete(QString());
		return;
	}
	bufferObject.frameLength = frameCount;
	memcpy(
		bufferObject.floatChannelData[0],
		pcm->data(),
		pcm->size() * sizeof(float));

	const auto request = MakeOwnedObject(
		[[SFSpeechAudioBufferRecognitionRequest alloc] init]);
	auto *requestObject = request.get();
	if (!requestObject) {
		complete(QString());
		return;
	}
	requestObject.shouldReportPartialResults = NO;
	if (@available(macOS 10.15, *)) {
		requestObject.requiresOnDeviceRecognition = YES;
	}

	std::shared_ptr<std::atomic_bool> passFinished;
	struct PollState {
		std::function<void()> callback;
	};
	std::shared_ptr<PollState> poll;
	try {
		passFinished = std::make_shared<std::atomic_bool>(false);
		poll = std::make_shared<PollState>();
	} catch (const std::bad_alloc &) {
		complete(QString());
		return;
	}
	const auto weakPoll = std::weak_ptr<PollState>(poll);
	__block SFSpeechRecognitionTask *task = nil;
	void (^finish)(QString) = ^(QString text) {
		if (passFinished->exchange(true, std::memory_order_acq_rel)) {
			return;
		}
		if (task) {
			[task cancel];
			[task release];
			task = nil;
		}
		complete(std::move(text));
	};

	task = [[recognizerObject recognitionTaskWithRequest:requestObject
		resultHandler:^(SFSpeechRecognitionResult *result, NSError *error) {
			try {
				if ((cancelled && cancelled())
					|| !recognizer
					|| !request) {
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
		}]
		retain];
	if (!task) {
		finish(QString());
		return;
	}

	[requestObject appendAudioPCMBuffer:bufferObject];
	[requestObject endAudio];
	poll->callback = [weakPoll, passFinished, cancelled, finish] {
		if (passFinished->load(std::memory_order_acquire)) {
			return;
		}
		if (cancelled && cancelled()) {
			finish(QString());
			return;
		}
		const auto keepAlive = weakPoll.lock();
		if (!keepAlive) {
			return;
		}
		dispatch_after(
			dispatch_time(
				DISPATCH_TIME_NOW,
				static_cast<int64_t>(kCancellationPollSeconds)
					* NSEC_PER_SEC),
			dispatch_get_main_queue(),
			^{
				if (!passFinished->load(std::memory_order_acquire)) {
					keepAlive->callback();
				}
			});
	};
	poll->callback();
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

bool isAvailable() {
	if (@available(macOS 10.15, *)) {
		return true;
	}
	return false;
}

void transcribeFile(
		const QString &filePath,
		const QString &language,
		std::function<void(QString)> callback,
		std::function<bool()> externalCancelled) {
	if (!isAvailable()) {
		InvokeCallbackSafely(callback, QString());
		return;
	}
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
			FinishJob();
			if (*sharedCallback) {
				auto callback = std::move(*sharedCallback);
				InvokeCallbackSafely(callback, std::move(text));
			}
		});
	};
	const auto rejectBeforeStart = [completeOnce, sharedCallback] {
		const auto deliver = [completeOnce, sharedCallback] {
			if (completeOnce->exchange(true, std::memory_order_acq_rel)) {
				return;
			}
			if (*sharedCallback) {
				auto callback = std::move(*sharedCallback);
				InvokeCallbackSafely(callback, QString());
			}
		};
		try {
			crl::on_main(deliver);
		} catch (...) {
			deliver();
		}
	};

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
	const auto start = [=] {
		if (externalCancelled && externalCancelled()) {
			finish(QString());
			return;
		}
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
				if (cancelled->load(std::memory_order_acquire)
					|| (externalCancelled && externalCancelled())) {
					finish(QString());
					return;
				}
				if (status != SFSpeechRecognizerAuthorizationStatusAuthorized) {
					finish(QString());
					return;
				}
				dispatch_async(
					dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0),
					^{
					@autoreleasepool {
						try {
							if (cancelled->load(std::memory_order_acquire)
								|| (externalCancelled && externalCancelled())) {
								finish(QString());
								return;
							}
							auto pcm = Ayu::STT::decodeAudioToPcm(
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
							if (cancelled->load(std::memory_order_acquire)
								|| (externalCancelled && externalCancelled())) {
								finish(QString());
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
							const auto locale = MakeOwnedObject(
								[[NSLocale alloc]
									initWithLocaleIdentifier:localeId]);
							const auto recognizer = MakeOwnedObject(
								[[SFSpeechRecognizer alloc]
									initWithLocale:locale.get()]);
							if (!recognizer || ![recognizer.get() isAvailable]) {
								finish(QString());
								return;
							}
							RunRecognitionPass(
								std::move(recognizer),
								sharedPcm,
								finish,
								externalCancelled);
						} catch (...) {
							finish(QString());
						}
					}
					});
			}];
		} else {
			finish(QString());
		}
	};
	const auto isCancelled = [externalCancelled] {
		if (!externalCancelled) {
			return false;
		}
		try {
			return externalCancelled();
		} catch (...) {
			return true;
		}
	};
	if (!ScheduleJob(start, isCancelled, rejectBeforeStart)) {
		return;
	}
}

void requestSpeechPermission() {
	if (@available(macOS 10.15, *)) {
		[SFSpeechRecognizer requestAuthorization:^(
				SFSpeechRecognizerAuthorizationStatus) {}];
	}
}

} // namespace Ayu::STT::Mac
