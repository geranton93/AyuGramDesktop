/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#pragma once

#include <algorithm>
#include <cstdint>
#include <limits>

namespace Media {
namespace Streaming {

namespace Prefetch {

inline constexpr auto kPartSize = std::int64_t(128 * 1024);
inline constexpr auto kMinParts = 8;
inline constexpr auto kMaxParts = 64;
inline constexpr auto kMinWindowBytes = kMinParts * kPartSize;
inline constexpr auto kMaxWindowBytes = kMaxParts * kPartSize;
inline constexpr auto kStableBufferMs = std::int64_t(1500);
inline constexpr auto kPlaybackBufferMs = std::int64_t(3000);
inline constexpr auto kMaxLatencyMs = std::int64_t(2000);

constexpr std::int64_t SaturatingMultiply(
		std::int64_t left,
		std::int64_t right) {
	if (left <= 0 || right <= 0) {
		return 0;
	}
	constexpr auto max = std::numeric_limits<std::int64_t>::max();
	return (left > max / right) ? max : (left * right);
}

constexpr std::int64_t BytesFor(
		std::int64_t bytesPerSecond,
		std::int64_t milliseconds) {
	return SaturatingMultiply(bytesPerSecond, milliseconds) / 1000;
}

} // namespace Prefetch

struct PrefetchInput {
	std::int64_t downloadBytesPerSecond = 0;
	std::int64_t consumptionBytesPerSecond = 0;
	int requestLatencyMs = 0;
	bool unreliable = true;
	bool seeking = false;
};

struct PrefetchWindow {
	int partsAhead = Prefetch::kMinParts;
};

// A finite window is derived from both the time needed to cover request latency
// and the amount the decoder is likely to consume while those requests finish.
// Unknown or unstable observations intentionally use the small fallback so one
// bad sample cannot turn into an unbounded burst of speculative downloads.
[[nodiscard]] constexpr PrefetchWindow ComputePrefetchWindow(
		const PrefetchInput &input) {
	if (input.seeking
		|| input.unreliable
		|| input.downloadBytesPerSecond <= 0) {
		return {};
	}

	const auto latency = std::clamp<std::int64_t>(
		input.requestLatencyMs,
		0,
		Prefetch::kMaxLatencyMs);
	const auto networkWindow = Prefetch::BytesFor(
		input.downloadBytesPerSecond,
		Prefetch::kStableBufferMs + latency);
	const auto playbackWindow = Prefetch::BytesFor(
		std::max<std::int64_t>(input.consumptionBytesPerSecond, 0),
		Prefetch::kPlaybackBufferMs + latency);
	const auto targetBytes = std::min(
		Prefetch::kMaxWindowBytes,
		std::max({
			Prefetch::kMinWindowBytes,
			networkWindow,
			playbackWindow,
		}));
	const auto parts = (targetBytes + Prefetch::kPartSize - 1)
		/ Prefetch::kPartSize;

	return {
		.partsAhead = int(std::clamp<std::int64_t>(
			parts,
			Prefetch::kMinParts,
			Prefetch::kMaxParts)),
	};
}

} // namespace Streaming
} // namespace Media
