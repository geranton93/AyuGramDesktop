// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/features/forward/ayu_sync.h"
#include "ayu/features/forward/ayu_sync_wait.h"

#include "api/api_common.h"
#include "api/api_sending.h"
#include "apiwrap.h"
#include "ayu/features/forward/ayu_forward.h"
#include "ayu/utils/ayu_mapper.h"
#include "ayu/utils/telegram_helpers.h"
#include "base/base_file_utilities.h"
#include "base/weak_ptr.h"
#include "core/application.h"
#include "core/core_settings.h"
#include "core/file_utilities.h"
#include "data/data_channel.h"
#include "data/data_document.h"
#include "data/data_file_origin.h"
#include "data/data_peer.h"
#include "data/data_photo.h"
#include "data/data_photo_media.h"
#include "data/data_session.h"
#include "history/history.h"
#include "history/history_item.h"
#include "history/history_item_helpers.h"
#include "iv/iv_rich_page.h"
#include "main/main_session.h"
#include "mtproto/sender.h"
#include "storage/localimageloader.h"

#include <QtCore/QFile>

#include <algorithm>
#include <atomic>
#include <memory>
#include <mutex>
#include <optional>
#include <utility>

namespace AyuSync {

void forwardMessagesSync(not_null<Main::Session*> session,
							 const MessageIdsList &itemIds,
							 const ApiWrap::SendAction &action,
							 Data::ForwardOptions options,
							 base::weak_ptr<History> targetHistory) {
	auto latch = std::make_shared<TimedCountDownLatch>(1);
	const auto forwardIds = itemIds;
	const auto weakSession = base::make_weak(session);

	crl::on_main([=]
	{
		const auto current = weakSession.get();
		const auto history = targetHistory.get();
		if (current && history) {
			auto safeAction = action;
			safeAction.history = history;
			const auto forwardItems = current->data().idsToItems(forwardIds);
			if (forwardItems.size() != forwardIds.size()) {
				latch->countDown();
				return;
			}
			current->api().forwardMessages(
				Data::ResolvedForwardDraft(forwardItems, options),
				std::move(safeAction),
				[latch] { latch->countDown(); });
		} else {
			latch->countDown();
		}
	});


	latch->await(std::chrono::minutes(1));
}

void sendMessageSync(
		not_null<Main::Session*> session,
		Api::MessageToSend &&message,
		base::weak_ptr<History> targetHistory) {
	const auto action = message.action;
	const auto weakSession = base::make_weak(session);
	crl::on_main([=, message = std::move(message)]() mutable
	{
		const auto current = weakSession.get();
		const auto history = targetHistory.get();
		if (current && history) {
			message.action.history = history;
			// we cannot send events to objects
			// owned by a different thread
			// because sendMessage updates UI too

			current->api().sendMessage(std::move(message));
		}
	});


	waitForMsgSync(session, std::move(targetHistory));
}

void waitForMsgSync(
	not_null<Main::Session*> session,
		base::weak_ptr<History> targetHistory) {
	auto latch = std::make_shared<TimedCountDownLatch>(1);
	auto lifetime = std::make_shared<rpl::lifetime>();
	const auto weakSession = base::make_weak(session);
	const auto target = AyuForward::SnapshotForwardTarget(
		session,
		targetHistory);
	if (!target) {
		return;
	}
	const auto peerId = target->peerId;

	crl::on_main([=]
	{
		if (const auto current = weakSession.get()) {
			current->data().itemIdChanged()
				| rpl::filter([=](const Data::Session::IdChange &update)
				{
					return peerId == update.newId.peer;
				}) | rpl::on_next([=]
									  {
										  latch->countDown();
									  },
									  *lifetime);
		} else {
			latch->countDown();
		}
	});

	latch->await(std::chrono::minutes(5));
	crl::on_main_sync([lifetime = base::take(lifetime)]
	{
		lifetime->destroy();
	});
}

void sendDocumentSync(not_null<Main::Session*> session,
					  Ui::PreparedGroup &group,
					  SendMediaType type,
					  TextWithTags &&caption,
					  const Api::SendAction &action,
					  base::weak_ptr<History> targetHistory) {
	auto groupId = std::make_shared<SendingAlbum>();
	groupId->groupId = base::RandomValue<uint64>();
	const auto weakSession = base::make_weak(session);

	crl::on_main([=, lst = std::move(group.list), caption = std::move(caption)]() mutable
	{
		const auto current = weakSession.get();
		const auto history = targetHistory.get();
		if (!current || !history) {
			return;
		}
		auto safeAction = action;
		safeAction.history = history;
		auto size = lst.files.size();
		if (!lst.files.empty()) {
			lst.files.front().caption = std::move(caption);
		}
		current->api().sendFiles(
			std::move(lst),
			type,
			size > 1 ? groupId : nullptr,
			std::move(safeAction));
	});

	waitForMsgSync(session, std::move(targetHistory));
}

void sendStickerSync(not_null<Main::Session*> session,
						 Api::MessageToSend &&message,
						 DocumentId documentId,
						 base::weak_ptr<History> targetHistory) {
	const auto action = message.action;
	const auto weakSession = base::make_weak(session);
	crl::on_main([=, message = std::move(message)]() mutable
	{
		const auto current = weakSession.get();
		const auto history = targetHistory.get();
		if (current && history && documentId) {
			message.action.history = history;
			Api::SendExistingDocument(
				std::move(message),
				current->data().document(documentId),
				std::nullopt);
		}
	});

	waitForMsgSync(session, std::move(targetHistory));
}

void sendVoiceSync(not_null<Main::Session*> session,
					   const QByteArray &data,
					   int64_t duration,
					   bool video,
					   Api::MessageToSend &&message,
					   base::weak_ptr<History> targetHistory) {
	const auto action = message.action;
	const auto weakSession = base::make_weak(session);
	const auto target = AyuForward::SnapshotForwardTarget(
		session,
		targetHistory);
	if (!target) {
		return;
	}
	const auto peerId = target->peerId;
	const auto voiceData = data;

	crl::on_main([=]
	{
		const auto current = weakSession.get();
		if (!current) {
			return;
		}
		const auto to = FileLoadTo(
			peerId,
			action.options,
			action.replyTo,
			action.replaceMediaOf);
		current->api().fileLoader()->addTask(
			std::make_unique<FileLoadTask>(FileLoadTask::VoiceArgs{
				.session = current,
				.voice = voiceData,
				.duration = duration,
				.waveform = QVector<signed char>(),
				.video = video,
				.to = to,
				.caption = message.textWithTags
			}));
	});
	waitForMsgSync(session, std::move(targetHistory));
}

namespace {

[[nodiscard]] MTPInputMedia UploadedInputMedia(
	const std::shared_ptr<FilePrepareResult> &prepared,
	const Api::RemoteFileInfo &info) {
	if (prepared->type == SendMediaType::Photo) {
		return MTP_inputMediaUploadedPhoto(
			MTP_flags(0),
			info.file,
			MTP_vector<MTPInputDocument>(),
			MTP_int(0),
			MTPInputDocument());
	}

	auto attributes = QVector<MTPDocumentAttribute>();
	prepared->document.match([&](const MTPDdocument &data)
							 {
								 attributes = data.vattributes().v;
							 },
							 [](const auto &)
							 {
							 });
	if (attributes.isEmpty()) {
		attributes.push_back(MTP_documentAttributeFilename(MTP_string(prepared->filename)));
	}

	using Flag = MTPDinputMediaUploadedDocument::Flag;
	auto flags = MTPDinputMediaUploadedDocument::Flags();
	if (prepared->forceFile) {
		flags |= Flag::f_force_file;
	}
	if (info.thumb) {
		flags |= Flag::f_thumb;
	}
	return MTP_inputMediaUploadedDocument(
		MTP_flags(flags),
		info.file,
		info.thumb.value_or(MTPInputFile()),
		MTP_string(prepared->filemime),
		MTP_vector<MTPDocumentAttribute>(std::move(attributes)),
		MTP_vector<MTPInputDocument>(),
		MTPInputPhoto(),
		MTP_int(0),
		MTP_int(0));
}

} // namespace

UploadedFile uploadFileSync(not_null<Main::Session*> session,
							PeerId peerId,
							const QString &path,
							SendMediaType type,
							bool forceFile,
							const QString &displayName,
							const Fn<bool()> &cancelled) {
	const auto weakSession = base::make_weak(session);
	if (!weakSession) {
		return {};
	}
	if (path.isEmpty() || !QFile::exists(path)) {
		return {};
	}
	const auto pathInfo = QFileInfo(path);
	if (!pathInfo.isFile()
		|| pathInfo.size() < 0
		|| pathInfo.size() > AyuMapper::kMaxMediaFileSize) {
		return {};
	}
	if (cancelled && cancelled()) {
		return {};
	}
	std::unique_ptr<FileLoadTask> task;
	crl::on_main_sync([&]
	{
		if (const auto current = weakSession.get()) {
			task = std::make_unique<FileLoadTask>(FileLoadTask::Args{
				.session = current,
				.filepath = path,
				.type = type,
				.to = FileLoadTo(
					peerId,
					Api::SendOptions(),
					FullReplyTo(),
					MsgId()),
				.forceFile = forceFile,
				.sendLargePhotos = (type == SendMediaType::Photo),
				.displayName = displayName,
			});
		}
	});
	if (!task) {
		return {};
	}
	task->process({.generateGoodThumbnail = false});

	const auto prepared = task->peekResult();
	if (!prepared) {
		return {};
	}

	const auto info = std::make_shared<std::optional<Api::RemoteFileInfo>>();
	auto uploadLatch = std::make_shared<TimedCountDownLatch>(1);
	auto lifetime = std::make_shared<rpl::lifetime>();
	const auto uploadId = std::make_shared<FullMsgId>();
	const auto uploadAbandoned = std::make_shared<std::atomic_bool>(false);

	crl::on_main([=]
	{
		if (uploadAbandoned->load()) {
			uploadLatch->countDown();
			return;
		}
		const auto current = weakSession.get();
		if (!current) {
			uploadLatch->countDown();
			return;
		}
		const auto currentUploadId = FullMsgId(
			peerId,
			current->data().nextLocalMessageId());
		*uploadId = currentUploadId;
		const auto ready = [=](const Storage::UploadedMedia &data)
		{
			if (!weakSession) {
				uploadLatch->countDown();
				return;
			}
			if (data.fullId != currentUploadId) {
				return;
			}
			*info = data.info;
			uploadLatch->countDown();
		};
		const auto failed = [=](const FullMsgId &id)
		{
			if (id == currentUploadId) {
				uploadLatch->countDown();
			}
		};

		current->uploader().photoReady() | rpl::on_next(ready, *lifetime);
		current->uploader().documentReady() | rpl::on_next(ready, *lifetime);
		current->uploader().photoFailed() | rpl::on_next(failed, *lifetime);
		current->uploader().documentFailed() | rpl::on_next(failed, *lifetime);

		current->uploader().upload(currentUploadId, prepared);
	});

	const auto uploadFinished = WaitForDownload(
		*uploadLatch,
		std::chrono::minutes(30),
		cancelled);
	const auto cancelUpload = uploadFinished != DownloadWaitResult::Completed;
	if (cancelUpload) {
		uploadAbandoned->store(true);
	}
	crl::on_main_sync([
		lifetime = base::take(lifetime),
		weakSession,
		uploadId,
		uploadAbandoned,
		cancelUpload
	]
	{
		lifetime->destroy();
		if (cancelUpload && *uploadId) {
			if (const auto current = weakSession.get()) {
				current->uploader().cancel(*uploadId);
			}
		}
		uploadAbandoned->store(true);
	});

	if (uploadFinished != DownloadWaitResult::Completed
		|| !info->has_value()) {
		return {};
	}
	if (cancelled && cancelled()) {
		return {};
	}

	const auto result = std::make_shared<UploadedFile>();
	auto mediaLatch = std::make_shared<TimedCountDownLatch>(1);
	const auto mediaAbandoned = std::make_shared<std::atomic_bool>(false);
	std::shared_ptr<MTP::Sender> mediaApi;
	crl::on_main_sync([&]
	{
		if (const auto current = weakSession.get()) {
			mediaApi = std::make_shared<MTP::Sender>(&current->mtp());
		}
	});
	if (!mediaApi) {
		return {};
	}

	crl::on_main([=]
	{
		if (mediaAbandoned->load()) {
			mediaLatch->countDown();
			return;
		}
		const auto current = weakSession.get();
		if (!current) {
			mediaLatch->countDown();
			return;
		}
		const auto currentPeer = current->data().peer(peerId);
		mediaApi->request(MTPmessages_UploadMedia(
			MTP_flags(0),
			MTPstring(),
			currentPeer->input(),
				UploadedInputMedia(prepared, **info)
			)).done([=](const MTPMessageMedia &media)
			{
				if (const auto current = weakSession.get()) {
					media.match(
						[&](const MTPDmessageMediaPhoto &data) {
							const auto photo = data.vphoto();
							if (photo && photo->type() == mtpc_photo) {
								result->photoId = current->data().processPhoto(*photo)->id;
							}
						},
						[&](const MTPDmessageMediaDocument &data) {
							const auto document = data.vdocument();
							if (document && document->type() == mtpc_document) {
								result->documentId = current->data().processDocument(*document)->id;
							}
						},
						[](const auto &) {
						});
				}
			mediaLatch->countDown();
		}).fail([=](const MTP::Error &)
		{
			mediaLatch->countDown();
		}).send();
	});

	const auto mediaFinished = WaitForDownload(
		*mediaLatch,
		std::chrono::minutes(5),
		cancelled);
	if (mediaFinished != DownloadWaitResult::Completed) {
		mediaAbandoned->store(true);
	}
	crl::on_main_sync([
		mediaApi = base::take(mediaApi),
		mediaAbandoned
	]() mutable
	{
		mediaAbandoned->store(true);
		mediaApi.reset();
	});

	return mediaFinished == DownloadWaitResult::Completed
		? *result
		: UploadedFile();
}

std::shared_ptr<const Iv::RichPage> loadFullRichPageSync(
	not_null<Main::Session*> session,
	FullMsgId itemId) {
	const auto resolved = std::make_shared<std::shared_ptr<const Iv::RichPage>>();
	auto latch = std::make_shared<TimedCountDownLatch>(1);
	const auto weakSession = base::make_weak(session);

	crl::on_main([=]
	{
		const auto current = weakSession.get();
		if (!current) {
			latch->countDown();
			return;
		}
		const auto item = current->data().message(itemId);
		if (!item) {
			latch->countDown();
			return;
		}
		if (const auto full = item->fullRichPage()) {
			*resolved = full;
			latch->countDown();
			return;
		}

		const auto page = item->richPage();
		if (!page) {
			latch->countDown();
			return;
		}
		if (!page->part || item->isLocal() || item->isDeleted()) {
			*resolved = page;
			latch->countDown();
			return;
		}

		const auto peerId = item->history()->peer->id;
		const auto peerInput = item->history()->peer->input();

		current->api().request(MTPmessages_GetRichMessage(
			peerInput,
			MTP_int(itemId.msg)
		)).done([=](const MTPmessages_Messages &result)
		{
			const auto current = weakSession.get();
			if (!current) {
				latch->countDown();
				return;
			}
			auto full = std::shared_ptr<const Iv::RichPage>();
			const auto process = [&](const auto &data)
			{
				current->data().processUsers(data.vusers());
				current->data().processChats(data.vchats());
				current->data().peer(peerId)->processTopics(data.vtopics());
				for (const auto &message : data.vmessages().v) {
					if (message.type() != mtpc_message) {
						continue;
					}
					const auto &fields = message.c_message();
					if (MsgId(fields.vid().v) != itemId.msg) {
						continue;
					}
					if (const auto richMessage = fields.vrich_message()) {
						full = Iv::ParseRichPage(current, *richMessage);
					}
					break;
				}
			};
			result.match([](const MTPDmessages_messagesNotModified &)
						 {
						 },
						 [&](const MTPDmessages_channelMessages &data)
						 {
							 process(data);
							 if (const auto channel
									 = current->data().peer(peerId)->asChannel()) {
								 channel->ptsReceived(data.vpts().v);
							 }
						 },
						 [&](const auto &data)
						 {
							 process(data);
						 });
			if (full) {
				if (const auto resolvedItem = current->data().message(itemId)) {
					resolvedItem->setFullRichPage(full);
				}
				*resolved = full;
			}
			latch->countDown();
		}).fail([=](const MTP::Error &)
		{
			latch->countDown();
		}).send();
	});

	const auto finished = latch->await(std::chrono::minutes(1));

	return finished ? *resolved : nullptr;
}

bool sendRichMessageSync(not_null<Main::Session*> session,
						 const MTPInputRichMessage &richMessage,
						 const Api::SendAction &action,
						 base::weak_ptr<History> targetHistory) {
	const auto sent = std::make_shared<bool>(false);
	auto latch = std::make_shared<TimedCountDownLatch>(1);
	const auto weakSession = base::make_weak(session);
	const auto target = AyuForward::SnapshotForwardTarget(
		session,
		targetHistory);
	if (!target) {
		return false;
	}
	const auto peerId = target->peerId;

	crl::on_main([=]
	{
		const auto current = weakSession.get();
		const auto currentHistory = targetHistory.get();
		if (!current || !currentHistory) {
			latch->countDown();
			return;
		}
		auto safeAction = action;
		safeAction.history = currentHistory;
		const auto peer = current->data().peer(peerId);

		using Flag = MTPmessages_SendMessage::Flag;
		auto sendFlags = MTPmessages_SendMessage::Flags(0)
			| Flag::f_rich_message;
		if (safeAction.replyTo) {
			sendFlags |= Flag::f_reply_to;
		}
		if (ShouldSendSilent(peer, safeAction.options)) {
			sendFlags |= Flag::f_silent;
		}
		if (safeAction.options.scheduled) {
			sendFlags |= Flag::f_schedule_date;
			if (safeAction.options.scheduleRepeatPeriod) {
				sendFlags |= Flag::f_schedule_repeat_period;
			}
		}
		if (safeAction.options.sendAs) {
			sendFlags |= Flag::f_send_as;
		}
		if (safeAction.options.effectId) {
			sendFlags |= Flag::f_effect;
		}

		current->api().request(MTPmessages_SendMessage(
			MTP_flags(sendFlags),
			peer->input(),
			safeAction.mtpReplyTo(),
			MTP_string(QString()),
			MTP_long(base::RandomValue<uint64>()),
			MTPReplyMarkup(),
			MTPVector<MTPMessageEntity>(),
			MTP_int(safeAction.options.scheduled),
			MTP_int(safeAction.options.scheduleRepeatPeriod),
			(safeAction.options.sendAs
				? safeAction.options.sendAs->input()
				: MTP_inputPeerEmpty()),
			MTPInputQuickReplyShortcut(),
			MTP_long(safeAction.options.effectId),
			MTP_long(0),
			Api::SuggestToMTP(safeAction.options.suggest),
			richMessage
		)).done([=](const MTPUpdates &result)
		{
			if (const auto current = weakSession.get()) {
				current->api().applyUpdates(result);
				*sent = true;
			}
			latch->countDown();
		}).fail([=](const MTP::Error &error)
		{
			LOG(("AyuForward: rich message send failed: %1").arg(error.type()));
			latch->countDown();
		}).send();
	});

	const auto finished = latch->await(std::chrono::minutes(2));

	return finished && *sent;
}

} // namespace AyuSync
