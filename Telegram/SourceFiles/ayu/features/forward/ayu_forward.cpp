// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/features/forward/ayu_forward.h"

#include "apiwrap.h"
#include "lang_auto.h"
#include "ayu/features/forward/ayu_forward_rich.h"
#include "ayu/features/forward/ayu_sync.h"
#include "ayu/utils/telegram_helpers.h"
#include "base/random.h"
#include "base/unixtime.h"
#include "base/weak_ptr.h"
#include "core/application.h"
#include "data/data_channel.h"
#include "data/data_changes.h"
#include "data/data_document.h"
#include "data/data_peer.h"
#include "data/data_photo.h"
#include "data/data_session.h"
#include "history/history_item.h"
#include "main/main_session.h"
#include "storage/localimageloader.h"
#include "storage/storage_account.h"
#include "storage/storage_media_prepare.h"
#include "styles/style_boxes.h"
#include "ui/chat/attach/attach_prepare.h"
#include "ui/text/text_utilities.h"

#include <chrono>
#include <mutex>
#include <new>
#include <optional>
#include <unordered_set>

namespace AyuForward {

namespace {

// forwardStates is written from the crl::async background thread that runs
// forwardMessages/intelligentForward and read from the main thread
// (isForwarding, stateName, cancelForward), so the map itself (insertion can
// rehash) needs its own lock; the ForwardState fields it points to are
// atomic (see ayu_forward.h) and stay valid via shared_ptr regardless of
// later map mutations.
std::mutex ForwardStatesMutex;
struct ForwardStateKey {
	uint64 sessionUniqueId = 0;
	PeerId peerId;

