// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/data/messages_storage.h"

#include "ayu/data/ayu_database.h"
#include "ayu/utils/ayu_mapper.h"
#include "ayu/utils/telegram_helpers.h"
#include "base/unixtime.h"
#include "data/data_document.h"
#include "data/data_forum_topic.h"
#include "data/data_media_types.h"
#include "data/data_photo.h"
#include "data/data_session.h"
#include "history/history.h"
#include "history/history_item.h"
#include "history/history_item_components.h"
#include "lang/lang_keys.h"
#include "main/main_session.h"

#include <algorithm>
#include <new>

namespace AyuMessages {

namespace {

constexpr auto kMaxArchivedMessageBatch = std::size_t(64);

[[nodiscard]] std::string DeletedMediaText(not_null<HistoryItem*> item) {
	const auto media = item->media();
	if (!media) {
		return {};
	}
	if (media->photo()) {
		return tr::lng_in_dlg_photo(tr::now).toStdString();
	}

	const auto document = media->document();
	if (!document) {
		return {};
	}
	if (document->isVideoMessage()) {
		return tr::lng_in_dlg_video_message(tr::now).toStdString();
	} else if (document->isAnimation()) {
		return u"GIF"_q.toStdString();
	} else if (document->isVideoFile()) {
		return tr::lng_in_dlg_video(tr::now).toStdString();
	} else if (document->isVoiceMessage()) {
		return tr::lng_in_dlg_audio(tr::now).toStdString();
	} else if (document->sticker()) {
		return tr::lng_in_dlg_sticker(tr::now).toStdString();
	} else if (document->isAudioFile()) {
		return tr::lng_in_dlg_audio_file(tr::now).toStdString();
	}
	return tr::lng_in_dlg_file(tr::now).toStdString();
}

} // namespace

template<typename DerivedMessage>
std::vector<AyuMessageBase> convertToBase(const std::vector<DerivedMessage> &messages) {
	try {
		std::vector<AyuMessageBase> based;
		based.reserve(messages.size());
		for (const auto &msg : messages) {
			based.push_back(static_cast<AyuMessageBase>(msg));
		}
		return based;
	} catch (const std::bad_alloc &) {
		LOG(("AyuMessages: Failed to materialize archived messages"));
		return {};
	}
}

void map(not_null<HistoryItem*> item, AyuMessageBase &message) {
	const ID userId = item->history()->owner().session().userId().bare & PeerId::kChatTypeMask;

	message.userId = userId;
	message.dialogId = getDialogIdFromPeer(item->history()->peer);
	message.groupedId = item->groupId().raw();
	message.peerId = item->history()->peer->id.value & PeerId::kChatTypeMask;
	message.fromId = item->from()->id.value & PeerId::kChatTypeMask;
	if (item->topic()) {
		message.topicId = item->topicRootId().bare;
	} else {
		message.topicId = 0;
	}
	message.messageId = item->id.bare;
	message.date = item->date();
	message.flags = AyuMapper::mapItemFlagsToMTPFlags(item);

	if (const auto edited = item->Get<HistoryMessageEdited>()) {
		message.editDate = edited->date;
	} else {
		message.editDate = base::unixtime::now();
	}

	message.views = item->viewsCount();
	message.fwdFlags = 0;
	message.fwdFromId = 0;
	// message.fwdName
	message.fwdDate = 0;
	// message.fwdPostAuthor
	if (const auto msgsigned = item->Get<HistoryMessageSigned>()) {
		message.postAuthor = msgsigned->author.toStdString();
	}
	message.replyFlags = 0;
	message.replyMessageId = 0;
	message.replyPeerId = 0;
	message.replyTopId = 0;
	message.replyForumTopic = false;
	// message.replySerialized
	// message.replyMarkupSerialized
	message.entityCreateDate = base::unixtime::now();

	auto serializedText = AyuMapper::serializeTextWithEntities(item);
	message.text = serializedText.first;
	message.textEntities = serializedText.second;

	AyuMapper::mapMediaToMessage(item, message);
}

void addEditedMessage(not_null<HistoryItem *> item) {
	try {
		EditedMessage message;
		map(item, message);

		if (message.text.empty()) {
			return;
		}

		AyuDatabase::addEditedMessage(message);
	} catch (const std::bad_alloc &) {
		LOG(("AyuMessages: Failed to archive edited message"));
	}
}

std::vector<AyuMessageBase> getEditedMessages(
		ID userId,
		ID dialogId,
		ID messageId,
		ID minId,
		ID maxId,
		int totalLimit) {
	return convertToBase(AyuDatabase::getEditedMessages(userId, dialogId, messageId, minId, maxId, totalLimit));
}

std::vector<AyuMessageBase> getEditedMessages(not_null<HistoryItem*> item, ID minId, ID maxId, int totalLimit) {
	const ID userId = item->history()->owner().session().userId().bare & PeerId::kChatTypeMask;
	const auto dialogId = getDialogIdFromPeer(item->history()->peer);
	const auto messageId = item->id.bare;

	return getEditedMessages(userId, dialogId, messageId, minId, maxId, totalLimit);
}

bool hasRevisions(not_null<HistoryItem*> item) {
	const ID userId = item->history()->owner().session().userId().bare & PeerId::kChatTypeMask;
	const auto dialogId = getDialogIdFromPeer(item->history()->peer);
	const auto msgId = item->id.bare;

	return AyuDatabase::hasRevisions(userId, dialogId, msgId);
}

void addDeletedMessage(not_null<HistoryItem*> item) {
	try {
		DeletedMessage message;
		map(item, message);

		if (message.text.empty()) {
			message.text = DeletedMediaText(item);
		}
		if (message.text.empty()) {
			return;
		}

		AyuDatabase::addDeletedMessage(message);
	} catch (const std::bad_alloc &) {
		LOG(("AyuMessages: Failed to archive deleted message"));
	}
}

void addDeletedMessages(const std::vector<not_null<HistoryItem*>> &items) {
	for (auto offset = std::size_t(0); offset < items.size();) {
		const auto count = std::min(
			kMaxArchivedMessageBatch,
			items.size() - offset);
		try {
			std::vector<DeletedMessage> messages;
			messages.reserve(count);
			for (auto i = offset; i != offset + count; ++i) {
				DeletedMessage message;
				map(items[i], message);
				if (message.text.empty()) {
					message.text = DeletedMediaText(items[i]);
				}
				if (!message.text.empty()) {
					messages.push_back(std::move(message));
				}
			}
			AyuDatabase::addDeletedMessages(messages);
		} catch (const std::bad_alloc &) {
			LOG(("AyuMessages: Failed to archive deleted messages batch"));
			return;
		}
		offset += count;
	}
}

std::vector<AyuMessageBase> getDeletedMessages(
		ID userId,
		ID dialogId,
		ID topicId,
		ID minId,
		ID maxId,
		int totalLimit,
		const std::string &searchQuery) {
	return convertToBase(
		AyuDatabase::getDeletedMessages(userId, dialogId, topicId, minId, maxId, totalLimit, searchQuery));
}

std::vector<AyuMessageBase>
getDeletedMessages(not_null<PeerData*> peer, ID topicId, ID minId, ID maxId, int totalLimit, const QString &searchQuery) {
	const ID userId = peer->session().userId().bare & PeerId::kChatTypeMask;
	return getDeletedMessages(
		userId,
		getDialogIdFromPeer(peer),
		topicId,
		minId,
		maxId,
		totalLimit,
		searchQuery.toStdString());
}

bool hasDeletedMessages(not_null<PeerData*> peer, ID topicId) {
	const ID userId = peer->session().userId().bare & PeerId::kChatTypeMask;
	return AyuDatabase::hasDeletedMessages(userId, getDialogIdFromPeer(peer), topicId);
}

void removeDeletedMessage(not_null<HistoryItem*> item) {
	const auto peer = item->history()->peer;
	const ID userId = peer->session().userId().bare & PeerId::kChatTypeMask;
	AyuDatabase::removeDeletedMessage(userId, getDialogIdFromPeer(peer), item->id.bare);
}

void clearDeletedMessages(not_null<PeerData*> peer, ID topicId) {
	const ID userId = peer->session().userId().bare & PeerId::kChatTypeMask;
	AyuDatabase::clearDeletedMessages(userId, getDialogIdFromPeer(peer), topicId);
}

}
