// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/utils/ayu_mapper.h"

#include "api/api_text_entities.h"
#include "apiwrap.h"
#include "data/data_document.h"
#include "data/data_media_types.h"
#include "data/data_photo.h"
#include "data/stickers/data_stickers_set.h"
#include "history/history.h"
#include "history/history_item.h"
#include "history/history_item_components.h"
#include "mtproto/connection_abstract.h"
#include "mtproto/details/mtproto_dump_to_text.h"

#include <cstring>
#include <cstdint>
#include <limits>
#include <new>
#include <optional>
#include <utility>

namespace AyuMapper {

namespace {

constexpr auto kMaxSerializedObjectBytes = std::size_t(256 * 1024);
constexpr auto kMediaThumbsMagic = std::uint32_t(0x41594D54);
constexpr auto kMediaThumbsVersion = std::uint32_t(1);
constexpr auto kMediaThumbsHeaderBytes = sizeof(std::uint32_t) * 4;
constexpr auto kMaxPhotoThumbs = std::size_t(3);
constexpr auto kMaxVideoThumbs = std::size_t(2);
constexpr auto kMaxMessageEntities = std::size_t(4096);

[[nodiscard]] bool IsBoundedMediaBytes(const QByteArray &bytes) {
	return bytes.size() <= static_cast<int>(kMaxSerializedMediaBytes);
}

[[nodiscard]] bool IsValidInputPhoto(const MTPInputPhoto &input) {
	return input.match(
		[](const MTPDinputPhoto &data) {
			return (data.vid().v != 0)
				&& (data.vaccess_hash().v != 0)
				&& IsBoundedMediaBytes(data.vfile_reference().v);
		},
		[](const MTPDinputPhotoEmpty &) {
			return false;
		});
}

[[nodiscard]] bool IsValidInputDocument(const MTPInputDocument &input) {
	return input.match(
		[](const MTPDinputDocument &data) {
			return (data.vid().v != 0)
				&& (data.vaccess_hash().v != 0)
				&& IsBoundedMediaBytes(data.vfile_reference().v);
		},
		[](const MTPDinputDocumentEmpty &) {
			return false;
		});
}

template <typename MTPObject>
[[nodiscard]] std::vector<char> SerializeObject(
		const MTPObject &object,
		std::size_t maxBytes) {
	try {
		const auto length = tl::count_length(object);
		if (!length
			|| length > maxBytes
			|| (length % sizeof(mtpPrime) != 0)) {
			LOG(("AyuMapper: Refusing to serialize an oversized MTP object"));
			return {};
		}

		mtpBuffer buffer;
		buffer.reserve(length / sizeof(mtpPrime));
		object.write(buffer);

		const auto byteCount = static_cast<std::size_t>(buffer.size())
			* sizeof(mtpPrime);
		if (byteCount != length || byteCount > maxBytes) {
			LOG(("AyuMapper: MTP object size changed during serialization"));
			return {};
		}

		std::vector<char> result(byteCount);
		std::memcpy(result.data(), buffer.constData(), byteCount);
		return result;
	} catch (const std::bad_alloc &) {
		LOG(("AyuMapper: Failed to allocate serialized MTP object"));
		return {};
	}
}

template <typename MTPObject>
[[nodiscard]] std::optional<MTPObject> DeserializeObject(
		const std::vector<char> &serialized,
		std::size_t maxBytes) {
	try {
		if (serialized.empty()
			|| serialized.size() > maxBytes
			|| (serialized.size() % sizeof(mtpPrime) != 0)) {
			LOG(("AyuMapper: Refusing to deserialize an invalid MTP object"));
			return std::nullopt;
		}

		const auto count = serialized.size() / sizeof(mtpPrime);
		auto aligned = std::vector<mtpPrime>(count);
		std::memcpy(
			aligned.data(),
			serialized.data(),
			serialized.size());

		const auto *from = aligned.data();
		const auto *end = from + count;
		MTPObject result;
		if (!result.read(from, end) || from != end) {
			LOG(("AyuMapper: Failed to deserialize an MTP object"));
			return std::nullopt;
		}
		return result;
	} catch (const std::bad_alloc &) {
		LOG(("AyuMapper: Failed to allocate deserialized MTP object"));
		return std::nullopt;
	}
}

void AppendUint32(std::vector<char> &result, std::uint32_t value) {
	const auto offset = result.size();
	result.resize(offset + sizeof(value));
	std::memcpy(result.data() + offset, &value, sizeof(value));
}

[[nodiscard]] bool ReadUint32(
		const std::vector<char> &serialized,
		std::size_t &offset,
		std::uint32_t &result) {
	if (offset > serialized.size()
		|| serialized.size() - offset < sizeof(result)) {
		return false;
	}
	std::memcpy(&result, serialized.data() + offset, sizeof(result));
	offset += sizeof(result);
	return true;
}

[[nodiscard]] int BoundedMediaDimension(int value) {
	return (value > 0 && value <= kMaxMediaDimension) ? value : 0;
}

[[nodiscard]] int BoundedMediaSize(int value) {
	return std::clamp(value, 0, std::numeric_limits<int>::max());
}

[[nodiscard]] bool IsBoundedMediaText(const QString &value) {
	return value.size() <= static_cast<int>(kMaxMediaTextBytes)
		&& value.toUtf8().size() <= static_cast<int>(kMaxMediaTextBytes);
}

[[nodiscard]] MTPVector<MTPPhotoSize> BuildPhotoThumbs(
		not_null<PhotoData*> photo) {
	auto result = QVector<MTPPhotoSize>();
	const auto add = [&](Data::PhotoSize size, const char *type) {
		const auto &location = photo->location(size);
		const auto width = BoundedMediaDimension(location.width());
		const auto height = BoundedMediaDimension(location.height());
		if (!location.valid() || !width || !height) {
			return;
		}
		result.push_back(MTP_photoSize(
			MTP_string(type),
			MTP_int(width),
			MTP_int(height),
			MTP_int(BoundedMediaSize(photo->imageByteSize(size)))));
	};
	add(Data::PhotoSize::Small, "s");
	add(Data::PhotoSize::Thumbnail, "m");
	add(Data::PhotoSize::Large, "y");
	return MTP_vector<MTPPhotoSize>(std::move(result));
}

[[nodiscard]] MTPVector<MTPVideoSize> BuildPhotoVideoThumbs(
		not_null<PhotoData*> photo) {
	auto result = QVector<MTPVideoSize>();
	const auto location = photo->videoLocation(Data::PhotoSize::Large);
	const auto width = BoundedMediaDimension(location.width());
	const auto height = BoundedMediaDimension(location.height());
	if (location.valid() && width && height) {
		result.push_back(MTP_videoSize(
			MTP_flags(0),
			MTP_string("v"),
			MTP_int(width),
			MTP_int(height),
			MTP_int(BoundedMediaSize(
				photo->videoByteSize(Data::PhotoSize::Large))),
			MTPdouble()));
	}
	return MTP_vector<MTPVideoSize>(std::move(result));
}

[[nodiscard]] MTPVector<MTPPhotoSize> BuildDocumentThumbs(
		not_null<DocumentData*> document) {
	auto result = QVector<MTPPhotoSize>();
	const auto &location = document->thumbnailLocation();
	const auto width = BoundedMediaDimension(location.width());
	const auto height = BoundedMediaDimension(location.height());
	if (location.valid() && width && height) {
		result.push_back(MTP_photoSize(
			MTP_string("m"),
			MTP_int(width),
			MTP_int(height),
			MTP_int(BoundedMediaSize(document->thumbnailByteSize()))));
	}
	return MTP_vector<MTPPhotoSize>(std::move(result));
}

[[nodiscard]] MTPVector<MTPVideoSize> BuildDocumentVideoThumbs(
		not_null<DocumentData*> document) {
	auto result = QVector<MTPVideoSize>();
	const auto &location = document->videoThumbnailLocation();
	const auto width = BoundedMediaDimension(location.width());
	const auto height = BoundedMediaDimension(location.height());
	if (location.valid() && width && height) {
		result.push_back(MTP_videoSize(
			MTP_flags(0),
			MTP_string("v"),
			MTP_int(width),
			MTP_int(height),
			MTP_int(BoundedMediaSize(document->videoThumbnailByteSize())),
			MTPdouble()));
	}
	return MTP_vector<MTPVideoSize>(std::move(result));
}

[[nodiscard]] std::vector<char> SerializeMediaThumbs(
		const MTPVector<MTPPhotoSize> &photos,
		const MTPVector<MTPVideoSize> &videos) {
	if (photos.v.size() > kMaxPhotoThumbs
		|| videos.v.size() > kMaxVideoThumbs) {
		return {};
	}
	const auto photoBytes = SerializeObject(photos, kMaxSerializedMediaBytes);
	const auto videoBytes = SerializeObject(videos, kMaxSerializedMediaBytes);
	if (photoBytes.empty() || videoBytes.empty()
		|| photoBytes.size() > std::numeric_limits<std::uint32_t>::max()
		|| videoBytes.size() > std::numeric_limits<std::uint32_t>::max()
		|| photoBytes.size() > kMaxSerializedMediaBytes - kMediaThumbsHeaderBytes
		|| videoBytes.size() > kMaxSerializedMediaBytes
			- kMediaThumbsHeaderBytes - photoBytes.size()) {
		return {};
	}

	auto result = std::vector<char>();
	result.reserve(kMediaThumbsHeaderBytes + photoBytes.size() + videoBytes.size());
	AppendUint32(result, kMediaThumbsMagic);
	AppendUint32(result, kMediaThumbsVersion);
	AppendUint32(result, static_cast<std::uint32_t>(photoBytes.size()));
	AppendUint32(result, static_cast<std::uint32_t>(videoBytes.size()));
	result.insert(result.end(), photoBytes.begin(), photoBytes.end());
	result.insert(result.end(), videoBytes.begin(), videoBytes.end());
	return result;
}

[[nodiscard]] QVector<MTPDocumentAttribute> BuildDocumentAttributes(
		not_null<DocumentData*> document) {
	auto result = QVector<MTPDocumentAttribute>();
	const auto toMtpInt = [](int64 value) {
		return int(std::clamp<int64>(
			value,
			0,
			std::numeric_limits<int>::max()));
	};
	const auto filename = document->filename();
	if (!filename.isEmpty() && IsBoundedMediaText(filename)) {
		result.push_back(MTP_documentAttributeFilename(MTP_string(filename)));
	}

	const auto width = BoundedMediaDimension(document->dimensions.width());
	const auto height = BoundedMediaDimension(document->dimensions.height());
	if (width && height) {
		if (document->hasDuration()
			&& !document->hasMimeType(u"image/gif"_q)) {
			auto flags = MTPDdocumentAttributeVideo::Flags(0);
			using VideoFlag = MTPDdocumentAttributeVideo::Flag;
			if (document->isVideoMessage()) {
				flags |= VideoFlag::f_round_message;
			}
			if (document->supportsStreaming()) {
				flags |= VideoFlag::f_supports_streaming;
			}
			result.push_back(MTP_documentAttributeVideo(
				MTP_flags(flags),
				MTP_double(std::max(document->duration(), int64(0)) / 1000.),
				MTP_int(width),
				MTP_int(height),
				MTPint(),
				MTPdouble(),
				MTPstring()));
		} else {
			result.push_back(MTP_documentAttributeImageSize(
				MTP_int(width),
				MTP_int(height)));
		}
	} else if (document->hasDuration()
		&& (document->isVideoFile() || document->isVideoMessage())) {
		auto flags = MTPDdocumentAttributeVideo::Flags(0);
		using VideoFlag = MTPDdocumentAttributeVideo::Flag;
		if (document->isVideoMessage()) {
			flags |= VideoFlag::f_round_message;
		}
		if (document->supportsStreaming()) {
			flags |= VideoFlag::f_supports_streaming;
		}
		result.push_back(MTP_documentAttributeVideo(
			MTP_flags(flags),
			MTP_double(std::max(document->duration(), int64(0)) / 1000.),
			MTP_int(0),
			MTP_int(0),
			MTPint(),
			MTPdouble(),
			MTPstring()));
	}

	if (document->type == AnimatedDocument) {
		result.push_back(MTP_documentAttributeAnimated());
	} else if (document->type == StickerDocument) {
		if (const auto sticker = document->sticker()) {
			if (IsBoundedMediaText(sticker->alt)) {
				result.push_back(MTP_documentAttributeSticker(
					MTP_flags(0),
					MTP_string(sticker->alt),
					Data::InputStickerSet(sticker->set),
					MTPMaskCoords()));
			}
		}
	} else if (const auto song = document->song()) {
		if (IsBoundedMediaText(song->title)
			&& IsBoundedMediaText(song->performer)) {
			const auto flags = MTPDdocumentAttributeAudio::Flag::f_title
				| MTPDdocumentAttributeAudio::Flag::f_performer;
			result.push_back(MTP_documentAttributeAudio(
				MTP_flags(flags),
				MTP_int(toMtpInt(document->duration() / 1000)),
				MTP_string(song->title),
				MTP_string(song->performer),
				MTPstring()));
		}
	} else if (document->voice()) {
		result.push_back(MTP_documentAttributeAudio(
			MTP_flags(MTPDdocumentAttributeAudio::Flag::f_voice),
			MTP_int(toMtpInt(document->duration() / 1000)),
			MTPstring(),
			MTPstring(),
			MTPbytes()));
	}
	return result;
}

[[nodiscard]] int MapDocumentType(not_null<DocumentData*> document) {
	if (document->isVideoMessage()) {
		return kDocumentTypeVideoNote;
	} else if (document->isAnimation()) {
		return kDocumentTypeAnimation;
	} else if (document->isVideoFile()) {
		return kDocumentTypeVideo;
	} else if (document->isVoiceMessage()) {
		return kDocumentTypeVoice;
	} else if (document->sticker()) {
		return kDocumentTypeSticker;
	} else if (document->isAudioFile()) {
		return kDocumentTypeAudio;
	}
	return kDocumentTypeFile;
}

} // namespace

constexpr auto kMessageFlagUnread = 0x00000001;
constexpr auto kMessageFlagOut = 0x00000002;
constexpr auto kMessageFlagForwarded = 0x00000004;
constexpr auto kMessageFlagReply = 0x00000008;
constexpr auto kMessageFlagMention = 0x00000010;
constexpr auto kMessageFlagContentUnread = 0x00000020;
constexpr auto kMessageFlagHasMarkup = 0x00000040;
constexpr auto kMessageFlagHasEntities = 0x00000080;
constexpr auto kMessageFlagHasFromId = 0x00000100;
constexpr auto kMessageFlagHasMedia = 0x00000200;
constexpr auto kMessageFlagHasViews = 0x00000400;
constexpr auto kMessageFlagHasBotId = 0x00000800;
constexpr auto kMessageFlagIsSilent = 0x00001000;
constexpr auto kMessageFlagIsPost = 0x00004000;
constexpr auto kMessageFlagEdited = 0x00008000;
constexpr auto kMessageFlagHasPostAuthor = 0x00010000;
constexpr auto kMessageFlagIsGrouped = 0x00020000;
constexpr auto kMessageFlagFromScheduled = 0x00040000;
constexpr auto kMessageFlagHasReactions = 0x00100000;
constexpr auto kMessageFlagHideEdit = 0x00200000;
constexpr auto kMessageFlagRestricted = 0x00400000;
constexpr auto kMessageFlagHasReplies = 0x00800000;
constexpr auto kMessageFlagIsPinned = 0x01000000;
constexpr auto kMessageFlagHasTTL = 0x02000000;
constexpr auto kMessageFlagInvertMedia = 0x08000000;
constexpr auto kMessageFlagHasSavedPeer = 0x10000000;

std::pair<std::string, std::vector<char>> serializeTextWithEntities(not_null<HistoryItem*> item) {
	if (item->emptyText()) {
		return std::make_pair("", std::vector<char>());
	}
	auto textWithEntities = item->originalText();
	if (!IsBoundedMediaText(textWithEntities.text)) {
		LOG(("AyuMapper: Refusing to archive oversized message text"));
		return std::make_pair("", std::vector<char>());
	}
	const auto text = textWithEntities.text.toUtf8();

	std::vector<char> entities;
	if (!textWithEntities.entities.empty()) {
		if (textWithEntities.entities.size() > kMaxMessageEntities) {
			LOG(("AyuMapper: Refusing to archive too many message entities"));
			return std::make_pair("", std::vector<char>());
		}
		const auto mtpEntities = Api::EntitiesToMTP(
			&item->history()->session(),
			textWithEntities.entities,
			Api::ConvertOption::WithLocal);

		entities = SerializeObject(mtpEntities, kMaxSerializedObjectBytes);
	}

	return std::make_pair(text.toStdString(), entities);
}

std::vector<char> serializeInputPhoto(const MTPInputPhoto &input) {
	return IsValidInputPhoto(input)
		? SerializeObject(input, kMaxSerializedMediaBytes)
		: std::vector<char>();
}

std::vector<char> serializeInputDocument(const MTPInputDocument &input) {
	return IsValidInputDocument(input)
		? SerializeObject(input, kMaxSerializedMediaBytes)
		: std::vector<char>();
}

std::optional<MTPInputPhoto> deserializeInputPhoto(
		const std::vector<char> &serialized) {
	auto result = DeserializeObject<MTPInputPhoto>(
		serialized,
		kMaxSerializedMediaBytes);
	if (!result || !IsValidInputPhoto(*result)) {
		return std::nullopt;
	}
	return result;
}

std::optional<MTPInputDocument> deserializeInputDocument(
		const std::vector<char> &serialized) {
	auto result = DeserializeObject<MTPInputDocument>(
		serialized,
		kMaxSerializedMediaBytes);
	if (!result || !IsValidInputDocument(*result)) {
		return std::nullopt;
	}
	return result;
}

QVector<MTPDocumentAttribute> buildDocumentAttributes(
		not_null<DocumentData*> document) {
	return BuildDocumentAttributes(document);
}

std::vector<char> serializeDocumentAttributes(
		not_null<DocumentData*> document) {
	return SerializeObject(
		MTP_vector<MTPDocumentAttribute>(BuildDocumentAttributes(document)),
		kMaxSerializedMediaBytes);
}

std::optional<MTPVector<MTPDocumentAttribute>>
deserializeDocumentAttributes(const std::vector<char> &serialized) {
	auto result = DeserializeObject<MTPVector<MTPDocumentAttribute>>(
		serialized,
		kMaxSerializedMediaBytes);
	if (!result || result->v.size() > 32) {
		return std::nullopt;
	}
	return result;
}

std::vector<char> serializeMediaThumbs(not_null<PhotoData*> photo) {
	return SerializeMediaThumbs(
		BuildPhotoThumbs(photo),
		BuildPhotoVideoThumbs(photo));
}

std::vector<char> serializeMediaThumbs(not_null<DocumentData*> document) {
	return SerializeMediaThumbs(
		BuildDocumentThumbs(document),
		BuildDocumentVideoThumbs(document));
}

std::optional<MediaThumbs> deserializeMediaThumbs(
		const std::vector<char> &serialized) {
	if (serialized.empty() || serialized.size() > kMaxSerializedMediaBytes) {
		return std::nullopt;
	}

	auto offset = std::size_t(0);
	std::uint32_t magic = 0;
	std::uint32_t version = 0;
	std::uint32_t photoBytes = 0;
	std::uint32_t videoBytes = 0;
	if (!ReadUint32(serialized, offset, magic)
		|| !ReadUint32(serialized, offset, version)
		|| !ReadUint32(serialized, offset, photoBytes)
		|| !ReadUint32(serialized, offset, videoBytes)
		|| magic != kMediaThumbsMagic
		|| version != kMediaThumbsVersion
		|| photoBytes > serialized.size() - offset
		|| videoBytes > serialized.size() - offset - photoBytes
		|| offset + photoBytes + videoBytes != serialized.size()) {
		return std::nullopt;
	}

	auto photosSerialized = std::vector<char>(
		serialized.begin() + offset,
		serialized.begin() + offset + photoBytes);
	offset += photoBytes;
	auto videosSerialized = std::vector<char>(
		serialized.begin() + offset,
		serialized.end());

	auto photos = DeserializeObject<MTPVector<MTPPhotoSize>>(
		photosSerialized,
		kMaxSerializedMediaBytes);
	auto videos = DeserializeObject<MTPVector<MTPVideoSize>>(
		videosSerialized,
		kMaxSerializedMediaBytes);
	if (!photos || !videos
		|| photos->v.size() > kMaxPhotoThumbs
		|| videos->v.size() > kMaxVideoThumbs) {
		return std::nullopt;
	}
	return MediaThumbs{ std::move(*photos), std::move(*videos) };
}

MTPVector<MTPMessageEntity> deserializeTextWithEntities(
		const std::vector<char> &serialized) {
	auto result = DeserializeObject<MTPVector<MTPMessageEntity>>(
		serialized,
		kMaxSerializedObjectBytes);
	if (!result || result->v.size() > kMaxMessageEntities) {
		return MTPVector<MTPMessageEntity>();
	}
	return std::move(*result);
}

int mapItemFlagsToMTPFlags(not_null<HistoryItem*> item) {
	int flags = 0;

	const auto thread = item->topic()
							? reinterpret_cast<Data::Thread*>(item->topic())
							: item->history();
	if (item->unread(thread)) {
		flags |= kMessageFlagUnread;
	}

	if (item->out()) {
		flags |= kMessageFlagOut;
	}

	if (item->Get<HistoryMessageForwarded>()) {
		flags |= kMessageFlagForwarded;
	}

	if (item->Get<HistoryMessageReply>()) {
		flags |= kMessageFlagReply;
	}

	if (item->mentionsMe()) {
		flags |= kMessageFlagMention;
	}

	if (item->hasUnreadMediaFlag()) {
		flags |= kMessageFlagContentUnread;
	}

	if (item->definesReplyKeyboard()) {
		flags |= kMessageFlagHasMarkup;
	}

	if (!item->originalText().entities.empty()) {
		flags |= kMessageFlagHasEntities;
	}

	if (item->displayFrom()) {
		// todo: maybe wrong
		flags |= kMessageFlagHasFromId;
	}

	if (item->media()) {
		flags |= kMessageFlagHasMedia;
	}

	if (item->hasViews()) {
		flags |= kMessageFlagHasViews;
	}

	if (item->viaBot()) {
		flags |= kMessageFlagHasBotId;
	}

	if (item->isSilent()) {
		flags |= kMessageFlagIsSilent;
	}

	if (item->isPost()) {
		flags |= kMessageFlagIsPost;
	}

	if (item->Get<HistoryMessageEdited>()) {
		flags |= kMessageFlagEdited;
	}

	if (item->Get<HistoryMessageSigned>()) {
		flags |= kMessageFlagHasPostAuthor;
	}

	if (item->groupId()) {
		flags |= kMessageFlagIsGrouped;
	}

	if (item->isScheduled()) {
		flags |= kMessageFlagFromScheduled;
	}

	if (!item->reactions().empty()) {
		flags |= kMessageFlagHasReactions;
	}

	if (item->hideEditedBadge()) {
		flags |= kMessageFlagHideEdit;
	}

	if (item->hasPossibleRestrictions()) {
		flags |= kMessageFlagRestricted;
	}

	if (item->repliesCount() > 0) {
		flags |= kMessageFlagHasReplies;
	}

	if (item->isPinned()) {
		flags |= kMessageFlagIsPinned;
	}

	if (item->ttlDestroyAt() > 0) {
		flags |= kMessageFlagHasTTL;
	}

	if (item->invertMedia()) {
		flags |= kMessageFlagInvertMedia;
	}

	if (item->savedFromSender()) {
		// todo: maybe wrong
		flags |= kMessageFlagHasSavedPeer;
	}

	return flags;
}

void mapMediaToMessage(not_null<HistoryItem*> item, AyuMessageBase &message) {
	message.mediaPath.clear();
	message.hqThumbPath.clear();
	message.documentType = kDocumentTypeNone;
	message.mediaDc = 0;
	message.mediaSize = 0;
	message.documentSerialized.clear();
	message.thumbsSerialized.clear();
	message.documentAttributesSerialized.clear();
	message.mimeType.clear();

	const auto media = item->media();
	if (!media) {
		return;
	}

	if (const auto photo = media->photo()) {
		const auto input = photo->mtpInput();
		if (photo->getDC() <= 0
			|| photo->date() == 0
			|| !photo->hasExact(Data::PhotoSize::Large)
			|| !IsValidInputPhoto(input)) {
			return;
		}

		auto serialized = serializeInputPhoto(input);
		if (serialized.empty()) {
			return;
		}
		auto thumbs = serializeMediaThumbs(photo);
		if (thumbs.empty()) {
			return;
		}
		message.documentType = kDocumentTypePhoto;
		message.mediaDc = photo->getDC();
		message.documentSerialized = std::move(serialized);
		message.thumbsSerialized = std::move(thumbs);
		return;
	}

	const auto document = media->document();
	if (!document
		|| !document->hasRemoteLocation()
		|| !document->id
		|| (document->getDC() <= 0)
		|| (document->date <= 0)
		|| (document->size < 0)
		|| (document->size > kMaxMediaFileSize)) {
		return;
	}

	const auto input = document->mtpInput();
	if (!IsValidInputDocument(input)) {
		return;
	}
	auto serialized = serializeInputDocument(input);
	if (serialized.empty()) {
		return;
	}

	const auto mime = document->mimeString();
	if (!IsBoundedMediaText(mime)) {
		return;
	}
	const auto mimeBytes = mime.toUtf8();

	message.documentType = MapDocumentType(document);
	message.mediaDc = document->getDC();
	message.mediaSize = document->size;
	message.documentSerialized = std::move(serialized);
	message.thumbsSerialized = serializeMediaThumbs(document);
	message.documentAttributesSerialized = serializeDocumentAttributes(document);
	message.mimeType.assign(mimeBytes.constData(), mimeBytes.size());

	const auto &location = document->location(true);
	if (!location.isEmpty()) {
		const auto pathName = location.name();
		if (pathName.size() <= static_cast<int>(kMaxMediaPathBytes)) {
			const auto path = pathName.toUtf8();
			if (path.size() <= static_cast<int>(kMaxMediaPathBytes)) {
				message.mediaPath.assign(path.constData(), path.size());
			}
		}
	}
}

}
