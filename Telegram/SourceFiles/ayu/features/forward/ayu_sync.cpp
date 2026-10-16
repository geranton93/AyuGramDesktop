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
#include "storage/localimageloader.h"

#include <QtCore/QFile>

#include <algorithm>
#include <atomic>
#include <memory>
#include <mutex>
#include <optional>
#include <utility>
#include <vector>

namespace AyuSync {

namespace {

enum class SendWaitResult {
	Pending,
	Succeeded,
	Failed,
	Indeterminate,
	TimedOut,
	Cancelled,
};

class RequestCancellations final {
public:
	void add(Fn<void()> cancellation) {
		const auto lock = std::lock_guard(_mutex);
		_cancellations.push_back(std::move(cancellation));
	}

	[[nodiscard]] std::vector<Fn<void()>> take() {
		const auto lock = std::lock_guard(_mutex);
		return base::take(_cancellations);
	}

private:
	std::mutex _mutex;
	std::vector<Fn<void()>> _cancellations;

};

[[nodiscard]] SendSyncResult ToSendSyncResult(SendWaitResult result) {
	return (result == SendWaitResult::Succeeded)
		? SendSyncResult::Succeeded
		: (result == SendWaitResult::Failed)
		? SendSyncResult::Failed
		: SendSyncResult::Indeterminate;
}

[[nodiscard]] SendSyncResult ToSendSyncResult(Api::SendResult result) {
	return (result == Api::SendResult::Succeeded)
		? SendSyncResult::Succeeded
		: (result == Api::SendResult::Failed)
		? SendSyncResult::Failed
		: SendSyncResult::Indeterminate;
}

} // namespace

template <typename StartSend>
[[nodiscard]] SendSyncResult waitForSendRequest(
		const base::weak_ptr<Main::Session> &session,
		base::weak_ptr<History> targetHistory,
		const Fn<bool()> &cancelled,
		int expectedResults,
		StartSend &&startSend) {
	constexpr auto kTimeout = std::chrono::minutes(5);
	const auto deadline = std::chrono::steady_clock::now() + kTimeout;
	auto latch = std::make_shared<TimedCountDownLatch>(1);
	auto result = std::make_shared<std::atomic<SendWaitResult>>(
		SendWaitResult::Pending);
	auto abandoned = std::make_shared<std::atomic<bool>>(false);
	const auto isCancelled = cancelled;
	const auto complete = [latch, result](Api::SendResult value) {
		const auto mapped = value == Api::SendResult::Succeeded
			? SendWaitResult::Succeeded
			: value == Api::SendResult::Failed
			? SendWaitResult::Failed
			: SendWaitResult::Indeterminate;
		auto expected = SendWaitResult::Pending;
		if (result->compare_exchange_strong(expected, mapped)) {
			latch->countDown();
		}
	};
	const auto request = std::make_shared<Api::SendRequest>(
		expectedResults,
		complete,
		[abandoned, isCancelled, deadline] {
			return abandoned->load()
				|| (isCancelled && isCancelled())
				|| std::chrono::steady_clock::now() >= deadline;
		});
	const auto weakSession = session;

	try {
		crl::on_main([
				weakSession,
				targetHistory,
				isCancelled,
				deadline,
				abandoned,
				request,
				startSend = std::forward<StartSend>(startSend)]() mutable {
			if (abandoned->load()) {
				return;
			}
			if ((isCancelled && isCancelled())
				|| std::chrono::steady_clock::now() >= deadline) {
				request->finishOne(Api::SendResult::Indeterminate);
				return;
			}
			const auto current = weakSession.get();
			const auto history = targetHistory.get();
			if (!current || !history || &history->session() != current) {
				request->finishOne(Api::SendResult::Failed);
				return;
			}
			try {
				startSend(current, history, request);
			} catch (...) {
				request->cancel();
			}
		});
	} catch (...) {
		request->finishOne(Api::SendResult::Failed);
	}

	const auto waited = WaitForDownload(*latch, kTimeout, cancelled);
	if (waited == DownloadWaitResult::Completed
		&& std::chrono::steady_clock::now() < deadline
		&& !(cancelled && cancelled())) {
		return ToSendSyncResult(result->load());
	}
	const auto terminal = (waited == DownloadWaitResult::TimedOut
		|| std::chrono::steady_clock::now() >= deadline)
		? SendWaitResult::TimedOut
		: SendWaitResult::Cancelled;
	auto expected = SendWaitResult::Pending;
	result->compare_exchange_strong(expected, terminal);
	abandoned->store(true);
	crl::on_main_sync([request] {
		request->cancel();
	});
	return SendSyncResult::Indeterminate;
}

ForwardMessagesSyncResult forwardMessagesSync(
		const base::weak_ptr<Main::Session> &session,
		const MessageIdsList &itemIds,
		const ApiWrap::SendAction &action,
		Data::ForwardOptions options,
		base::weak_ptr<History> targetHistory,
		const Fn<bool()> &cancelled,
		Fn<void(Api::SendResult, int)> requestResultCallback) {
	constexpr auto kTimeout = std::chrono::minutes(1);
	if (cancelled && cancelled()) {
		return ForwardMessagesSyncResult::Cancelled;
	}
	if (itemIds.empty()) {
		return ForwardMessagesSyncResult::Failed;
	}
	const auto deadline = std::chrono::steady_clock::now() + kTimeout;
	auto latch = std::make_shared<TimedCountDownLatch>(1);
	auto result = std::make_shared<std::atomic<SendWaitResult>>(
		SendWaitResult::Pending);
	auto abandoned = std::make_shared<std::atomic<bool>>(false);
	auto cancellations = std::make_shared<RequestCancellations>();
	const auto complete = [latch, result](
			ForwardMessagesSyncResult value) {
		auto expected = SendWaitResult::Pending;
		const auto mapped = value == ForwardMessagesSyncResult::Succeeded
			? SendWaitResult::Succeeded
			: value == ForwardMessagesSyncResult::Failed
			? SendWaitResult::Failed
			: value == ForwardMessagesSyncResult::Cancelled
			? SendWaitResult::Cancelled
			: value == ForwardMessagesSyncResult::TimedOut
			? SendWaitResult::TimedOut
			: SendWaitResult::Indeterminate;
		if (result->compare_exchange_strong(expected, mapped)) {
			latch->countDown();
		}
	};
	const auto forwardIds = itemIds;
	const auto weakSession = session;
	const auto isCancelled = cancelled;
	const auto cancelRequestsOnMain = [cancellations] {
		for (const auto &cancel : cancellations->take()) {
			cancel();
		}
	};
	const auto cancelOutstanding = [
			abandoned,
			cancelRequestsOnMain] {
		abandoned->store(true);
		crl::on_main_sync(cancelRequestsOnMain);
	};

	try {
		crl::on_main([=]
		{
			if (abandoned->load()) {
				return;
			}
			if (isCancelled && isCancelled()) {
				complete(ForwardMessagesSyncResult::Cancelled);
				return;
			}
			if (std::chrono::steady_clock::now() >= deadline) {
				complete(ForwardMessagesSyncResult::TimedOut);
				return;
			}
			const auto current = weakSession.get();
			const auto history = targetHistory.get();
			if (!current || !history || &history->session() != current) {
				complete(ForwardMessagesSyncResult::Failed);
				return;
			}
			auto safeAction = action;
			safeAction.history = history;
			const auto forwardItems = current->data().idsToItems(forwardIds);
			if (forwardItems.size() != forwardIds.size()) {
				complete(ForwardMessagesSyncResult::Failed);
				return;
			}
			try {
				current->api().forwardMessagesWithResult(
					Data::ResolvedForwardDraft(forwardItems, options),
					std::move(safeAction),
					[complete](Api::ForwardMessagesResult value) {
						complete(value == Api::ForwardMessagesResult::Succeeded
							? ForwardMessagesSyncResult::Succeeded
							: value == Api::ForwardMessagesResult::Failed
							? ForwardMessagesSyncResult::Failed
							: ForwardMessagesSyncResult::Indeterminate);
					},
					[cancellations](Fn<void()> cancel) {
						cancellations->add(std::move(cancel));
					},
					[abandoned, isCancelled, deadline] {
						return abandoned->load()
							|| (isCancelled && isCancelled())
							|| std::chrono::steady_clock::now() >= deadline;
					},
						requestResultCallback);
				} catch (...) {
					abandoned->store(true);
					cancelRequestsOnMain();
					complete(ForwardMessagesSyncResult::Indeterminate);
				}
			});
	} catch (...) {
		complete(ForwardMessagesSyncResult::Failed);
	}

	const auto waited = WaitForDownload(*latch, kTimeout, cancelled);
	const auto toForwardResult = [](SendWaitResult value) {
		return value == SendWaitResult::Succeeded
			? ForwardMessagesSyncResult::Succeeded
			: value == SendWaitResult::Failed
			? ForwardMessagesSyncResult::Failed
			: value == SendWaitResult::Cancelled
			? ForwardMessagesSyncResult::Cancelled
			: value == SendWaitResult::TimedOut
			? ForwardMessagesSyncResult::TimedOut
			: ForwardMessagesSyncResult::Indeterminate;
	};
	if (waited == DownloadWaitResult::Completed
		&& std::chrono::steady_clock::now() < deadline
		&& !(cancelled && cancelled())) {
		return toForwardResult(result->load());
	}
	auto expected = SendWaitResult::Pending;
	const auto terminal = (waited == DownloadWaitResult::TimedOut
		|| std::chrono::steady_clock::now() >= deadline)
		? SendWaitResult::TimedOut
		: SendWaitResult::Cancelled;
	result->compare_exchange_strong(expected, terminal);
	cancelOutstanding();
	return toForwardResult(terminal);
}

[[nodiscard]] SendSyncResult sendMessageSync(
	const base::weak_ptr<Main::Session> &session,
	Api::MessageToSend &&message,
	base::weak_ptr<History> targetHistory,
	const Fn<bool()> &cancelled) {
	constexpr auto kTimeout = std::chrono::minutes(5);
	const auto deadline = std::chrono::steady_clock::now() + kTimeout;
	auto latch = std::make_shared<TimedCountDownLatch>(1);
	auto result = std::make_shared<std::atomic<SendWaitResult>>(
		SendWaitResult::Pending);
	auto abandoned = std::make_shared<std::atomic<bool>>(false);
	auto cancellations = std::make_shared<RequestCancellations>();
	const auto complete = [latch, result](Api::SendResult value) {
		auto expected = SendWaitResult::Pending;
		const auto mapped = value == Api::SendResult::Succeeded
			? SendWaitResult::Succeeded
			: value == Api::SendResult::Failed
			? SendWaitResult::Failed
			: SendWaitResult::Indeterminate;
		if (result->compare_exchange_strong(expected, mapped)) {
			latch->countDown();
		}
	};
	const auto weakSession = session;
	const auto isCancelled = cancelled;
	const auto cancelOutstanding = [
			abandoned,
			cancellations] {
		abandoned->store(true);
		crl::on_main_sync([cancellations] {
			for (const auto &cancel : cancellations->take()) {
				cancel();
			}
		});
	};

	try {
		crl::on_main([
				weakSession,
				targetHistory,
				isCancelled,
				deadline,
				abandoned,
				complete,
				cancellations,
				message = std::move(message)]() mutable {
			if (abandoned->load()
				|| (isCancelled && isCancelled())
				|| std::chrono::steady_clock::now() >= deadline) {
				complete(Api::SendResult::Indeterminate);
				return;
			}
			const auto current = weakSession.get();
			const auto history = targetHistory.get();
			if (!current || !history || &history->session() != current) {
				complete(Api::SendResult::Failed);
				return;
			}
			if (abandoned->load()
				|| (isCancelled && isCancelled())
				|| std::chrono::steady_clock::now() >= deadline) {
				complete(Api::SendResult::Indeterminate);
				return;
			}
			message.action.history = history;
			try {
				current->api().sendMessageWithResult(
					std::move(message),
					[complete](Api::SendResult value) {
						complete(value);
					},
					[cancellations](Fn<void()> cancel) {
						cancellations->add(std::move(cancel));
					},
					[abandoned, isCancelled, deadline] {
						return abandoned->load()
							|| (isCancelled && isCancelled())
							|| std::chrono::steady_clock::now() >= deadline;
				});
			} catch (...) {
				abandoned->store(true);
				for (const auto &cancel : cancellations->take()) {
					cancel();
				}
				complete(Api::SendResult::Indeterminate);
			}
		});
	} catch (...) {
		complete(Api::SendResult::Failed);
	}

	const auto waitResult = WaitForDownload(*latch, kTimeout, cancelled);
	if (waitResult == DownloadWaitResult::Completed
		&& std::chrono::steady_clock::now() < deadline
		&& !(cancelled && cancelled())) {
		return ToSendSyncResult(result->load());
	}
	auto expected = SendWaitResult::Pending;
	const auto terminal = (waitResult == DownloadWaitResult::TimedOut
		|| std::chrono::steady_clock::now() >= deadline)
		? SendWaitResult::TimedOut
		: SendWaitResult::Cancelled;
	result->compare_exchange_strong(expected, terminal);
	cancelOutstanding();
	return SendSyncResult::Indeterminate;
}

[[nodiscard]] SendSyncResult sendDocumentSync(
	const base::weak_ptr<Main::Session> &session,
	Ui::PreparedGroup &group,
	SendMediaType type,
	TextWithTags &&caption,
	const Api::SendAction &action,
	base::weak_ptr<History> targetHistory,
	const Fn<bool()> &cancelled) {
	if (group.list.files.empty()) {
		return SendSyncResult::Failed;
	}
	auto hasAlbum = group.list.files.size() > 1;
	if (hasAlbum
		&& ranges::any_of(group.list.files, &Ui::PreparedFile::ttlSeconds)) {
		hasAlbum = false;
	}
	const auto expectedResults = hasAlbum
		? 1
		: static_cast<int>(group.list.files.size());
	auto groupId = std::make_shared<SendingAlbum>();
	return waitForSendRequest(
		session,
		std::move(targetHistory),
		cancelled,
		expectedResults,
		[groupId,
		 action,
		 type,
		 hasAlbum,
		 list = std::move(group.list),
		 caption = std::move(caption)](
				Main::Session *current,
				History *history,
				std::shared_ptr<Api::SendRequest> request) mutable {
			auto safeAction = action;
			safeAction.history = history;
			list.files.front().caption = std::move(caption);
			current->api().sendFiles(
				std::move(list),
				type,
				hasAlbum ? groupId : nullptr,
				std::move(safeAction),
				std::move(request));
		});
}

[[nodiscard]] SendSyncResult sendStickerSync(
	const base::weak_ptr<Main::Session> &session,
	Api::MessageToSend &&message,
	DocumentId documentId,
	base::weak_ptr<History> targetHistory,
	const Fn<bool()> &cancelled) {
	return waitForSendRequest(
		session,
		std::move(targetHistory),
		cancelled,
		1,
		[message = std::move(message), documentId](
				Main::Session *current,
				History *history,
				std::shared_ptr<Api::SendRequest> request) mutable {
			if (!documentId) {
				request->finishOne(Api::SendResult::Failed);
				return;
			}
			message.action.history = history;
			Api::SendExistingDocument(
				std::move(message),
				current->data().document(documentId),
				std::nullopt,
				std::move(request));
		});
}

[[nodiscard]] SendSyncResult sendVoiceSync(
	const base::weak_ptr<Main::Session> &session,
	const QByteArray &data,
	int64_t duration,
	bool video,
	Api::MessageToSend &&message,
	base::weak_ptr<History> targetHistory,
	const Fn<bool()> &cancelled) {
	const auto action = message.action;
	const auto voiceData = data;
	return waitForSendRequest(
		session,
		std::move(targetHistory),
		cancelled,
		1,
		[action,
			voiceData,
			duration,
			video,
			caption = std::move(message.textWithTags)](
				Main::Session *current,
				History *history,
				std::shared_ptr<Api::SendRequest> request) {
			const auto to = FileLoadTo(
				history->peer->id,
				action.options,
				action.replyTo,
				action.replaceMediaOf);
			auto task = std::make_unique<FileLoadTask>(FileLoadTask::VoiceArgs{
				.session = current,
				.voice = voiceData,
				.duration = duration,
				.waveform = QVector<signed char>(),
				.video = video,
				.to = to,
				.caption = caption,
				.sendRequest = request,
			});
			const auto taskId = task->id();
			const auto weakSession = base::make_weak(current);
			request->registerCancellation([=] {
				if (const auto current = weakSession.get()) {
					current->api().fileLoader()->cancelTask(taskId);
				}
			});
			if (!request->shouldCancel()) {
				current->api().fileLoader()->addTask(std::move(task));
			}
		});
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

UploadedFile uploadFileSync(const base::weak_ptr<Main::Session> &session,
							PeerId peerId,
							const QString &path,
							SendMediaType type,
							bool forceFile,
							const QString &displayName,
							const Fn<bool()> &cancelled) {
	const auto weakSession = session;
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
	const auto mediaStartMutex = std::make_shared<std::mutex>();
	const auto mediaRequestId = std::make_shared<mtpRequestId>(0);

	crl::on_main([=]
	{
		if (mediaAbandoned->load() || (cancelled && cancelled())) {
			mediaLatch->countDown();
			return;
		}
		const auto current = weakSession.get();
		if (!current) {
			mediaLatch->countDown();
			return;
		}
		const auto currentPeer = current->data().peer(peerId);
		const auto lock = std::lock_guard(*mediaStartMutex);
		if (mediaAbandoned->load() || (cancelled && cancelled())) {
			mediaLatch->countDown();
			return;
		}
		*mediaRequestId = current->api().request(MTPmessages_UploadMedia(
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
	{
		const auto lock = std::lock_guard(*mediaStartMutex);
		mediaAbandoned->store(true);
	}
	crl::on_main_sync([weakSession, mediaRequestId] {
		if (*mediaRequestId) {
			if (const auto current = weakSession.get()) {
				current->api().request(*mediaRequestId).cancel();
			}
		}
	});

	return (mediaFinished == DownloadWaitResult::Completed)
		&& !(cancelled && cancelled())
		? *result
		: UploadedFile();
}

std::shared_ptr<const Iv::RichPage> loadFullRichPageSync(
	const base::weak_ptr<Main::Session> &session,
	FullMsgId itemId,
	const Fn<bool()> &cancelled) {
	if (cancelled && cancelled()) {
		return nullptr;
	}
	const auto resolved = std::make_shared<std::shared_ptr<const Iv::RichPage>>();
	auto latch = std::make_shared<TimedCountDownLatch>(1);
	const auto weakSession = session;
	const auto abandoned = std::make_shared<std::atomic<bool>>(false);
	const auto startMutex = std::make_shared<std::mutex>();
	const auto requestId = std::make_shared<mtpRequestId>(0);

	crl::on_main([=]
	{
		if (abandoned->load() || (cancelled && cancelled())) {
			latch->countDown();
			return;
		}
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
		const auto lock = std::lock_guard(*startMutex);
		if (abandoned->load() || (cancelled && cancelled())) {
			latch->countDown();
			return;
		}
		*requestId = current->api().request(MTPmessages_GetRichMessage(
			peerInput,
			MTP_int(itemId.msg)
		)).done([=](const MTPmessages_Messages &result)
		{
			if (abandoned->load()) {
				latch->countDown();
				return;
			}
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

	const auto finished = WaitForDownload(
		*latch,
		std::chrono::minutes(1),
		cancelled);
	{
		const auto lock = std::lock_guard(*startMutex);
		abandoned->store(true);
	}
	crl::on_main_sync([weakSession, requestId] {
		if (*requestId) {
			if (const auto current = weakSession.get()) {
				current->api().request(*requestId).cancel();
			}
		}
	});

	return (finished == DownloadWaitResult::Completed)
		? *resolved
		: nullptr;
}



SendSyncResult sendRichMessageSync(
		const base::weak_ptr<Main::Session> &session,
		const MTPInputRichMessage &richMessage,
		const Api::SendAction &action,
		base::weak_ptr<History> targetHistory,
		const Fn<bool()> &cancelled) {
	if (cancelled && cancelled()) {
		return SendSyncResult::Indeterminate;
	}
	const auto sendResult = std::make_shared<std::atomic<SendSyncResult>>(
		SendSyncResult::Failed);
	auto latch = std::make_shared<TimedCountDownLatch>(1);
	const auto weakSession = session;
	const auto target = AyuForward::SnapshotForwardTarget(
		session,
		targetHistory);
	if (!target) {
		return SendSyncResult::Failed;
	}
	const auto peerId = target->peerId;
	const auto abandoned = std::make_shared<std::atomic<bool>>(false);
	const auto startMutex = std::make_shared<std::mutex>();
	const auto requestId = std::make_shared<mtpRequestId>(0);

	crl::on_main([=]
	{
		if (abandoned->load() || (cancelled && cancelled())) {
			latch->countDown();
			return;
		}
		const auto current = weakSession.get();
		const auto currentHistory = targetHistory.get();
		if (!current || !currentHistory
			|| &currentHistory->session() != current) {
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

		const auto lock = std::lock_guard(*startMutex);
		if (abandoned->load() || (cancelled && cancelled())) {
			latch->countDown();
			return;
		}
		*requestId = current->api().request(MTPmessages_SendMessage(
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
				if (!abandoned->load()) {
					sendResult->store(SendSyncResult::Succeeded);
				}
			}
			latch->countDown();
		}).fail([=](const MTP::Error &error)
		{
			LOG(("AyuForward: rich message send failed: %1").arg(error.type()));
			sendResult->store(ToSendSyncResult(Api::SendResultForError(error)));
			latch->countDown();
		}).send();
	});

	const auto finished = WaitForDownload(
		*latch,
		std::chrono::minutes(2),
		cancelled);
	{
		const auto lock = std::lock_guard(*startMutex);
		abandoned->store(true);
	}
	crl::on_main_sync([weakSession, requestId] {
		if (*requestId) {
			if (const auto current = weakSession.get()) {
				current->api().request(*requestId).cancel();
			}
		}
	});

	if (finished != DownloadWaitResult::Completed
		|| (cancelled && cancelled())) {
		return SendSyncResult::Indeterminate;
	}
	return sendResult->load();
}

} // namespace AyuSync
