// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/features/forward/ayu_forward.h"
#include "ayu/features/forward/ayu_forward_source.h"

#include "api/api_sending.h"
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
#include "ui/toast/toast.h"

#include <algorithm>
#include <atomic>
#include <memory>
#include <mutex>
#include <optional>
#include <unordered_map>

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

class IntelligentForwardCompletion final {
public:
	explicit IntelligentForwardCompletion(
			Fn<void(Api::ForwardMessagesResult)> callback)
	: _callback(std::move(callback)) {
	}

	void finish(Api::ForwardMessagesResult result) {
		if (!_finished.exchange(true) && _callback) {
			_callback(result);
		}
	}

private:
	std::atomic<bool> _finished = false;
	Fn<void(Api::ForwardMessagesResult)> _callback;
};

[[nodiscard]] bool IsForwardStopped(
		const base::weak_ptr<Main::Session> &weakSession,
		const ForwardState &state) {
	return !weakSession || state.stopRequested.load();
}

void FinishForward(
		const std::shared_ptr<ForwardState> &state,
		const PeerId &peerId) {
	state->state = ForwardState::State::Finished;
	state->updateBottomBar(&peerId, ForwardState::State::Finished);
}

void ShowForwardStartFailure(
		const base::weak_ptr<Main::Session> &weakSession) {
	crl::on_main([weakSession] {
		if (weakSession) {
			Ui::Toast::Show(tr::ayu_AyuForwardUnavailable(tr::now));
		}
	});
}

[[nodiscard]] std::shared_ptr<ForwardState> FindForwardState(
		const ForwardStateKey &key) {
	std::lock_guard<std::mutex> lock(ForwardStatesMutex);
	const auto it = forwardStates.find(key);
	if (it == forwardStates.end()) {
		return nullptr;
	}
	const auto owner = it->second->owner.get();
	if (!owner) {
		forwardStates.erase(it);
		return nullptr;
	}
	return it->second;
}

