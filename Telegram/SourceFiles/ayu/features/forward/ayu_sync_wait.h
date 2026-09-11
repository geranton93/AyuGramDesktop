// This is the source code of AyuGram for Desktop.

#pragma once

#include "ayu/utils/telegram_helpers.h"

#include <algorithm>
#include <chrono>

namespace AyuSync {

enum class DownloadWaitResult {
	Completed,
	Cancelled,
	TimedOut,
};

[[nodiscard]] inline DownloadWaitResult WaitForDownload(
		TimedCountDownLatch &done,
		std::chrono::milliseconds timeout,
		const Fn<bool()> &cancelled) {
	const auto deadline = std::chrono::steady_clock::now() + timeout;
	while (true) {
		if (done.await(std::chrono::milliseconds(0))) {
			return DownloadWaitResult::Completed;
		}
		if (cancelled && cancelled()) {
			return DownloadWaitResult::Cancelled;
		}
		const auto remaining = std::chrono::duration_cast<
			std::chrono::milliseconds>(
			deadline - std::chrono::steady_clock::now());
		if (remaining <= std::chrono::milliseconds(0)) {
			return DownloadWaitResult::TimedOut;
		}
		if (done.await(std::min(
				remaining,
				std::chrono::milliseconds(100)))) {
			return DownloadWaitResult::Completed;
		}
	}
}

} // namespace AyuSync