	friend inline auto operator<=>(ForwardStateKey, ForwardStateKey) = default;
};

struct ForwardStateKeyHash {
	size_t operator()(const ForwardStateKey &value) const noexcept {
		const auto first = std::hash<uint64>()(value.sessionUniqueId);
		const auto second = std::hash<PeerId>()(value.peerId);
		return first ^ (second + 0x9e3779b97f4a7c15ULL
			+ (first << 6)
			+ (first >> 2));
	}
};

std::unordered_map<ForwardStateKey,
	std::shared_ptr<ForwardState>,
	ForwardStateKeyHash> forwardStates;
constexpr auto kMaxForwardStates = std::size_t(256);
constexpr auto kMaxForwardItems = std::size_t(10'000);

struct ForwardTargetSnapshot {
	uint64 sessionUniqueId = 0;
	PeerId peerId;
	bool slowmodeApplied = false;
};

[[nodiscard]] std::optional<ForwardTargetSnapshot> SnapshotForwardTarget(
		not_null<Main::Session*> session,
		const base::weak_ptr<History> &targetHistory) {
	auto result = std::optional<ForwardTargetSnapshot>();
	const auto weakSession = base::make_weak(session);
	crl::on_main_sync([&] {
		const auto current = weakSession.get();
		const auto history = targetHistory.get();
		if (current && history && &history->session() == current) {
			result = ForwardTargetSnapshot{
				.sessionUniqueId = current->uniqueId(),
				.peerId = history->peer->id,
				.slowmodeApplied = history->peer->slowmodeApplied(),
			};
		}
	});
	return result;
}

[[nodiscard]] std::shared_ptr<ForwardState> FindForwardState(
		const ForwardStateKey &key) {
	std::lock_guard<std::mutex> lock(ForwardStatesMutex);
	const auto it = forwardStates.find(key);
	return (it != forwardStates.end()) ? it->second : nullptr;
}

[[nodiscard]] bool SetForwardState(
			const ForwardStateKey &key,
			std::shared_ptr<ForwardState> state) {
	std::lock_guard<std::mutex> lock(ForwardStatesMutex);
	if (const auto existing = forwardStates.find(key);
		 existing != forwardStates.end()) {
		if (existing->second->state.load() != ForwardState::State::Finished) {
			return false;
		}
		forwardStates.erase(existing);
	}
	if (forwardStates.size() >= kMaxForwardStates) {
		for (auto i = forwardStates.begin(); i != forwardStates.end();) {
			if (i->second->state.load() == ForwardState::State::Finished) {
				i = forwardStates.erase(i);
			} else {
				++i;
			}
		}
		if (forwardStates.size() >= kMaxForwardStates) {
			return false;
		}
	}
	forwardStates[key] = std::move(state);
	return true;
}

struct ForwardSource {
	not_null<ChannelData*> peer;
	MsgId id = 0;
};

struct ForwardSourceCacheKey {
	uint64 sessionUniqueId = 0;
	FullMsgId fullId;

	friend inline auto operator<=>(
		ForwardSourceCacheKey,
		ForwardSourceCacheKey) = default;
};

struct ForwardSourceCacheKeyHash {
	size_t operator()(const ForwardSourceCacheKey &value) const noexcept {
		const auto first = std::hash<uint64>()(value.sessionUniqueId);
		const auto second = std::hash<FullMsgId>()(value.fullId);
		return first ^ (second + 0x9e3779b97f4a7c15ULL
			+ (first << 6)
			+ (first >> 2));
	}
};

class UnavailableForwardSources final {
public:
	static constexpr auto kMaxEntries = std::size_t(1024);

	[[nodiscard]] bool contains(const ForwardSourceCacheKey &key) const {
		const auto lock = std::lock_guard(_mutex);
		return _values.contains(key);
	}

	void erase(const ForwardSourceCacheKey &key) {
		const auto lock = std::lock_guard(_mutex);
		_values.erase(key);
	}

	void insert(const ForwardSourceCacheKey &key) {
		const auto lock = std::lock_guard(_mutex);
		if (_values.contains(key)) {
			return;
		}
		if (_values.size() >= kMaxEntries) {
			_values.erase(_values.begin());
		}
		_values.emplace(key);
	}

private:
	mutable std::mutex _mutex;
	std::unordered_set<ForwardSourceCacheKey, ForwardSourceCacheKeyHash> _values;
};

UnavailableForwardSources unavailableForwardSources;

[[nodiscard]] bool canUseForwardSource(not_null<ChannelData*> channel) {
	return channel->isPublic() || channel->amIn();
}

[[nodiscard]] bool isSourceForwardRestricted(not_null<HistoryItem*> item) {
	const auto from = item->from();
	return item->isAyuNoForwards()
		|| item->history()->peer->isAyuNoForwards()
		|| (from && from->isAyuNoForwards());
}

[[nodiscard]] ForwardSourceCacheKey forwardSourceCacheKey(
		not_null<Main::Session*> session,
		const ForwardSource &source) {
	return ForwardSourceCacheKey{
		.sessionUniqueId = session->uniqueId(),
		.fullId = FullMsgId(source.peer->id, source.id),
	};
}

void clearUnavailableForwardSource(
		not_null<Main::Session*> session,
		const ForwardSource &source) {
	unavailableForwardSources.erase(forwardSourceCacheKey(session, source));
}

void markUnavailableForwardSource(
		not_null<Main::Session*> session,
		const ForwardSource &source) {
	unavailableForwardSources.insert(forwardSourceCacheKey(session, source));
}

[[nodiscard]] std::optional<ForwardSource> forwardSourceData(
		not_null<HistoryItem*> item) {
	if (item->isDeleted()
		|| item->unsupportedTTL()
		|| (item->media() && item->media()->ttlSeconds())
		|| !isSourceForwardRestricted(item)) {
		return std::nullopt;
	}
	const auto originalSender = item->originalSender();
	const auto originalId = item->originalId();
	const auto originalChannel = originalSender
		? originalSender->asChannel()
		: nullptr;
	if (!originalChannel || !IsServerMsgId(originalId)
		|| !canUseForwardSource(originalChannel)
		|| originalSender->isAyuNoForwards()
		|| !originalSender->allowsForwarding()) {
		return std::nullopt;
	}
	const auto source = ForwardSource{
		.peer = originalChannel,
		.id = originalId,
	};
	const auto session = &item->history()->session();
	if (unavailableForwardSources.contains(
		forwardSourceCacheKey(session, source))) {
		return std::nullopt;
	}
	return source;
}

[[nodiscard]] FullMsgId resolveForwardSourceId(
		not_null<Main::Session*> session,
		FullMsgId itemId) {
	const auto weakSession = base::make_weak(session);
	if (!weakSession) {
		return {};
	}
	struct Lookup {
		FullMsgId immediate;
		PeerId sourcePeerId;
		MsgId sourceId = 0;
		MTPInputPeer historyInput;
		MsgId historyItemId = 0;
	};
	auto lookup = Lookup();
	crl::on_main_sync([&] {
		const auto current = weakSession.get();
		if (!current) {
			return;
		}
		const auto item = current->data().message(itemId);
		if (!item) {
			return;
		}
		const auto source = forwardSourceData(item);
		if (!source) {
			lookup.immediate = item->fullId();
			return;
		}
		if (const auto existing = current->data().message(
				source->peer,
				source->id)) {
			clearUnavailableForwardSource(current, *source);
			lookup.immediate = existing->fullId();
			return;
		}
		lookup.sourcePeerId = source->peer->id;
		lookup.sourceId = source->id;
		lookup.historyInput = item->history()->peer->input();
		lookup.historyItemId = item->id;
	});
	if (lookup.immediate || !lookup.sourcePeerId || !lookup.sourceId) {
		return lookup.immediate;
	}

	const auto sourcePeerId = lookup.sourcePeerId;
	const auto sourceId = lookup.sourceId;
	std::shared_ptr<TimedCountDownLatch> latch;
	try {
		latch = std::make_shared<TimedCountDownLatch>(1);
	} catch (const std::bad_alloc &) {
		return {};
	}

	crl::on_main([=] {
		const auto current = weakSession.get();
		if (!current) {
			latch->countDown();
			return;
		}
		if (!current->data().peer(sourcePeerId)->asChannel()) {
			latch->countDown();
			return;
		}
		current->api().request(MTPchannels_GetMessages(
			MTP_inputChannelFromMessage(
				lookup.historyInput,
				MTP_int(lookup.historyItemId),
				MTP_long(peerToChannel(sourcePeerId).bare)),
			MTP_vector<MTPInputMessage>(
				1,
				MTP_inputMessageID(MTP_int(sourceId)))
		)).done([=](const MTPmessages_Messages &result) {
			if (const auto current = weakSession.get()) {
				if (const auto sourcePeer = current->data().peer(
						sourcePeerId)->asChannel()) {
					current->data().processExistingMessages(sourcePeer, result);
					if (current->data().message(sourcePeerId, sourceId)) {
						clearUnavailableForwardSource(
							current,
							ForwardSource{ sourcePeer, sourceId });
					} else {
						markUnavailableForwardSource(
							current,
							ForwardSource{ sourcePeer, sourceId });
					}
				}
			}
			latch->countDown();
		}).fail([=](const MTP::Error &) {
			latch->countDown();
	}).send();
	});

	if (!latch->await(std::chrono::seconds(15))) {
		if (const auto current = weakSession.get()) {
			if (const auto original = current->data().message(itemId)) {
				return original->fullId();
			}
		}
		return {};
	}
	if (const auto current = weakSession.get()) {
		if (const auto resolved = current->data().message(sourcePeerId, sourceId)) {
			return resolved->fullId();
		}
		if (const auto original = current->data().message(itemId)) {
			return original->fullId();
		}
	}
	return {};
}

[[nodiscard]] MessageIdsList resolveForwardSources(
	not_null<Main::Session*> session,
	const MessageIdsList &itemIds) {
	auto result = MessageIdsList();
	result.reserve(itemIds.size());
	const auto weakSession = base::make_weak(session);
	for (const auto itemId : itemIds) {
		if (!weakSession) {
			break;
		}
		if (const auto resolved = resolveForwardSourceId(session, itemId)) {
			result.push_back(resolved);
		} else {
			break;
		}
	}
	return result;
}

struct ForwardMedia {
	std::optional<AyuSync::PhotoSnapshot> photo;
	std::optional<AyuSync::DocumentSnapshot> document;
	bool poll = false;
	bool downloadable = false;
};

struct ForwardItem {
	FullMsgId id;
	TextWithTags text;
	std::shared_ptr<const Iv::RichPage> richPage;
	ForwardMedia media;
	uint64 groupId = 0;
	bool invertMedia = false;
	bool needsAyuForward = false;
};

[[nodiscard]] std::vector<ForwardItem> SnapshotForwardItems(
	not_null<Main::Session*> session,
	const MessageIdsList &itemIds) {
	if (itemIds.empty() || itemIds.size() > kMaxForwardItems) {
		return {};
	}
	auto result = std::vector<ForwardItem>();
	const auto weakSession = base::make_weak(session);
	try {
		crl::on_main_sync([&] {
			const auto current = weakSession.get();
			if (!current) {
				return;
			}
			const auto items = current->data().idsToItems(itemIds);
			if (items.size() != itemIds.size()) {
				return;
			}
			result.reserve(items.size());
			for (auto i = 0; i != items.size(); ++i) {
				const auto item = items[i];
				auto snapshot = ForwardItem();
				snapshot.id = item->fullId();
				snapshot.text = extractText(item);
				snapshot.richPage = item->richPage();
				snapshot.groupId = item->groupId().value;
				snapshot.invertMedia = item->invertMedia();
				snapshot.needsAyuForward = item->isDeleted()
					|| item->unsupportedTTL()
					|| (item->media() && item->media()->ttlSeconds())
					|| isSourceForwardRestricted(item);
				if (const auto media = item->media()) {
					snapshot.media.downloadable = mediaDownloadable(media);
					snapshot.media.poll = media->poll() != nullptr;
					if (const auto photo = media->photo()) {
						const auto value = AyuSync::snapshotPhoto(
							current,
							photo);
						if (AyuSync::isValidPhotoSnapshot(value)) {
							snapshot.media.photo = value;
						}
					}
					if (const auto document = media->document()) {
						const auto value = AyuSync::snapshotDocument(document);
						if (AyuSync::isValidDocumentSnapshot(value)) {
							snapshot.media.document = value;
						}
					}
				}
				result.push_back(std::move(snapshot));
			}
		});
	} catch (...) {
		result.clear();
	}
	return result;
}

} // namespace

bool isForwarding(const Main::Session &session, const PeerId &id) {
	if (!id.value) {
		return false;
	}
	const auto state = FindForwardState({ session.uniqueId(), id });
	if (!state) {
		return false;
	}
	return state->state.load() != ForwardState::State::Finished
		&& state->currentChunk.load() < state->totalChunks
		&& !state->stopRequested.load()
		&& ((state->totalChunks && state->totalMessages.load()) || state->state.load() == ForwardState::State::Downloading);
}

void cancelForward(const PeerId &id, const Main::Session &session) {
	const auto state = FindForwardState({ session.uniqueId(), id });
	if (state) {
		state->stopRequested = true;
		state->updateBottomBar(session, &id, ForwardState::State::Finished);
	}
}

std::pair<QString, QString> stateName(
		const Main::Session &session,
		const PeerId &id) {
	const auto state = FindForwardState({ session.uniqueId(), id });

	if (!state) {
		return std::make_pair(QString(), QString());
	}

	QString messagesString = tr::ayu_AyuForwardStatusSentCount(tr::now,
															   lt_count1,
															   QString::number(state->sentMessages),
															   lt_count2,
															   QString::number(state->totalMessages)

	);

	QString chunkString = tr::ayu_AyuForwardStatusChunkCount(tr::now,
															 lt_count1,
															 QString::number(state->currentChunk + 1),
															 lt_count2,
															 QString::number(state->totalChunks)

	);

	const auto partString = state->totalChunks <= 1 ? messagesString : (messagesString + " • " + chunkString);

	QString status;

	if (state->state == ForwardState::State::Preparing) {
		status = tr::ayu_AyuForwardStatusPreparing(tr::now);
	} else if (state->state == ForwardState::State::Downloading) {
		return std::make_pair(tr::ayu_AyuForwardStatusLoadingMedia(tr::now), "");
	} else if (state->state == ForwardState::State::Sending) {
		status = tr::ayu_AyuForwardStatusForwarding(tr::now);
	} else {
		// ForwardState::State::Finished
		status = tr::ayu_AyuForwardStatusFinished(tr::now);
	}


	return std::make_pair(status, partString);
}

void ForwardState::updateBottomBar(const Main::Session &session, const PeerId *peer, const State &st) {
	state = st;
	const auto weakSession = base::make_weak(&session);
	if (!weakSession) {
		return;
	}
	auto peerCopy = *peer;
	crl::on_main([weakSession, peerCopy]
	{
		if (const auto current = weakSession.get()) {
			current->changes().peerUpdated(
				current->data().peer(peerCopy),
				Data::PeerUpdate::Flag::Rights);
		}
	});
}

static Ui::PreparedList prepareMedia(
		const std::vector<ForwardItem> &items,
		int &i,
		std::vector<ForwardMedia> &groupMedia,
		const AyuSync::DocumentPaths &documentPaths) {
	auto list = Ui::PreparedList();
	const auto append = [&](const ForwardMedia &media)
	{
		auto path = QString();
		const auto document = media.document;
		if (document) {
			const auto j = documentPaths.find(document->id);
			if (j != documentPaths.end()) {
				path = j->second;
			}
		} else if (const auto photo = media.photo) {
			path = photo->path;
		}
		auto prepared = Ui::PreparedFile(path);
		if (prepared.path.isEmpty()) {
			return;
		}
		if (document) {
			prepared.displayName = document->name;
		}
		Storage::PrepareDetails(prepared, st::sendMediaPreviewSize, PhotoSideLimit());
		groupMedia.emplace_back(media);
		list.files.emplace_back(std::move(prepared));
	};

	const auto &startItem = items[i];
	const auto &media = startItem.media;
	const auto groupId = startItem.groupId;

	append(media);

	if (!groupId) {
		return list;
	}

	for (int k = i + 1; k < items.size(); ++k) {
		const auto &nextItem = items[k];
		if (nextItem.groupId != groupId) {
			break;
		}
		if (nextItem.media.photo || nextItem.media.document) {
			append(nextItem.media);
			i = k;
		}
	}
	return list;
}

void sendMedia(
	not_null<Main::Session*> session,
	const std::shared_ptr<Ui::PreparedBundle> &bundle,
	const ForwardMedia &primaryMedia,
	Api::MessageToSend &&message,
	bool sendImagesAsPhotos,
	base::weak_ptr<History> targetHistory) {
	if (const auto document = primaryMedia.document; document && document->sticker) {
		AyuSync::sendStickerSync(
			session,
			std::move(message),
			document->id,
			targetHistory);
		return;
	}

	auto mediaType = [&]
	{
		if (const auto document = primaryMedia.document) {
			if (document->voice) {
				return SendMediaType::Audio;
			} else if (document->round) {
				return SendMediaType::Round;
			} else if (document->playable) {
				// to send video as video need to pass it as 'photo'
				// ref: `void HistoryWidget::sendingFilesConfirmed`
				return SendMediaType::Photo;
			}
			return SendMediaType::File;
		}
		return SendMediaType::Photo;
	}();

	if (mediaType == SendMediaType::Round || mediaType == SendMediaType::Audio) {
		const auto path = bundle->groups.front().list.files.front().path;
		constexpr auto kMaxInlineVoiceBytes = qint64(256) * 1024 * 1024;

		QFile file(path);
		auto failed = false;
		if (!file.open(QIODevice::ReadOnly)) {
			LOG(("failed to open file for forward with reason: %1").arg(file.errorString()));
			failed = true;
		}
		auto data = QByteArray();
		if (!failed) {
			const auto fileSize = file.size();
			if (fileSize <= 0 || fileSize > kMaxInlineVoiceBytes) {
				failed = true;
			} else {
				data = file.readAll();
				failed = data.size() != fileSize;
			}
		}
		file.close();

		if (!failed && data.size()) {
			AyuSync::sendVoiceSync(session,
								   data,
								   primaryMedia.document->duration,
								   mediaType == SendMediaType::Round,
								   std::move(message),
								   targetHistory);
			return;
		}
		// at least try to send it as squared-video
	}

	// workaround for media albums consisting of video and photos
	if (sendImagesAsPhotos) {
		mediaType = SendMediaType::Photo;
	}

	for (auto &group : bundle->groups) {
		AyuSync::sendDocumentSync(
			session,
			group,
			mediaType,
			std::move(message.textWithTags),
			message.action,
			targetHistory);
	}
}

bool isAyuForwardNeeded(const std::vector<not_null<HistoryItem*>> &items) {
	const auto needAyuForward = [&](const auto &item)
	{
		return isAyuForwardNeeded(item);
	};
	return std::ranges::any_of(items, needAyuForward);
}

bool isAyuForwardNeeded(not_null<HistoryItem*> item) {
	if (item->isDeleted()
		|| item->unsupportedTTL()
		|| (item->media() && item->media()->ttlSeconds())) {
		return true;
	}
	return isSourceForwardRestricted(item);
}

bool isFullAyuForwardNeeded(not_null<HistoryItem*> item) {
	return isSourceForwardRestricted(item) && !forwardSourceData(item);
}

bool isFullAyuForwardNeeded(
		const std::vector<not_null<HistoryItem*>> &items) {
	return !items.empty() && std::ranges::all_of(
		items,
		[](const auto item) {
			return isFullAyuForwardNeeded(item);
		});
}

struct ForwardChunk
{
	bool isAyuForwardNeeded;
	MessageIdsList itemIds;
};

void intelligentForward(
	not_null<Main::Session*> session,
	const Api::SendAction &action,
	const MessageIdsList &itemIds,
	Data::ForwardOptions options,
	base::weak_ptr<History> targetHistory) {
	const auto weakSession = base::make_weak(session);
	if (!weakSession) {
		return;
	}
	const auto target = SnapshotForwardTarget(session, targetHistory);
	if (!target) {
		return;
	}
	const auto peerId = target->peerId;
	const auto stateKey = ForwardStateKey{
		.sessionUniqueId = target->sessionUniqueId,
		.peerId = peerId,
	};
	if (itemIds.empty() || itemIds.size() > kMaxForwardItems) {
		LOG(("AyuForward: refusing an invalid or oversized selection"));
		return;
	}
	const auto topicRootId = action.replyTo.topicRootId;
	const auto monoforumPeerId = action.replyTo.monoforumPeerId;
	crl::on_main([weakSession, targetHistory, topicRootId, monoforumPeerId]
	{
		if (weakSession && targetHistory) {
			targetHistory.get()->setForwardDraft(
				topicRootId,
				monoforumPeerId,
				{});
		}
	});

	const auto resolvedIds = resolveForwardSources(session, itemIds);
	if (resolvedIds.empty() || !weakSession
		|| resolvedIds.size() != itemIds.size()
		|| resolvedIds.size() > kMaxForwardItems) {
		return;
	}
	const auto items = SnapshotForwardItems(session, resolvedIds);
	if (items.empty() || items.size() != resolvedIds.size() || !weakSession) {
		return;
	}
	auto chunks = std::vector<ForwardChunk>();
	auto currentArray = MessageIdsList();

	auto currentChunk = ForwardChunk({
		.isAyuForwardNeeded = items[0].needsAyuForward,
		.itemIds = currentArray
	});

	for (auto i = 0; i != items.size(); ++i) {
		const auto needed = items[i].needsAyuForward;
		if (needed != currentChunk.isAyuForwardNeeded) {
			currentChunk.itemIds = currentArray;
			chunks.push_back(currentChunk);

			currentArray = MessageIdsList();

			currentChunk = ForwardChunk({
				.isAyuForwardNeeded = needed,
				.itemIds = currentArray
			});
		}
		currentArray.push_back(resolvedIds[i]);
	}

	currentChunk.itemIds = currentArray;
	chunks.push_back(currentChunk);

	auto state = std::make_shared<ForwardState>(chunks.size());
	if (!SetForwardState(stateKey, state)) {
		LOG(("AyuForward: forward state capacity reached"));
		return;
	}

	for (const auto &chunk : chunks) {
		if (!weakSession || state->stopRequested) {
			state->state = ForwardState::State::Finished;
			if (const auto current = weakSession.get()) {
				state->updateBottomBar(*current, &peerId, ForwardState::State::Finished);
			}
			return;
		}
		if (chunk.isAyuForwardNeeded) {
			forwardMessages(
				session,
				action,
				true,
				chunk.itemIds,
				options,
				targetHistory);
			if (!weakSession || state->stopRequested) {
				state->state = ForwardState::State::Finished;
				if (const auto current = weakSession.get()) {
					state->updateBottomBar(*current, &peerId, ForwardState::State::Finished);
				}
				return;
			}
		} else {
			state->totalMessages = static_cast<int>(chunk.itemIds.size());
			state->sentMessages = 0;
			if (const auto current = weakSession.get()) {
				state->updateBottomBar(*current, &peerId, ForwardState::State::Sending);
			}

			AyuSync::forwardMessagesSync(
				session,
				chunk.itemIds,
				action,
				options,
				targetHistory);
			if (!weakSession || state->stopRequested) {
				state->state = ForwardState::State::Finished;
				if (const auto current = weakSession.get()) {
					state->updateBottomBar(*current, &peerId, ForwardState::State::Finished);
				}
				return;
			}

			state->sentMessages = state->totalMessages.load();

			if (const auto current = weakSession.get()) {
				state->updateBottomBar(*current, &peerId, ForwardState::State::Finished);
			}
		}
		state->currentChunk++;
	}

	state->state = ForwardState::State::Finished;
	if (const auto current = weakSession.get()) {
		state->updateBottomBar(*current, &peerId, ForwardState::State::Finished);
	}
}

void forwardMessages(
	not_null<Main::Session*> session,
	const Api::SendAction &action,
	bool forwardState,
	const MessageIdsList &itemIds,
	Data::ForwardOptions options,
	base::weak_ptr<History> targetHistory) {
	const auto weakSession = base::make_weak(session);
	if (!weakSession) {
		return;
	}
	const auto target = SnapshotForwardTarget(session, targetHistory);
	if (!target) {
		return;
	}
	const auto items = SnapshotForwardItems(session, itemIds);
	if (items.empty() || items.size() != itemIds.size()
		|| items.size() > kMaxForwardItems) {
		LOG(("AyuForward: refusing an invalid or oversized selection"));
		return;
	}
	const auto peerId = target->peerId;
	const auto stateKey = ForwardStateKey{
		.sessionUniqueId = target->sessionUniqueId,
		.peerId = peerId,
	};
	const auto slowmode = target->slowmodeApplied;

	const auto topicRootId = action.replyTo.topicRootId;
	const auto monoforumPeerId = action.replyTo.monoforumPeerId;
	crl::on_main([weakSession, targetHistory, topicRootId, monoforumPeerId]
	{
		if (weakSession && targetHistory) {
			targetHistory.get()->setForwardDraft(
				topicRootId,
				monoforumPeerId,
				{});
		}
	});

	std::shared_ptr<ForwardState> state;
	auto keepState = false;

	if (forwardState) {
		state = FindForwardState(stateKey);
		if (!state) {
			state = std::make_shared<ForwardState>(1);
			if (!SetForwardState(stateKey, state)) {
				LOG(("AyuForward: forward state capacity reached"));
				return;
			}
		} else {
			keepState = true;
		}
	} else {
		state = std::make_shared<ForwardState>(1);
		if (!SetForwardState(stateKey, state)) {
			LOG(("AyuForward: forward state capacity reached"));
			return;
		}
	}

	std::unordered_map<uint64, uint64> groupIds;

	std::vector<AyuSync::DownloadItem> toBeDownloaded;

	for (const auto &item : items) {
		if (!weakSession) {
			state->state = ForwardState::State::Finished;
			return;
		}
		if (item.media.downloadable) {
			toBeDownloaded.push_back({
				.fullId = item.id,
				.photo = item.media.photo,
				.document = item.media.document,
			});
		}

		if (item.groupId) {
			const auto currentId = groupIds.find(item.groupId);

			if (currentId == groupIds.end()) {
				groupIds[item.groupId] = base::RandomValue<uint64>();
			}
		}
	}
	state->totalMessages = static_cast<int>(items.size());
	auto documentPaths = AyuSync::DocumentPaths();
	if (!toBeDownloaded.empty()) {
		state->state = ForwardState::State::Downloading;
		if (const auto current = weakSession.get()) {
			state->updateBottomBar(*current, &peerId, ForwardState::State::Downloading);
		}
		documentPaths = AyuSync::loadDocuments(
			session,
			toBeDownloaded,
			[state] { return state->stopRequested.load(); });
	}
	if (state->stopRequested || !weakSession) {
		state->state = ForwardState::State::Finished;
		if (const auto current = weakSession.get()) {
			state->updateBottomBar(*current, &peerId, ForwardState::State::Finished);
		}
		return;
	}

	state->sentMessages = 0;
	if (const auto current = weakSession.get()) {
		state->updateBottomBar(*current, &peerId, ForwardState::State::Sending);
	}

	for (int i = 0; i < static_cast<int>(items.size()); i++) {
		if (!weakSession) {
			state->state = ForwardState::State::Finished;
			return;
		}
		const auto &item = items[i];

		if (state->stopRequested || !weakSession) {
			state->state = ForwardState::State::Finished;
			if (const auto current = weakSession.get()) {
				state->updateBottomBar(*current, &peerId, ForwardState::State::Finished);
			}
			return;
		}
		const auto updateProgress = gsl::finally([&] {
			if (state->stopRequested) {
				return;
			}
			state->sentMessages = i + 1;
			if (const auto current = weakSession.get()) {
				state->updateBottomBar(
					*current,
					&peerId,
					ForwardState::State::Sending);
			}
		});

		if (item.richPage) {
			if (const auto current = weakSession.get()) {
				state->updateBottomBar(*current, &peerId, ForwardState::State::Downloading);
			}

			const auto sent = forwardRichMessage(
				session,
				item.id,
				action,
				targetHistory,
				[state]
			{
				return state->stopRequested.load();
			});

			if (state->stopRequested || !weakSession) {
				state->state = ForwardState::State::Finished;
				if (const auto current = weakSession.get()) {
					state->updateBottomBar(*current, &peerId, ForwardState::State::Finished);
				}
				return;
			}

			if (const auto current = weakSession.get()) {
				state->updateBottomBar(*current, &peerId, ForwardState::State::Sending);
			}

			if (sent) {
				continue;
			}
		}

		const auto &extractedText = item.text;
		if (extractedText.empty() && !item.media.downloadable) {
			continue;
		}

		auto message = Api::MessageToSend(action);
		message.action.options.invertCaption = item.invertMedia;
		message.action.options.scheduled = 0;
		message.action.options.suggest = {};
		message.action.options.effectId = 0;
		message.action.replaceMediaOf = 0;

		if (options != Data::ForwardOptions::NoNamesAndCaptions) {
			message.textWithTags = extractedText;
		}

		if (!item.media.downloadable) {
			AyuSync::sendMessageSync(
				session,
				std::move(message),
				targetHistory);
		} else {
			if (item.media.poll) {
				AyuSync::sendMessageSync(
					session,
					std::move(message),
					targetHistory);
				continue;
			}

			std::vector<ForwardMedia> groupMedia;
			auto preparedMedia = prepareMedia(
				items,
				i,
				groupMedia,
				documentPaths);

			// remove not finished files
			for (auto j = int(preparedMedia.files.size()); j != 0;) {
				--j;
				const auto &file = preparedMedia.files[j];

				const auto fileInfo = QFileInfo(file.path);
				const auto photo = groupMedia[j].photo;
				const auto document = groupMedia[j].document;
				const auto incompletePhoto = photo
					&& (!AyuSync::isValidPhotoSnapshot(*photo)
						|| !fileInfo.isFile()
						|| fileInfo.size() < photo->size);
				const auto incompleteDocument = document
					&& (!AyuSync::isValidDocumentSnapshot(*document)
						|| !fileInfo.isFile()
						|| fileInfo.size() != document->size);
				if (incompletePhoto || incompleteDocument) {
					preparedMedia.files.erase(preparedMedia.files.begin() + j);
					groupMedia.erase(groupMedia.begin() + j);
				}
			}

			if (preparedMedia.files.empty()) {
				if (!message.textWithTags.empty()) {
					AyuSync::sendMessageSync(
						session,
						std::move(message),
						targetHistory);
				}
				continue;
			}

			auto way = Ui::SendFilesWay();
			way.setGroupFiles(true);
			way.setSendImagesAsPhotos(false);
			for (const auto &groupItem : groupMedia) {
				if (groupItem.photo) {
					way.setSendImagesAsPhotos(true);
					break;
				}
			}

			auto groups = Ui::DivideByGroups(
				std::move(preparedMedia),
				way,
				slowmode);

			auto bundle = Ui::PrepareFilesBundle(
				std::move(groups),
				way,
				false);
			sendMedia(
				session,
				bundle,
				groupMedia.front(),
				std::move(message),
				way.sendImagesAsPhotos(),
				targetHistory);
		}
		// if there are grouped messages
		// "i" is incremented in prepareMedia
	}
	if (!keepState) {
		state->state = ForwardState::State::Finished;
		if (const auto current = weakSession.get()) {
			state->updateBottomBar(*current, &peerId, ForwardState::State::Finished);
		}
	}
}

} // namespace AyuForward
