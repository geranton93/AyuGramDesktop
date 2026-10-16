/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#include "data/data_media_preload.h"

#include "data/data_document.h"
#include "data/data_document_media.h"
#include "data/data_file_origin.h"
#include "data/data_photo.h"
#include "data/data_photo_media.h"
#include "data/data_session.h"
#include "main/main_session.h"
#include "main/main_session_settings.h"
#include "media/streaming/media_streaming_reader.h"
#include "storage/file_download.h" // kMaxFileInMemory.

#include <algorithm>
#include <array>
#include <cstring>
#include <utility>

namespace Data {
namespace {

constexpr auto kDefaultPreloadPrefix = 4 * 1024 * 1024;

static_assert(
	Storage::kDownloadPartSize
		== ::Media::Streaming::Loader::kPartSize);

[[nodiscard]] int64 ChoosePreloadPrefix(not_null<DocumentData*> video) {
	const auto result = video->videoPreloadPrefix();
	return result
		? result
		: std::min(int64(kDefaultPreloadPrefix), video->size);
}

[[nodiscard]] QByteArray ForceComplexPartsMapSerialization(
		QByteArray result,
		int64 fullSize) {
	while ((result.size() % ::Media::Streaming::Loader::kPartSize) == 0
		|| result.size() == fullSize) {
		// Make sure it is parsed as a complex map.
		result.push_back(char(0));
	}
	return result;
}

[[nodiscard]] bool HasExpectedVideoPreloadPartSize(
		int64 offset,
		int64 bytesSize,
		int64 fullSize) {
	return (offset >= 0)
		&& (offset < fullSize)
		&& (bytesSize == std::min<int64>(
			Storage::kDownloadPartSize,
			fullSize - offset));
}

[[nodiscard]] bool HasContiguousVideoPreloadPrefix(
		const char *data,
		int dataSize,
		int64 fullSize,
		int64 prefix,
		int64 partSize) {
	if (!data
		|| (dataSize <= 0)
		|| (fullSize <= 0)
		|| (prefix <= 0)
		|| (prefix > fullSize)
		|| (partSize <= 0)) {
		return false;
	}

	constexpr auto kMaxCachedParts = 80U;
	constexpr auto kPartsInSlice = 64;
	constexpr auto kPartsOutsideFirstSliceGood = 8;
	const auto firstSliceSize = partSize * kPartsInSlice;
	const auto maxHeaderParts = partSize * 80;
	enum class ComplexMapParseResult {
		kCountUnavailable,
		kInvalidCount,
		kInvalidPart,
		kComplete,
	};
	auto ranges = std::array<std::pair<uint64, uint64>, 160>();
	auto rangesCount = 0;
	const auto takeInt = [&](int &offset, uint32 &value) {
		if (dataSize - offset < int(sizeof(value))) {
			return false;
		}
		std::memcpy(&value, data + offset, sizeof(value));
		offset += sizeof(value);
		return true;
	};
	const auto parseComplexMap = [&](
			int &offset,
			int64 maxSize,
			int &outsideFirstSliceParts) {
		auto partCount = uint32();
		if (!takeInt(offset, partCount)) {
			return ComplexMapParseResult::kCountUnavailable;
		}
		if (!partCount || (partCount > kMaxCachedParts)) {
			return ComplexMapParseResult::kInvalidCount;
		}
		auto previousEnd = uint64();
		for (auto i = uint32(); i != partCount; ++i) {
			auto partOffset = uint32();
			auto partLength = uint32();
			if (!takeInt(offset, partOffset)
				|| !takeInt(offset, partLength)) {
				return ComplexMapParseResult::kInvalidPart;
			}
			const auto end = uint64(partOffset) + partLength;
			if ((partOffset % partSize) != 0
				|| (uint64(partOffset) < previousEnd)
				|| !HasExpectedVideoPreloadPartSize(
					partOffset,
					partLength,
					maxSize)
				|| (dataSize - offset < int(partLength))) {
				return ComplexMapParseResult::kInvalidPart;
			}
			if (rangesCount == int(ranges.size())) {
				return ComplexMapParseResult::kInvalidPart;
			}
			ranges[rangesCount++] = { partOffset, end };
			if (partOffset >= firstSliceSize) {
				++outsideFirstSliceParts;
			}
			previousEnd = end;
			offset += int(partLength);
		}
		return ComplexMapParseResult::kComplete;
	};
	if ((dataSize % partSize) == 0 || int64(dataSize) == fullSize) {
		const auto probeRangesCount = rangesCount;
		auto probeOffset = 0;
		auto probeOutsideFirstSliceParts = 0;
		const auto probeResult = parseComplexMap(
			probeOffset,
			fullSize,
			probeOutsideFirstSliceParts);
		const auto probeParsedParts = rangesCount - probeRangesCount;
		rangesCount = probeRangesCount;
		if (probeResult != ComplexMapParseResult::kComplete
			&& !probeParsedParts) {
			return (int64(dataSize) <= fullSize)
				&& (int64(dataSize) >= prefix);
		}
	}
	auto offset = 0;
	auto outsideFirstSliceParts = 0;
	const auto primaryMapResult = parseComplexMap(
		offset,
		fullSize,
		outsideFirstSliceParts);
	if ((primaryMapResult == ComplexMapParseResult::kComplete
			|| primaryMapResult == ComplexMapParseResult::kInvalidCount)
		&& (fullSize > maxHeaderParts)
		&& (outsideFirstSliceParts <= kPartsOutsideFirstSliceGood)) {
		const auto remaining = dataSize - offset;
		auto includedOffset = offset;
		auto includedRangesCount = rangesCount;
		auto ignoredOutsideCount = 0;
		const auto includedMapResult = parseComplexMap(
			offset,
			firstSliceSize,
			ignoredOutsideCount);
		if (includedMapResult != ComplexMapParseResult::kComplete
			&& (rangesCount == includedRangesCount)
			&& ((remaining % partSize) == 0
				|| int64(remaining) == firstSliceSize)
			&& (int64(remaining) <= firstSliceSize)) {
			offset = includedOffset;
			rangesCount = includedRangesCount;
			for (auto partOffset = int64();
				partOffset < remaining;
				partOffset += partSize) {
				if (rangesCount == int(ranges.size())) {
					return false;
				}
				ranges[rangesCount++] = {
					uint64(partOffset),
					uint64(std::min<int64>(
						partOffset + partSize,
						firstSliceSize))
				};
			}
		}
	}
	std::sort(ranges.begin(), ranges.begin() + rangesCount);
	auto contiguousTill = uint64();
	for (auto i = 0; i != rangesCount; ++i) {
		const auto &[partOffset, partEnd] = ranges[i];
		if (partOffset > contiguousTill) {
			break;
		}
		contiguousTill = std::max(contiguousTill, partEnd);
	}
	return contiguousTill >= uint64(prefix);
}

} // namespace

MediaPreload::MediaPreload(Fn<void(bool)> done)
: _done(std::move(done)) {
}

void MediaPreload::callDone(bool success) {
	if (const auto onstack = base::take(_done)) {
		onstack(success);
	}
}

PhotoPreload::PhotoPreload(
	not_null<PhotoData*> photo,
	FileOrigin origin,
	Fn<void(bool)> done,
	bool retryCancelled,
	Completion completion)
: MediaPreload(std::move(done))
, _photo(photo->createMediaView()) {
	start(origin, retryCancelled, completion);
}

PhotoPreload::PhotoPreload(
	not_null<PhotoData*> photo,
	FileOrigin origin,
	Fn<void()> done,
	bool retryCancelled,
	Completion completion)
: PhotoPreload(
	photo,
	origin,
	[done = std::move(done)](bool) mutable {
		if (done) {
			done();
		}
	},
	retryCancelled,
	completion) {
}

PhotoPreload::~PhotoPreload() {
	_sessionLifetime.destroy();
	_lifetime.destroy();
	if (_registered) {
		_registered = false;
		_photo->owner()->releasePreload(_requireFull);
	}
}

void PhotoPreload::sessionCleared() {
	_lifetime.destroy();
	if (_registered) {
		_registered = false;
		_photo->owner()->releasePreload(_requireFull);
	}
	callDone(false);
}

bool PhotoPreload::Should(
		not_null<PhotoData*> photo,
		not_null<PeerData*> context,
		bool retryCancelled) {
	return (!photo->cancelled()
		|| (retryCancelled && photo->_cancelledByPreloadLease))
		&& AutoDownload::Should(
			photo->session().settings().autoDownload(),
			context,
			photo);
}

void PhotoPreload::start(
		FileOrigin origin,
		bool retryCancelled,
		Completion completion) {
	const auto media = _photo;
	const auto photo = media->owner();
	const auto progressive = [photo, completion] {
		return (completion == Completion::Progressive)
			&& photo->_largeProgressivePartReady;
	};
	const auto successful = [media, progressive] {
		return media->loaded() || progressive();
	};
	const auto finished = [media, photo, progressive] {
		return media->loaded()
			|| progressive()
			|| photo->failed(PhotoSize::Large)
			|| photo->cancelled();
	};
	if (media->loaded()
		|| progressive()
		|| photo->failed(PhotoSize::Large)
		|| (photo->cancelled()
			&& (!retryCancelled || !photo->_cancelledByPreloadLease))) {
		callDone(successful());
		return;
	}
	if (photo->isNull()) {
		callDone(false);
		return;
	}
	const auto requireFull = (completion == Completion::Full);
	_requireFull = requireFull;
	_registered = true;
	photo->owner().sessionDataAboutToBeCleared()
	| rpl::take(1)
	| rpl::on_next([this] {
		sessionCleared();
	}, _sessionLifetime);
	photo->owner().photoLoadProgress() | rpl::filter([=](
			not_null<PhotoData*> updated) {
		return (updated == photo) && finished();
	}) | rpl::take(1) | rpl::on_next([=, this] {
		callDone(successful());
	}, _lifetime);
	if (photo->loadForPreload(origin, requireFull)) {
		crl::on_main(this, [=, this] {
			if (_registered) {
				photo->continueFullPreload(origin);
			}
		});
	}
}

VideoPreload::VideoPreload(
	not_null<DocumentData*> video,
	FileOrigin origin,
	Fn<void(bool)> done)
: MediaPreload(std::move(done))
, DownloadMtprotoTask(
	&video->session().downloader(),
	video->videoPreloadLocation(),
	origin)
, _video(video)
, _full(video->size) {
	if (Can(video)) {
		check();
	} else {
		callDone(false);
	}
}

VideoPreload::VideoPreload(
	not_null<DocumentData*> video,
	FileOrigin origin,
	Fn<void()> done)
: VideoPreload(
	video,
	origin,
	[done = std::move(done)](bool) mutable {
		if (done) {
			done();
		}
	}) {
}

void VideoPreload::check() {
	const auto key = _video->bigFileBaseCacheKey();
	const auto prefix = ChoosePreloadPrefix(_video);
	const auto partSize = int64(::Media::Streaming::Loader::kPartSize);
	const auto weak = base::make_weak(static_cast<has_weak_ptr*>(this));
	_video->owner().cacheBigFile().get(
		key,
		[weak, prefix, full = _full](
				const QByteArray &result) {
		const auto empty = result.isEmpty();
		const auto complete = !empty
			&& HasContiguousVideoPreloadPrefix(
				result.constData(),
				result.size(),
				full,
				prefix,
				partSize);
		crl::on_main([weak, empty, complete] {
			if (const auto strong = weak.get()) {
				const auto self = static_cast<VideoPreload*>(strong);
				if (complete) {
					self->callDone(true);
				} else if (empty) {
					self->load();
				} else {
					self->callDone(false);
				}
			}
		});
	});
}

void VideoPreload::load() {
	if (!Can(_video)) {
		callDone(false);
		return;
	}
	const auto prefix = ChoosePreloadPrefix(_video);
	Assert(prefix > 0 && prefix <= _video->size);
	const auto part = Storage::kDownloadPartSize;
	const auto parts = (prefix + part - 1) / part;
	for (auto i = 0; i != parts; ++i) {
		_parts.emplace(i * part, QByteArray());
	}
	addToQueue();
}

void VideoPreload::done(QByteArray result) {
	const auto key = _video->bigFileBaseCacheKey();
	const auto success = !result.isEmpty();
	if (!success || !key) {
		callDone(false);
		return;
	}
	Assert(result.size() < Storage::kMaxFileInMemory);
	_video->owner().cacheBigFile().putIfEmpty(
		key,
		Storage::Cache::Database::TaggedValue(std::move(result), 0));
	const auto prefix = ChoosePreloadPrefix(_video);
	const auto partSize = int64(::Media::Streaming::Loader::kPartSize);
	const auto weak = base::make_weak(static_cast<has_weak_ptr*>(this));
	_video->owner().cacheBigFile().get(key, [weak, prefix, full = _full](
			const QByteArray &cached) {
		const auto complete = HasContiguousVideoPreloadPrefix(
			cached.constData(),
			cached.size(),
			full,
			prefix,
			partSize);
		crl::on_main([weak, complete] {
			if (const auto strong = weak.get()) {
				static_cast<VideoPreload*>(strong)->callDone(complete);
			}
		});
	});
}

VideoPreload::~VideoPreload() {
	if (!_finished && !_failed) {
		cancelAllRequests();
	}
}

bool VideoPreload::Can(not_null<DocumentData*> video) {
	return video->canBeStreamed()
		&& video->videoPreloadLocation().valid()
		&& video->bigFileBaseCacheKey();
}

bool VideoPreload::readyToRequest() const {
	const auto part = Storage::kDownloadPartSize;
	return !_failed && (_nextRequestOffset < _parts.size() * part);
}

int64 VideoPreload::takeNextRequestOffset() {
	Expects(readyToRequest());

	_requestedOffsets.emplace(_nextRequestOffset);
	_nextRequestOffset += Storage::kDownloadPartSize;
	return _requestedOffsets.back();
}

bool VideoPreload::feedPart(
		int64 offset,
		const QByteArray &bytes) {
	Expects(offset < _parts.size() * Storage::kDownloadPartSize);
	Expects(_requestedOffsets.contains(int(offset)));
	Expects(bytes.size() <= Storage::kDownloadPartSize);

	const auto part = Storage::kDownloadPartSize;
	if (!HasExpectedVideoPreloadPartSize(offset, bytes.size(), _full)) {
		_failed = true;
		cancelAllRequests();
		done({});
		return false;
	}
	_requestedOffsets.remove(int(offset));
	_parts[offset] = bytes;
	if ((_nextRequestOffset + part >= _parts.size() * part)
		&& _requestedOffsets.empty()) {
		_finished = true;
		removeFromQueue();
		auto result = ForceComplexPartsMapSerialization(
			::Media::Streaming::SerializeComplexPartsMap(_parts),
			_full);
		done(std::move(result));
	}
	return true;
}

void VideoPreload::cancelOnFail() {
	_failed = true;
	cancelAllRequests();
	done({});
}

bool VideoPreload::setWebFileSizeHook(int64 size) {
	_failed = true;
	cancelAllRequests();
	done({});
	return false;
}

} // namespace Data
