// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/features/forward/ayu_forward_rich.h"

#include "ayu/features/forward/ayu_forward.h"
#include "ayu/features/forward/ayu_sync.h"
#include "ayu/utils/telegram_helpers.h"
#include "base/flat_map.h"
#include "base/weak_ptr.h"
#include "data/data_document.h"
#include "data/data_file_origin.h"
#include "data/data_peer.h"
#include "data/data_photo.h"
#include "data/data_session.h"
#include "history/history.h"
#include "iv/iv_rich_message_serializer.h"
#include "iv/iv_rich_page.h"
#include "storage/localimageloader.h"

#include <atomic>
#include <cstddef>
#include <optional>

namespace AyuForward {
namespace {

using Block = Iv::RichPage::Block;
using BlockKind = Iv::RichPage::BlockKind;

constexpr auto kMaxRichDepth = std::size_t(32);
constexpr auto kMaxRichBlocks = std::size_t(4096);
constexpr auto kMaxRichChildren = std::size_t(1024);
constexpr auto kMaxRichGroupedMedia = std::size_t(128);
constexpr auto kMaxRichMediaItems = std::size_t(4096);
constexpr auto kMaxRichTextLength = std::size_t(32768);
constexpr auto kMaxRichEntities = std::size_t(4096);
constexpr auto kMaxRichAnchors = std::size_t(4096);
constexpr auto kMaxRichHtmlBytes = std::size_t(4) * 1024 * 1024;
constexpr auto kMaxRichPayloadUnits = std::size_t(4) * 1024 * 1024;

struct RichMedia
{
	base::flat_map<PhotoId, AyuSync::PhotoSnapshot> photos;
	base::flat_map<DocumentId, AyuSync::DocumentSnapshot> documents;
};

struct UploadedRichMedia
{
	base::flat_map<PhotoId, PhotoId> photos;
	base::flat_map<DocumentId, DocumentId> documents;
};

struct RichTraversalLimits {
	std::size_t blocks = 0;
	std::size_t payloadUnits = 0;
	bool exceeded = false;

	[[nodiscard]] bool enter(std::size_t depth) {
		if (depth > kMaxRichDepth || blocks >= kMaxRichBlocks) {
			exceeded = true;
			return false;
		}
		++blocks;
		return true;
	}