[[nodiscard]] bool SetForwardState(
		const ForwardStateKey &key,
		std::shared_ptr<ForwardState> state) {
	std::lock_guard<std::mutex> lock(ForwardStatesMutex);
	if (const auto existing = forwardStates.find(key);
		existing != forwardStates.end()) {
		const auto owner = existing->second->owner.get();
		if (owner
			&& existing->second->state.load()
				!= ForwardState::State::Finished) {
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
	const base::weak_ptr<Main::Session> &session,
	const MessageIdsList &itemIds) {
	if (itemIds.empty() || itemIds.size() > kMaxForwardItems) {
		return {};
	}
	auto result = std::vector<ForwardItem>();
	const auto weakSession = session;
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

std::optional<ForwardTargetSnapshot> SnapshotForwardTarget(
	const base::weak_ptr<Main::Session> &session,
	const base::weak_ptr<History> &targetHistory) {
	auto result = std::optional<ForwardTargetSnapshot>();
	const auto weakSession = session;
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

bool isForwarding(const Main::Session &session, const PeerId &id) {
	if (!id.value) {
		return false;
	}
	const auto state = FindForwardState({ session.uniqueId(), id });
	if (!state) {
		return false;
	}
	const auto progress = state->progress();
	const auto currentState = state->state.load();
	return currentState != ForwardState::State::Finished
		&& progress.currentChunk < state->totalChunks
		&& !state->stopRequested.load()
		&& ((state->totalChunks && progress.totalMessages)
			|| currentState == ForwardState::State::Downloading);
}

void cancelForward(const PeerId &id, const Main::Session &session) {
	const auto state = FindForwardState({ session.uniqueId(), id });
	if (state) {
		state->stopRequested = true;
		state->updateBottomBar(&id, ForwardState::State::Finished);
	}
}

std::pair<QString, QString> stateName(
		const Main::Session &session,
		const PeerId &id) {
	const auto state = FindForwardState({ session.uniqueId(), id });

	if (!state) {
		return std::make_pair(QString(), QString());
	}
	const auto progress = state->progress();
	const auto currentState = state->state.load();

	QString messagesString = tr::ayu_AyuForwardStatusSentCount(tr::now,
												   lt_count1,
												   QString::number(progress.sentMessages),
												   lt_count2,
												   QString::number(progress.totalMessages)

	);

	QString chunkString = tr::ayu_AyuForwardStatusChunkCount(tr::now,
												 lt_count1,
												 QString::number(progress.currentChunk + 1),
												 lt_count2,
												 QString::number(state->totalChunks)

	);

	const auto partString = state->totalChunks <= 1 ? messagesString : (messagesString + " • " + chunkString);

	QString status;

	if (currentState == ForwardState::State::Preparing) {
		status = tr::ayu_AyuForwardStatusPreparing(tr::now);
	} else if (currentState == ForwardState::State::Downloading) {
		return std::make_pair(tr::ayu_AyuForwardStatusLoadingMedia(tr::now), "");
	} else if (currentState == ForwardState::State::Sending) {
		status = tr::ayu_AyuForwardStatusForwarding(tr::now);
	} else {
		// ForwardState::State::Finished
		status = tr::ayu_AyuForwardStatusFinished(tr::now);
	}


	return std::make_pair(status, partString);
}

void ForwardState::updateBottomBar(const PeerId *peer, const State &st) {
	auto previous = state.load();
	while (previous != st) {
		if (previous == State::Finished && st != State::Finished) {
			return;
		}
		if (state.compare_exchange_weak(previous, st)) {
			break;
		}
	}
	const auto weakSession = owner;
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
		const AyuSync::DocumentPaths &documentPaths,
		int &groupItemCount,
		int &expectedMediaCount,
		bool &allPrepared) {
	auto list = Ui::PreparedList();
	const auto append = [&](const ForwardMedia &media)
	{
		if (media.downloadable || media.photo || media.document) {
			++expectedMediaCount;
		}
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
		if (prepared.path.isEmpty()
			|| (!document && !media.photo)) {
			allPrepared = false;
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
		++groupItemCount;
		if (nextItem.media.downloadable
			|| nextItem.media.photo
			|| nextItem.media.document) {
			append(nextItem.media);
			i = k;
		}
	}
	return list;
}

[[nodiscard]] AyuSync::SendSyncResult sendMedia(
	const base::weak_ptr<Main::Session> &session,
	const std::shared_ptr<Ui::PreparedBundle> &bundle,
	const ForwardMedia &primaryMedia,
	Api::MessageToSend &&message,
	bool sendImagesAsPhotos,
	base::weak_ptr<History> targetHistory,
		const Fn<bool()> &cancelled) {
	if (cancelled && cancelled()) {
		return AyuSync::SendSyncResult::Indeterminate;
	}
	if (!bundle || bundle->groups.empty()) {
		return AyuSync::SendSyncResult::Failed;
	}
	if (const auto document = primaryMedia.document; document && document->sticker) {
		return AyuSync::sendStickerSync(
			session,
			std::move(message),
			document->id,
			targetHistory,
			cancelled);
	}

	auto mediaType = [&]
	{
		if (const auto document = primaryMedia.document) {
			if (document->voice) {
				return SendMediaType::Audio;
			} else if (document->round) {
				return SendMediaType::Round;
			} else if (document->video) {
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
		if (cancelled && cancelled()) {
			return AyuSync::SendSyncResult::Indeterminate;
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

		if (cancelled && cancelled()) {
			return AyuSync::SendSyncResult::Indeterminate;
		}
		if (!failed && data.size()) {
			return AyuSync::sendVoiceSync(
				session,
				data,
				primaryMedia.document->duration,
				mediaType == SendMediaType::Round,
				std::move(message),
				targetHistory,
				cancelled);
		}
		// at least try to send it as squared-video
	}

	// workaround for media albums consisting of video and photos
	if (sendImagesAsPhotos) {
		mediaType = SendMediaType::Photo;
	}

	auto sentAnyGroup = false;
	for (auto &group : bundle->groups) {
		if (cancelled && cancelled()) {
			return AyuSync::SendSyncResult::Indeterminate;
		}
		const auto result = AyuSync::sendDocumentSync(
			session,
			group,
			mediaType,
			std::move(message.textWithTags),
			message.action,
			targetHistory,
			cancelled);
		if (result != AyuSync::SendSyncResult::Succeeded) {
			return (sentAnyGroup
				&& result == AyuSync::SendSyncResult::Failed)
				? AyuSync::SendSyncResult::Indeterminate
				: result;
		}
		sentAnyGroup = true;
	}
	return AyuSync::SendSyncResult::Succeeded;
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

Api::ForwardMessagesResult ContinueIntelligentForward(
		const base::weak_ptr<Main::Session> &session,
		const Api::SendAction &action,
		MessageIdsList resolvedIds,
		Data::ForwardOptions options,
		base::weak_ptr<History> targetHistory,
		const Fn<bool()> &cancelled) {
	const auto weakSession = session;
	if (!weakSession) {
		return Api::ForwardMessagesResult::Failed;
	}
	if (cancelled && cancelled()) {
		return Api::ForwardMessagesResult::Indeterminate;
	}
	const auto target = SnapshotForwardTarget(session, targetHistory);
	if (!target || resolvedIds.empty()
		|| resolvedIds.size() > kMaxForwardItems) {
		return Api::ForwardMessagesResult::Failed;
	}
	const auto peerId = target->peerId;
	const auto stateKey = ForwardStateKey{
		.sessionUniqueId = target->sessionUniqueId,
		.peerId = peerId,
	};
	const auto items = SnapshotForwardItems(session, resolvedIds);
	if (items.empty() || items.size() != resolvedIds.size() || !weakSession) {
		return Api::ForwardMessagesResult::Failed;
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

	auto state = std::make_shared<ForwardState>(chunks.size(), session);
	if (!SetForwardState(stateKey, state)) {
		LOG(("AyuForward: forward state capacity reached"));
		ShowForwardStartFailure(weakSession);
		return Api::ForwardMessagesResult::Failed;
	}
	const auto finishState = gsl::finally([&] {
		if (state->state.load() != ForwardState::State::Finished) {
			FinishForward(state, peerId);
		}
	});
	const auto isStopped = [weakSession, state, cancelled] {
		return IsForwardStopped(weakSession, *state)
			|| (cancelled && cancelled());
	};

	auto hasUntrackedSends = false;
	auto hasSucceeded = false;
	for (auto chunkIndex = 0;
		chunkIndex != static_cast<int>(chunks.size());
		++chunkIndex) {
		const auto &chunk = chunks[chunkIndex];
		if (isStopped()) {
			FinishForward(state, peerId);
			return Api::ForwardMessagesResult::Indeterminate;
		}
		state->setProgress({
			.currentChunk = chunkIndex,
			.totalMessages = static_cast<int>(chunk.itemIds.size()),
			.sentMessages = 0,
		});
		if (chunk.isAyuForwardNeeded) {
			const auto result = forwardMessages(
				session,
				action,
				true,
				chunk.itemIds,
				options,
				targetHistory,
				true,
				cancelled);
			if (result == Api::ForwardMessagesResult::Failed) {
				FinishForward(state, peerId);
				return hasSucceeded || hasUntrackedSends
					? Api::ForwardMessagesResult::Indeterminate
					: Api::ForwardMessagesResult::Failed;
			}
			hasUntrackedSends = hasUntrackedSends
				|| (result == Api::ForwardMessagesResult::Indeterminate);
			hasSucceeded = hasSucceeded
				|| (result == Api::ForwardMessagesResult::Succeeded);
			if (isStopped()) {
				FinishForward(state, peerId);
				return Api::ForwardMessagesResult::Indeterminate;
			}
		} else {
			state->updateBottomBar(&peerId, ForwardState::State::Sending);

			const auto result = AyuSync::forwardMessagesSync(
				session,
				chunk.itemIds,
				action,
				options,
				targetHistory,
				[isStopped] {
					return isStopped();
				},
				[state, peerId](Api::SendResult requestResult, int messageCount) {
					if (requestResult == Api::SendResult::Succeeded) {
						state->addSentMessages(messageCount);
					}
					state->updateBottomBar(
						&peerId,
						ForwardState::State::Sending);
				});
			if (result != AyuSync::ForwardMessagesSyncResult::Succeeded) {
				FinishForward(state, peerId);
				return result == AyuSync::ForwardMessagesSyncResult::Failed
					&& !hasSucceeded
					&& !hasUntrackedSends
					? Api::ForwardMessagesResult::Failed
					: Api::ForwardMessagesResult::Indeterminate;
			}
			if (isStopped()) {
				FinishForward(state, peerId);
				return Api::ForwardMessagesResult::Indeterminate;
			}
			const auto progress = state->progress();
			if (progress.sentMessages != progress.totalMessages) {
				FinishForward(state, peerId);
				return Api::ForwardMessagesResult::Indeterminate;
			}
			hasSucceeded = true;

			state->updateBottomBar(&peerId, ForwardState::State::Sending);
		}
	}

	FinishForward(state, peerId);
	return hasUntrackedSends
		? Api::ForwardMessagesResult::Indeterminate
		: Api::ForwardMessagesResult::Succeeded;
}

void intelligentForward(
		const base::weak_ptr<Main::Session> &session,
		const Api::SendAction &action,
		const MessageIdsList &itemIds,
		Data::ForwardOptions options,
		base::weak_ptr<History> targetHistory,
		Fn<void(Api::ForwardMessagesResult)> done,
		Fn<bool()> cancelled) {
	const auto completion = std::make_shared<IntelligentForwardCompletion>(
		std::move(done));
	const auto weakSession = session;
	if (!weakSession) {
		completion->finish(Api::ForwardMessagesResult::Failed);
		return;
	}
	if (cancelled && cancelled()) {
		completion->finish(Api::ForwardMessagesResult::Indeterminate);
		return;
	}
	if (itemIds.empty() || itemIds.size() > kMaxForwardItems) {
		LOG(("AyuForward: refusing an invalid or oversized selection"));
		completion->finish(Api::ForwardMessagesResult::Failed);
		return;
	}
	if (!SnapshotForwardTarget(session, targetHistory)) {
		completion->finish(Api::ForwardMessagesResult::Failed);
		return;
	}
	const auto topicRootId = action.replyTo.topicRootId;
	const auto monoforumPeerId = action.replyTo.monoforumPeerId;
	try {
		crl::on_main([
				weakSession,
				targetHistory,
				topicRootId,
				monoforumPeerId,
				cancelled]
		{
			if ((!cancelled || !cancelled())
				&& weakSession
				&& targetHistory) {
				targetHistory.get()->setForwardDraft(
					topicRootId,
					monoforumPeerId,
					{});
			}
		});
	} catch (...) {
		completion->finish(Api::ForwardMessagesResult::Failed);
		return;
	}

	try {
		resolveForwardSources(
			session,
			MessageIdsList(itemIds),
			[completion, weakSession, action, options, targetHistory, cancelled](
					MessageIdsList resolvedIds) mutable {
				if (cancelled && cancelled()) {
					completion->finish(
						Api::ForwardMessagesResult::Indeterminate);
					return;
				}
				if (resolvedIds.empty()) {
					if (weakSession) {
						ShowForwardStartFailure(weakSession);
					}
					completion->finish(Api::ForwardMessagesResult::Failed);
					return;
				}
				try {
					crl::async([
							completion,
							weakSession,
							action,
							resolvedIds = std::move(resolvedIds),
							options,
							targetHistory,
							cancelled]() mutable {
						try {
							const auto result = ContinueIntelligentForward(
								weakSession,
								action,
								std::move(resolvedIds),
								options,
								targetHistory,
								cancelled);
							completion->finish(result);
						} catch (...) {
							ShowForwardStartFailure(weakSession);
							completion->finish(
								Api::ForwardMessagesResult::Indeterminate);
						}
					});
				} catch (...) {
					if (weakSession) {
						ShowForwardStartFailure(weakSession);
					}
					completion->finish(
						Api::ForwardMessagesResult::Indeterminate);
				}
			});
	} catch (...) {
		if (weakSession) {
			ShowForwardStartFailure(weakSession);
		}
		completion->finish(Api::ForwardMessagesResult::Failed);
	}
}

Api::ForwardMessagesResult forwardMessages(
	const base::weak_ptr<Main::Session> &session,
	const Api::SendAction &action,
	bool forwardState,
	const MessageIdsList &itemIds,
	Data::ForwardOptions options,
	base::weak_ptr<History> targetHistory,
	bool trackProgress,
	Fn<bool()> cancelled) {
	const auto weakSession = session;
	if (!weakSession) {
		return Api::ForwardMessagesResult::Failed;
	}
	if (cancelled && cancelled()) {
		return Api::ForwardMessagesResult::Indeterminate;
	}
	const auto target = SnapshotForwardTarget(session, targetHistory);
	if (!target) {
		return Api::ForwardMessagesResult::Failed;
	}
	const auto items = SnapshotForwardItems(session, itemIds);
	if (items.empty() || items.size() != itemIds.size()
		|| items.size() > kMaxForwardItems) {
		LOG(("AyuForward: refusing an invalid or oversized selection"));
		return Api::ForwardMessagesResult::Failed;
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
			state = std::make_shared<ForwardState>(1, session);
			if (!SetForwardState(stateKey, state)) {
				LOG(("AyuForward: forward state capacity reached"));
				ShowForwardStartFailure(weakSession);
				return Api::ForwardMessagesResult::Failed;
			}
		} else {
			keepState = true;
		}
	} else {
		state = std::make_shared<ForwardState>(1, session);
		if (!SetForwardState(stateKey, state)) {
			LOG(("AyuForward: forward state capacity reached"));
			ShowForwardStartFailure(weakSession);
			return Api::ForwardMessagesResult::Failed;
		}
	}
	const auto finishState = gsl::finally([&] {
		if (!keepState
			&& state->state.load() != ForwardState::State::Finished) {
			FinishForward(state, peerId);
		}
	});
	const auto isStopped = [weakSession, state, cancelled] {
		return IsForwardStopped(weakSession, *state)
			|| (cancelled && cancelled());
	};

	std::vector<AyuSync::DownloadItem> toBeDownloaded;

	for (const auto &item : items) {
		if (!weakSession) {
			FinishForward(state, peerId);
			return Api::ForwardMessagesResult::Indeterminate;
		}
		if (item.media.downloadable) {
			toBeDownloaded.push_back({
				.fullId = item.id,
				.photo = item.media.photo,
				.document = item.media.document,
			});
		}

	}
	state->setMessageProgress(static_cast<int>(items.size()), 0);
	auto documentPaths = AyuSync::DocumentPaths();
	if (!toBeDownloaded.empty()) {
		state->updateBottomBar(&peerId, ForwardState::State::Downloading);
		documentPaths = AyuSync::loadDocuments(
			session,
			toBeDownloaded,
			[isStopped] { return isStopped(); });
	}
	if (isStopped()) {
		FinishForward(state, peerId);
		return Api::ForwardMessagesResult::Indeterminate;
	}

	state->updateBottomBar(&peerId, ForwardState::State::Sending);
	const auto isCancelled = isStopped;
	auto hasSkippedItems = false;
	auto didSendAnything = false;
	auto progressSentMessages = 0;

	for (int i = 0; i < static_cast<int>(items.size()); i++) {
		if (!weakSession) {
			FinishForward(state, peerId);
			return Api::ForwardMessagesResult::Indeterminate;
		}
		const auto &item = items[i];

		if (isStopped()) {
			FinishForward(state, peerId);
			return Api::ForwardMessagesResult::Indeterminate;
		}
		auto itemSucceeded = false;
		auto itemProgressKnown = false;
		auto itemSendResult = AyuSync::SendSyncResult::Indeterminate;
		auto itemProgressCount = 1;
		const auto updateProgress = gsl::finally([&] {
			if (!trackProgress
				|| !itemSucceeded
				|| !itemProgressKnown
				|| isStopped()) {
				return;
			}
			progressSentMessages += itemProgressCount;
			state->setSentMessages(progressSentMessages);
			state->updateBottomBar(&peerId,
					ForwardState::State::Sending);
		});

		if (item.richPage) {
			state->updateBottomBar(&peerId, ForwardState::State::Downloading);

			itemSendResult = forwardRichMessage(
				session,
				item.id,
				action,
				targetHistory,
				[isStopped] {
					return isStopped();
				});
			itemSucceeded = itemSendResult
				== AyuSync::SendSyncResult::Succeeded;
			itemProgressKnown = itemSucceeded;

			if (isStopped()) {
				FinishForward(state, peerId);
				return Api::ForwardMessagesResult::Indeterminate;
			}

			state->updateBottomBar(&peerId, ForwardState::State::Sending);

			if (!itemSucceeded) {
				state->stopRequested = true;
				FinishForward(state, peerId);
				return (itemSendResult == AyuSync::SendSyncResult::Failed
					&& !didSendAnything)
					? Api::ForwardMessagesResult::Failed
					: Api::ForwardMessagesResult::Indeterminate;
			}
			didSendAnything = true;
			continue;
		}

		const auto &extractedText = item.text;
		if (extractedText.empty() && !item.media.downloadable) {
			hasSkippedItems = true;
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
			itemSendResult = AyuSync::sendMessageSync(
				session,
				std::move(message),
				targetHistory,
				isCancelled);
			itemSucceeded = itemSendResult
				== AyuSync::SendSyncResult::Succeeded;
			itemProgressKnown = itemSucceeded;
			didSendAnything = didSendAnything || itemSucceeded;
		} else {
			if (item.media.poll) {
				itemSendResult = AyuSync::sendMessageSync(
					session,
					std::move(message),
					targetHistory,
					isCancelled);
				itemSucceeded = itemSendResult
					== AyuSync::SendSyncResult::Succeeded;
				itemProgressKnown = itemSucceeded;
				didSendAnything = didSendAnything || itemSucceeded;
				if (!itemSucceeded) {
					state->stopRequested = true;
					FinishForward(state, peerId);
				return (itemSendResult == AyuSync::SendSyncResult::Failed
					&& !didSendAnything)
						? Api::ForwardMessagesResult::Failed
						: Api::ForwardMessagesResult::Indeterminate;
				}
				continue;
			}

			std::vector<ForwardMedia> groupMedia;
			auto groupItemCount = 1;
			auto expectedMediaCount = 0;
			auto allMediaPrepared = true;
			auto preparedMedia = prepareMedia(
				items,
				i,
				groupMedia,
				documentPaths,
				groupItemCount,
				expectedMediaCount,
				allMediaPrepared);
			itemProgressCount = groupItemCount;

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
					allMediaPrepared = false;
					preparedMedia.files.erase(preparedMedia.files.begin() + j);
					groupMedia.erase(groupMedia.begin() + j);
				}
			}

			if (item.groupId
				&& groupItemCount > 1
				&& (!allMediaPrepared
					|| groupMedia.size() != expectedMediaCount
					|| expectedMediaCount != groupItemCount)) {
				state->stopRequested = true;
				FinishForward(state, peerId);
				return Api::ForwardMessagesResult::Indeterminate;
			}

			if (preparedMedia.files.empty()) {
				hasSkippedItems = true;
				if (!message.textWithTags.empty()) {
					itemSendResult = AyuSync::sendMessageSync(
						session,
						std::move(message),
						targetHistory,
						isCancelled);
					itemSucceeded = itemSendResult
						== AyuSync::SendSyncResult::Succeeded;
					itemProgressKnown = itemSucceeded;
					didSendAnything = didSendAnything || itemSucceeded;
				}
				if (!itemSucceeded) {
					state->stopRequested = true;
					FinishForward(state, peerId);
					return (itemSendResult == AyuSync::SendSyncResult::Failed
						&& !didSendAnything)
						? Api::ForwardMessagesResult::Failed
						: Api::ForwardMessagesResult::Indeterminate;
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
			const auto trackedSticker = groupMedia.front().document
				&& groupMedia.front().document->sticker;
			if (trackedSticker) {
				itemSendResult = AyuSync::sendStickerSync(
					session,
					std::move(message),
					groupMedia.front().document->id,
					targetHistory,
					isCancelled);
				itemSucceeded = itemSendResult
					== AyuSync::SendSyncResult::Succeeded;
			} else {
				itemSendResult = sendMedia(
					session,
					bundle,
					groupMedia.front(),
					std::move(message),
					way.sendImagesAsPhotos(),
					targetHistory,
					isCancelled);
				itemSucceeded = itemSendResult
					== AyuSync::SendSyncResult::Succeeded;
			}
			itemProgressKnown = itemSucceeded;
		}
		if (!itemSucceeded) {
			state->stopRequested = true;
			FinishForward(state, peerId);
			return (itemSendResult == AyuSync::SendSyncResult::Failed
				&& !didSendAnything)
				? Api::ForwardMessagesResult::Failed
				: Api::ForwardMessagesResult::Indeterminate;
		}
		didSendAnything = didSendAnything || itemSucceeded;
		// if there are grouped messages
		// "i" is incremented in prepareMedia
	}
	if (!keepState) {
		FinishForward(state, peerId);
	}
	return !didSendAnything || hasSkippedItems
		? Api::ForwardMessagesResult::Indeterminate
		: Api::ForwardMessagesResult::Succeeded;
}

} // namespace AyuForward
