// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "ayu/data/entities.h"
#include "scheme.h"

#include <QtCore/QVector>

#include <optional>
#include <vector>

class DocumentData;
class HistoryItem;
class PhotoData;

namespace AyuMapper {

inline constexpr auto kDocumentTypeNone = 0;
inline constexpr auto kDocumentTypePhoto = 1;
inline constexpr auto kDocumentTypeVideoNote = 2;
inline constexpr auto kDocumentTypeAnimation = 3;
inline constexpr auto kDocumentTypeVideo = 4;
inline constexpr auto kDocumentTypeVoice = 5;
inline constexpr auto kDocumentTypeSticker = 6;
inline constexpr auto kDocumentTypeAudio = 7;
inline constexpr auto kDocumentTypeFile = 8;
inline constexpr auto kMaxSerializedMediaBytes = std::size_t(64 * 1024);
inline constexpr auto kMaxMediaTextBytes = std::size_t(64 * 1024);
inline constexpr auto kMaxMediaPathBytes = std::size_t(4096);
inline constexpr auto kMaxMediaDimension = 32768;
inline constexpr ID kMaxMediaFileSize = ID(4) * 1024 * 1024 * 1024;

struct MediaThumbs {
	MTPVector<MTPPhotoSize> photos;
	MTPVector<MTPVideoSize> videos;
};

[[nodiscard]] std::vector<char> serializeInputPhoto(
	const MTPInputPhoto &input);
[[nodiscard]] std::vector<char> serializeInputDocument(
	const MTPInputDocument &input);
[[nodiscard]] std::optional<MTPInputPhoto> deserializeInputPhoto(
	const std::vector<char> &serialized);
[[nodiscard]] std::optional<MTPInputDocument> deserializeInputDocument(
	const std::vector<char> &serialized);
[[nodiscard]] QVector<MTPDocumentAttribute> buildDocumentAttributes(
	not_null<DocumentData*> document);
[[nodiscard]] std::vector<char> serializeDocumentAttributes(
	not_null<DocumentData*> document);
[[nodiscard]] std::optional<MTPVector<MTPDocumentAttribute>>
deserializeDocumentAttributes(const std::vector<char> &serialized);
[[nodiscard]] std::vector<char> serializeMediaThumbs(
	not_null<PhotoData*> photo);
[[nodiscard]] std::vector<char> serializeMediaThumbs(
	not_null<DocumentData*> document);
[[nodiscard]] std::optional<MediaThumbs> deserializeMediaThumbs(
	const std::vector<char> &serialized);

std::pair<std::string, std::vector<char>> serializeTextWithEntities(not_null<HistoryItem*> item);
[[nodiscard]] MTPVector<MTPMessageEntity> deserializeTextWithEntities(
	const std::vector<char> &serialized);
int mapItemFlagsToMTPFlags(not_null<HistoryItem*> item);
void mapMediaToMessage(not_null<HistoryItem*> item, AyuMessageBase &message);

} // namespace AyuMapper