	[[nodiscard]] bool addPayload(std::size_t units) {
		if (units > kMaxRichPayloadUnits - payloadUnits) {
			exceeded = true;
			return false;
		}
		payloadUnits += units;
		return true;
	}
};

[[nodiscard]] PhotoData *resolvePhoto(
		not_null<Main::Session*> session,
		PhotoId id) {
	return id ? session->data().photo(id).get() : nullptr;
}

[[nodiscard]] DocumentData *resolveDocument(
		not_null<Main::Session*> session,
		DocumentId id) {
	return id ? session->data().document(id).get() : nullptr;
}

[[nodiscard]] bool isSerializableKind(BlockKind kind) {
	switch (kind) {
	case BlockKind::Unsupported:
	case BlockKind::AuthorDate:
	case BlockKind::Embed:
	case BlockKind::EmbedPost:
	case BlockKind::Channel:
	case BlockKind::RelatedArticles:
		return false;
	default:
		return true;
	}
}

[[nodiscard]] bool IsRichTextWithinLimits(const Iv::RichPage::RichText &text) {
	return text.text.text.size() <= kMaxRichTextLength
		&& text.text.entities.size() <= kMaxRichEntities
		&& text.anchorIds.size() <= kMaxRichAnchors;
}

[[nodiscard]] bool IsRichBlockWithinLimits(const Block &block) {
	if (!IsRichTextWithinLimits(block.text)
		|| !IsRichTextWithinLimits(block.caption)
		|| block.html.size() > kMaxRichHtmlBytes
		|| block.blocks.size() > kMaxRichChildren
		|| block.listItems.size() > kMaxRichChildren
		|| block.mediaItems.size() > kMaxRichGroupedMedia
		|| block.tableRows.size() > kMaxRichChildren
		|| block.relatedArticles.size() > kMaxRichChildren
		|| block.buttons.size() > kMaxRichChildren) {
		return false;
	}
	for (const auto &row : block.tableRows) {
		if (row.cells.size() > kMaxRichGroupedMedia) {
			return false;
		}
		for (const auto &cell : row.cells) {
			if (!IsRichTextWithinLimits(cell.text)) {
				return false;
			}
		}
	}
	for (const auto &item : block.listItems) {
		if (!IsRichTextWithinLimits(item.text)) {
			return false;
		}
	}
	for (const auto &button : block.buttons) {
		if (!IsRichTextWithinLimits(button.text)) {
			return false;
		}
	}
	return true;
}

[[nodiscard]] bool AccountRichBlockPayload(
	const Block &block,
	RichTraversalLimits &limits) {
	const auto accountText = [&](const Iv::RichPage::RichText &text) {
		if (!limits.addPayload(text.text.text.size())
			|| !limits.addPayload(text.anchorId.size())) {
			return false;
		}
		for (const auto &anchorId : text.anchorIds) {
			if (!limits.addPayload(anchorId.size())) {
				return false;
			}
		}
		return true;
	};
	if (!accountText(block.text)
		|| !accountText(block.caption)
		|| !limits.addPayload(block.html.size())) {
		return false;
	}
	for (const auto &item : block.listItems) {
		if (!accountText(item.text)) {
			return false;
		}
	}
	for (const auto &row : block.tableRows) {
		for (const auto &cell : row.cells) {
			if (!accountText(cell.text)) {
				return false;
			}
		}
	}
	for (const auto &button : block.buttons) {
		if (!accountText(button.text)) {
			return false;
		}
	}
	return true;
}

[[nodiscard]] bool collectMedia(
		not_null<Main::Session*> session,
		const std::vector<Block> &blocks,
		RichMedia &media,
		RichTraversalLimits &limits,
		std::size_t depth = 0) {
	const auto addPhoto = [&](PhotoId id)
	{
		if (const auto resolved = resolvePhoto(session, id)) {
			if (media.photos.contains(resolved->id)) {
				return true;
			}
			if (media.photos.size() + media.documents.size()
				>= kMaxRichMediaItems) {
				limits.exceeded = true;
				return false;
			}
			const auto snapshot = AyuSync::snapshotPhoto(session, resolved);
			if (!AyuSync::isValidPhotoSnapshot(snapshot)) {
				limits.exceeded = true;
				return false;
			}
			media.photos.emplace(resolved->id, snapshot);
		}
		return true;
	};
	const auto addDocument = [&](DocumentId id)
	{
		if (const auto resolved = resolveDocument(session, id)) {
			if (media.documents.contains(resolved->id)) {
				return true;
			}
			if (media.photos.size() + media.documents.size()
				>= kMaxRichMediaItems) {
				limits.exceeded = true;
				return false;
			}
			const auto snapshot = AyuSync::snapshotDocument(resolved);
			if (!AyuSync::isValidDocumentSnapshot(snapshot)) {
				limits.exceeded = true;
				return false;
			}
			media.documents.emplace(resolved->id, snapshot);
		}
		return true;
	};

	for (const auto &block : blocks) {
		if (!limits.enter(depth)
			|| !IsRichBlockWithinLimits(block)
			|| !AccountRichBlockPayload(block, limits)) {
			limits.exceeded = true;
			return false;
		}
		if (!isSerializableKind(block.kind)) {
			continue;
		}

		auto mediaAccepted = true;
		switch (block.kind) {
		case BlockKind::Photo:
			mediaAccepted = addPhoto(block.photoId);
			break;
		case BlockKind::Video:
		case BlockKind::Audio:
			mediaAccepted = addDocument(block.documentId);
			break;
		case BlockKind::GroupedMedia:
			for (const auto &item : block.mediaItems) {
				if (item.kind == BlockKind::Photo) {
					mediaAccepted = addPhoto(item.photoId)
						&& mediaAccepted;
				} else if (item.kind == BlockKind::Video) {
					mediaAccepted = addDocument(item.documentId)
						&& mediaAccepted;
				}
			}
			break;
		default:
			break;
		}

		if (!mediaAccepted || limits.exceeded) {
			return false;
		}
		if (!collectMedia(session, block.blocks, media, limits, depth + 1)) {
			return false;
		}
		for (const auto &item : block.listItems) {
			if (!collectMedia(
					session,
					item.blocks,
					media,
					limits,
					depth + 1)) {
				return false;
			}
		}
	}
	return !limits.exceeded;
}

[[nodiscard]] UploadedRichMedia reuploadMedia(
		not_null<Main::Session*> session,
		PeerId peerId,
		Data::FileOrigin origin,
		const RichMedia &source,
		const Fn<bool()> &cancelled) {
	auto result = UploadedRichMedia();

	const auto ensureUploaded = [&](const QString &path,
									int64 expected,
									const Fn<void()> &load,
									SendMediaType type,
									bool forceFile)
	{
		const auto ready = [&]
		{
			const auto size = AyuSync::fileSize(path);
			return size > 0 && size >= expected;
		};
		if (!ready()) {
			load();
			if (!ready()) {
				return AyuSync::UploadedFile();
			}
		}
		return AyuSync::uploadFileSync(
			session,
			peerId,
			path,
			type,
			forceFile,
			QString(),
			cancelled);
	};

	for (const auto &[id, photo] : source.photos) {
		if (cancelled()) {
			return result;
		}

		const auto uploaded = ensureUploaded(
			photo.path,
			photo.size,
			[&] {
				AyuSync::loadPhotoSync(
					session,
					photo,
					origin,
					cancelled);
			},
			SendMediaType::Photo,
			false);
		if (uploaded.photoId) {
			result.photos.emplace(id, uploaded.photoId);
		} else {
			LOG(("AyuForward: failed to transfer photo %1 for rich message").arg(id));
		}
	}

	for (const auto &[id, document] : source.documents) {
		if (cancelled()) {
			return result;
		}

		const auto video = document.video;
		const auto path = AyuSync::loadDocumentSync(
			session,
			document,
			origin,
			cancelled);
		const auto pathInfo = QFileInfo(path);
		auto uploaded = AyuSync::UploadedFile();
		if (!path.isEmpty()
			&& pathInfo.isFile()
			&& pathInfo.size() == document.size) {
			uploaded = AyuSync::uploadFileSync(
				session,
				peerId,
				path,
				SendMediaType::File,
				!video,
				document.name,
				cancelled);
		}
		if (uploaded.documentId) {
			result.documents.emplace(id, uploaded.documentId);
		} else {
			LOG(("AyuForward: failed to transfer document %1 for rich message").arg(id));
		}
	}

	return result;
}

[[nodiscard]] bool runOnMainSync(Fn<void()> callback) {
	auto latch = std::make_shared<TimedCountDownLatch>(1);
	auto completed = std::make_shared<std::atomic_bool>(false);
	crl::on_main([latch, completed, callback = std::move(callback)]
	{
		try {
			callback();
			completed->store(true, std::memory_order_release);
		} catch (...) {
			LOG(("AyuForward: main-thread rich callback failed"));
		}
		latch->countDown();
	});
	return latch->await(std::chrono::minutes(1))
		&& completed->load(std::memory_order_acquire);
}

void sanitizeRichText(Iv::RichPage::RichText &text) {
	text.anchorId = QString();
	text.anchorIds.clear();
	const auto autolink = [](const EntityInText &entity)
	{
		switch (entity.type()) {
		case EntityType::Mention:
		case EntityType::Hashtag:
		case EntityType::BotCommand:
		case EntityType::Cashtag:
		case EntityType::Url:
		case EntityType::Email:
		case EntityType::Phone:
		case EntityType::BankCard:
			return true;
		default:
			return false;
		}
	};
	const auto removed = std::ranges::remove_if(text.text.entities, autolink);
	text.text.entities.erase(removed.begin(), removed.end());
}

[[nodiscard]] bool pruneBlocks(
	not_null<Main::Session*> session,
	std::vector<Block> &blocks,
	const UploadedRichMedia &remap,
	RichTraversalLimits &limits,
	std::size_t depth);

[[nodiscard]] bool prepareBlock(
		not_null<Main::Session*> session,
		Block &block,
		const UploadedRichMedia &remap,
		RichTraversalLimits &limits,
		std::size_t depth) {
	if (!limits.enter(depth)
		|| !IsRichBlockWithinLimits(block)
		|| !AccountRichBlockPayload(block, limits)) {
		limits.exceeded = true;
		return false;
	}
	if (!isSerializableKind(block.kind)) {
		return false;
	}

	const auto remapPhoto = [&](PhotoId &id, PhotoData *&photo)
	{
		const auto resolved = resolvePhoto(session, id);
		const auto i = resolved ? remap.photos.find(resolved->id) : remap.photos.end();
		if (i == remap.photos.end()) {
			return false;
		}
		const auto replacement = session->data().photo(i->second);
		photo = replacement;
		id = replacement->id;
		return true;
	};
	const auto remapDocument = [&](DocumentId &id, DocumentData *&document)
	{
		const auto resolved = resolveDocument(session, id);
		const auto i = resolved ? remap.documents.find(resolved->id) : remap.documents.end();
		if (i == remap.documents.end()) {
			return false;
		}
		const auto replacement = session->data().document(i->second);
		document = replacement;
		id = replacement->id;
		return true;
	};

	switch (block.kind) {
	case BlockKind::Photo:
		if (!remapPhoto(block.photoId, block.photo)) {
			return false;
		}
		break;
	case BlockKind::Video:
	case BlockKind::Audio:
		if (!remapDocument(block.documentId, block.document)) {
			return false;
		}
		break;
	case BlockKind::GroupedMedia: {
		auto &items = block.mediaItems;
		for (auto i = items.begin(); i != items.end();) {
			auto kept = false;
			if (i->kind == BlockKind::Photo) {
				kept = remapPhoto(i->photoId, i->photo);
			} else if (i->kind == BlockKind::Video) {
				kept = remapDocument(i->documentId, i->document);
			}
			i = kept ? (i + 1) : items.erase(i);
		}
		if (items.empty()) {
			return false;
		}
		break;
	}
	case BlockKind::Anchor:
		if (block.anchorId.isEmpty()) {
			return false;
		}
		break;
	case BlockKind::Quote:
		if (block.pullquote && !block.blocks.empty()) {
			return false;
		}
		break;
	case BlockKind::Code:
		if (!block.blocks.empty()) {
			return false;
		}
		break;
	case BlockKind::Map:
		if (block.zoom <= 0) {
			return false;
		}
		break;
	default:
		break;
	}

	sanitizeRichText(block.text);
	sanitizeRichText(block.caption);
	if (block.kind != BlockKind::Anchor) {
		block.anchorId = QString();
	}
	for (auto &row : block.tableRows) {
		for (auto &cell : row.cells) {
			sanitizeRichText(cell.text);
		}
	}

	if (!pruneBlocks(session, block.blocks, remap, limits, depth + 1)) {
		return false;
	}
	for (auto &item : block.listItems) {
		sanitizeRichText(item.text);
		item.anchorId = QString();
		if (!pruneBlocks(session, item.blocks, remap, limits, depth + 1)) {
			return false;
		}
	}
	return true;
}


[[nodiscard]] bool pruneBlocks(
	not_null<Main::Session*> session,
	std::vector<Block> &blocks,
	const UploadedRichMedia &remap,
	RichTraversalLimits &limits,
	std::size_t depth) {
	auto write = blocks.begin();
	for (auto read = blocks.begin(); read != blocks.end(); ++read) {
		if (limits.exceeded) {
			break;
		}
		if (prepareBlock(session, *read, remap, limits, depth)) {
			if (write != read) {
				*write = std::move(*read);
			}
			++write;
		}
	}
	if (limits.exceeded) {
		return false;
	}
	blocks.erase(write, blocks.end());
	return true;
}

} // namespace

bool forwardRichMessage(
	not_null<Main::Session*> session,
	FullMsgId itemId,
	const Api::SendAction &action,
	base::weak_ptr<History> targetHistory,
	Fn<bool()> cancelled) {
	// session (and everything reached through it -- targetHistory,
	// session->data(), etc.) can be destroyed mid-flight if the user logs
	// out or switches accounts while this runs on a background thread
	// (this whole call is dispatched via crl::async with no lifetime
	// guard at the call site). Fold a liveness check into cancelled() so
	// every bail-out point already in this function -- and every deeper
	// AyuSync::*Sync call that periodically polls cancelled() -- also
	// catches that case, without needing a guard at each of them.
	const auto weakSession = base::make_weak(session);
	auto userCancelled = cancelled
		? std::move(cancelled)
		: Fn<bool()>([] { return false; });
	cancelled = [=] { return !weakSession || userCancelled(); };

	if (cancelled()) {
		return false;
	}

	const auto source = AyuSync::loadFullRichPageSync(session, itemId);
	if (!source || cancelled()) {
		return false;
	}
	const auto target = SnapshotForwardTarget(session, targetHistory);
	if (!target || cancelled()) {
		return false;
	}
	const auto peerId = target->peerId;

	struct PrepareState
	{
		Iv::RichPage page;
		RichMedia media;
		bool premiumBlocked = false;
		bool valid = true;
	};
	const auto prepared = std::make_shared<PrepareState>();

	const auto preparedOk = runOnMainSync([=]
	{
		const auto current = weakSession.get();
		if (!current || cancelled()) {
			return;
		}
		auto limits = RichTraversalLimits();
		prepared->valid = collectMedia(
			current,
			source->blocks,
			prepared->media,
			limits);
		if (!prepared->valid) {
			return;
		}
		prepared->premiumBlocked = !current->premium()
			&& Iv::RichPageUsesPremiumFormatting(*source);
		if (!prepared->premiumBlocked) {
			prepared->page = *source;
			prepared->page.part = false;
			prepared->page.views = 0;
		}
	});
	if (!preparedOk
		|| prepared->premiumBlocked
		|| !prepared->valid
		|| cancelled()) {
		return false;
	}

	const auto uploaded = reuploadMedia(
		session,
		peerId,
		itemId,
		prepared->media,
		cancelled);
	if (cancelled()) {
		return false;
	}

	const auto serialized = std::make_shared<std::optional<MTPInputRichMessage>>();
	const auto serializedOk = runOnMainSync([=]
	{
		const auto current = weakSession.get();
		if (!current || cancelled()) {
			return;
		}
		auto limits = RichTraversalLimits();
		if (!pruneBlocks(
				current,
				prepared->page.blocks,
				uploaded,
				limits,
				0)) {
			return;
		}
		if (prepared->page.blocks.empty()) {
			return;
		}
		const auto result = Iv::SerializeInputRichMessage(
			current,
			prepared->page,
			Iv::SerializeInputRichMessageMode::FinalSubmit);
		if (result.status == Iv::SerializeInputRichMessageStatus::Success) {
			*serialized = result.value;
		}
	});
	if (!serializedOk || !serialized->has_value() || cancelled()) {
		return false;
	}

	return AyuSync::sendRichMessageSync(
		session,
		**serialized,
		action,
		std::move(targetHistory));
}

} // namespace AyuForward
