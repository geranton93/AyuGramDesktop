// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/features/forward/ayu_sync.h"

#include "api/api_common.h"
#include "api/api_sending.h"
#include "apiwrap.h"
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
namespace {

constexpr auto kDocumentDownloadTimeout = std::chrono::minutes(15);
constexpr auto kDownloadWaitStep = std::chrono::milliseconds(100);
constexpr auto kMaxDocumentPathAttempts = 128;
constexpr auto kMaxDocumentNameChars = int(AyuMapper::kMaxMediaPathBytes);

[[nodiscard]] bool IsBoundedMediaSize(int64 size) {
	return size >= 0 && size <= AyuMapper::kMaxMediaFileSize;
}

[[nodiscard]] bool IsBoundedDocumentName(const QString &name) {
	if (name.size() > kMaxDocumentNameChars) {
		return false;
	}
	return name.toUtf8().size()
		<= static_cast<int>(AyuMapper::kMaxMediaPathBytes);
}

[[nodiscard]] std::optional<PeerId> ResolveTargetPeerId(
		not_null<Main::Session*> session,
		const base::weak_ptr<History> &targetHistory) {
	auto result = std::optional<PeerId>();
	const auto weakSession = base::make_weak(session);
	crl::on_main_sync([&] {
		const auto current = weakSession.get();
		const auto history = targetHistory.get();
		if (current && history && &history->session() == current) {
			result = history->peer->id;
		}
	});
	return result;
}

enum class DownloadWaitResult {
	Completed,
	Cancelled,
	TimedOut,
};

struct ActiveDocumentDownload {
	QString path;
	QString readyPath;
	TimedCountDownLatch done{ 1 };
	rpl::lifetime lifetime;
	std::mutex mutex;
	int consumers = 1;
	bool allowSizeFallback = false;
	bool finished = false;
};

struct DocumentDownloadKey {
	uint64 sessionUniqueId = 0;
	DocumentId documentId = 0;

	friend inline auto operator<=>(
		DocumentDownloadKey,
		DocumentDownloadKey) = default;
};

struct DocumentDownloadRegistration {
	std::shared_ptr<ActiveDocumentDownload> state;
	bool owner = false;
};

using ActiveDocumentDownloads = base::flat_map<
	DocumentDownloadKey,
	std::shared_ptr<ActiveDocumentDownload>>;

[[nodiscard]] std::mutex &DocumentDownloadsMutex() {
	static auto result = std::mutex();
	return result;
}

[[nodiscard]] ActiveDocumentDownloads &DocumentDownloads() {
	static auto result = ActiveDocumentDownloads();
	return result;
}

[[nodiscard]] DocumentDownloadRegistration RegisterDocumentDownload(
		DocumentDownloadKey key) {
	const auto lock = std::lock_guard(DocumentDownloadsMutex());
	auto &active = DocumentDownloads();
	const auto i = active.find(key);
	if (i != active.end()) {
		++i->second->consumers;
		return { i->second, false };
	}
	auto state = std::make_shared<ActiveDocumentDownload>();
	active.emplace(key, state);
	return { std::move(state), true };
}

[[nodiscard]] bool FinishDocumentDownload(
		DocumentDownloadKey key,
		const std::shared_ptr<ActiveDocumentDownload> &state,
		bool ready,
		bool removePath = true) {
	auto path = QString();
	{
		const auto stateLock = std::lock_guard(state->mutex);
		if (state->finished) {
			return false;
		}
		state->finished = true;
		path = state->path;
		state->path.clear();
		if (ready) {
			state->readyPath = path;
		} else {
			state->readyPath.clear();
		}
	}
	if (!ready && removePath && !path.isEmpty()) {
		QFile::remove(path);
	}
	{
		const auto lock = std::lock_guard(DocumentDownloadsMutex());
		auto &active = DocumentDownloads();
		const auto i = active.find(key);
		if (i != active.end() && i->second == state) {
			active.erase(i);
		}
	}
	state->done.countDown();
	return true;
}

[[nodiscard]] bool SameFilePath(const QString &left, const QString &right);

void CancelDocumentDownload(
		DocumentDownloadKey key,
		const std::shared_ptr<ActiveDocumentDownload> &state,
		const base::weak_ptr<Main::Session> &weakSession) {
	auto path = QString();
	{
		const auto stateLock = std::lock_guard(state->mutex);
		path = state->path;
	}
	if (FinishDocumentDownload(key, state, false, false)) {
		crl::on_main_sync([=] {
			auto removePath = true;
			if (const auto current = weakSession.get()) {
				const auto document = current->data().document(key.documentId);
				const auto loadingPath = document->loadingFilePath();
				if (document->loading()
					&& !loadingPath.isEmpty()
					&& SameFilePath(path, loadingPath)) {
					document->cancel();
					removePath = false;
				} else if (document->loading()) {
					removePath = false;
				}
			}
			if (removePath && !path.isEmpty()) {
				QFile::remove(path);
			}
			state->lifetime.destroy();
		});
	}
}

void ReleaseDocumentDownloadConsumer(
		DocumentDownloadKey key,
		const std::shared_ptr<ActiveDocumentDownload> &state) {
	auto released = false;
	{
		const auto lock = std::lock_guard(DocumentDownloadsMutex());
		Expects(state->consumers > 0);
		--state->consumers;
		if (state->consumers == 0) {
			auto &active = DocumentDownloads();
			const auto i = active.find(key);
			if (i != active.end() && i->second == state) {
				active.erase(i);
				released = true;
			}
		}
	}
	if (released) {
		state->done.countDown();
	}
}

[[nodiscard]] DownloadWaitResult WaitForDownload(
		TimedCountDownLatch &done,
		std::chrono::milliseconds timeout,
		const Fn<bool()> &cancelled) {
	const auto deadline = std::chrono::steady_clock::now() + timeout;
	while (true) {
		if (done.await(std::chrono::milliseconds(0))) {
			return DownloadWaitResult::Completed;
		}
		if (cancelled && cancelled()) {
			return DownloadWaitResult::Cancelled;
		}
		const auto remaining = std::chrono::duration_cast<
			std::chrono::milliseconds>(
			deadline - std::chrono::steady_clock::now());
		if (remaining <= std::chrono::milliseconds(0)) {
			return DownloadWaitResult::TimedOut;
		}
		if (done.await(std::min(
				remaining,
				kDownloadWaitStep))) {
			return DownloadWaitResult::Completed;
		}
	}
}

[[nodiscard]] bool SameFilePath(const QString &left, const QString &right) {
	const auto leftInfo = QFileInfo(left);
	const auto rightInfo = QFileInfo(right);
	const auto leftCanonical = leftInfo.canonicalFilePath();
	const auto rightCanonical = rightInfo.canonicalFilePath();
	if (!leftCanonical.isEmpty() && !rightCanonical.isEmpty()) {
		return leftCanonical == rightCanonical;
	}
#ifdef Q_OS_WIN
	return leftInfo.absoluteFilePath().compare(
		rightInfo.absoluteFilePath(),
		Qt::CaseInsensitive) == 0;
#else // Q_OS_WIN
	return leftInfo.absoluteFilePath() == rightInfo.absoluteFilePath();
#endif // !Q_OS_WIN
}

[[nodiscard]] bool FileHasExactSize(const QString &path, int64 expectedSize) {
	const auto info = QFileInfo(path);
	return IsBoundedMediaSize(expectedSize)
		&& !path.isEmpty()
		&& info.isFile()
		&& info.size() == expectedSize;
}

[[nodiscard]] QString ReadyDocumentPath(
		const std::shared_ptr<ActiveDocumentDownload> &state,
		int64 expectedSize) {
	auto path = QString();
	{
		const auto stateLock = std::lock_guard(state->mutex);
		path = state->readyPath;
	}
	return FileHasExactSize(path, expectedSize) ? path : QString();
}

[[nodiscard]] QString LoadedDocumentPath(
	not_null<Main::Session*> session,
	DocumentId documentId) {
	auto result = QString();
	const auto weakSession = base::make_weak(session);
	crl::on_main_sync([&] {
		if (const auto current = weakSession.get()) {
			result = current->data().document(documentId)->filepath(true);
		}
	});
	return result;
}

[[nodiscard]] bool DocumentReady(
	not_null<DocumentData*> document,
	const QString &path,
	int64 expectedSize) {
	const auto loaded = document->filepath(true);
	return !path.isEmpty()
		&& !loaded.isEmpty()
		&& SameFilePath(path, loaded)
		&& FileHasExactSize(path, expectedSize);
}

void CompleteDocumentDownload(
	not_null<Main::Session*> session,
	DocumentDownloadKey key,
	int64 expectedSize,
	const std::shared_ptr<ActiveDocumentDownload> &state) {
	const auto document = session->data().document(key.documentId);
	if (document->loading()) {
		return;
	}
	auto path = QString();
	auto allowSizeFallback = false;
	{
		const auto stateLock = std::lock_guard(state->mutex);
		path = state->path;
		allowSizeFallback = state->allowSizeFallback;
	}
	const auto ready = allowSizeFallback
		? FileHasExactSize(path, expectedSize)
		: (document->status != FileDownloadFailed
			&& !document->cancelled()
			&& DocumentReady(document, path, expectedSize));
	if (FinishDocumentDownload(key, state, ready)) {
		state->lifetime.destroy();
	}
}

[[nodiscard]] QString GeneratedDocumentName(
		not_null<DocumentData*> document,
		const QString &prefix,
		const QString &extension) {
	return prefix
		+ QString::number(document->getDC())
		+ u"_"_q
		+ QString::number(document->id)
		+ extension;
}

[[nodiscard]] QString DocumentFileName(not_null<DocumentData*> document) {
	if (!document->filename().isEmpty()) {
		const auto result = base::FileNameFromUserString(document->filename());
		return IsBoundedDocumentName(result) ? result : QString();
	}
	if (document->isVoiceMessage()) {
		return GeneratedDocumentName(document, u"audio_"_q, u".ogg"_q);
	}
	if (document->isVideoMessage()) {
		return GeneratedDocumentName(document, u"round_"_q, u".mp4"_q);
	}
	if (document->isGifv()) {
		return GeneratedDocumentName(document, u"gif_"_q, u".gif"_q);
	}
	if (document->isVideoFile()) {
		return GeneratedDocumentName(document, u"video_"_q, u".mp4"_q);
	}
	return {};
}

[[nodiscard]] QString NextDocumentPath(
	not_null<Main::Session*> session,
	DocumentId documentId) {
	auto result = QString();
	const auto weakSession = base::make_weak(session);
	crl::on_main_sync([&] {
		if (const auto current = weakSession.get()) {
			const auto document = current->data().document(documentId);
			const auto directory = pathForSave(current);
			const auto filename = DocumentFileName(document);
			if (!directory.isEmpty() && !filename.isEmpty()) {
				result = filedialogNextFilename(
					filename,
					QString(),
					directory);
			}
		}
	});
	return result;
}

} // namespace

QString pathForSave(not_null<Main::Session*> session) {
	auto path = Core::App().settings().downloadPath();
	if (path.isEmpty()) {
		return File::DefaultDownloadPath(session);
	}
	if (path == FileDialog::Tmp()) {
		return session->local().tempDirectory();
	}
	return path;
}

QString documentFileName(not_null<DocumentData*> document) {
	return DocumentFileName(document);
}

QString filePath(not_null<Main::Session*> session, not_null<PhotoData*> photo) {
	const auto directory = pathForSave(session);
	if (directory.isEmpty()) {
		return {};
	}
	const auto filename = QString::number(photo->getDC())
		+ u"_"_q
		+ QString::number(photo->id)
		+ u".jpg"_q;
	return QDir(directory).filePath(filename);
}

qint64 fileSize(const QString &path) {
	if (path.isEmpty()) {
		return 0;
	}
	QFile file(path);
	return file.exists() ? file.size() : 0;
}

bool isValidPhotoSnapshot(const PhotoSnapshot &photo) {
	return photo.id
		&& IsBoundedMediaSize(photo.size);
}

bool isValidDocumentSnapshot(const DocumentSnapshot &document) {
	return document.id
		&& IsBoundedMediaSize(document.size)
		&& IsBoundedDocumentName(document.name);
}

PhotoSnapshot snapshotPhoto(
	not_null<Main::Session*> session,
	not_null<PhotoData*> photo) {
	return PhotoSnapshot{
		.id = photo->id,
		.dc = photo->getDC(),
		.size = photo->imageByteSize(Data::PhotoSize::Large),
		.path = filePath(session, photo),
	};
}

DocumentSnapshot snapshotDocument(not_null<DocumentData*> document) {
	return DocumentSnapshot{
		.id = document->id,
		.size = document->size,
		.name = DocumentFileName(document),
		.duration = document->duration(),
		.sticker = document->sticker() != nullptr,
		.voice = document->isVoiceMessage(),
		.round = document->isVideoMessage(),
		.playable = document->isVideoFile()
			|| document->isGifv()
			|| document->isSong()
			|| document->isAudioFile()
			|| document->isVoiceMessage(),
	};
}

DocumentPaths loadDocuments(
		not_null<Main::Session*> session,
		const std::vector<DownloadItem> &items,
		const Fn<bool()> &cancelled) {
	auto result = DocumentPaths();
	const auto weakSession = base::make_weak(session);
	for (const auto &item : items) {
		if (!weakSession || (cancelled && cancelled())) {
			break;
		}
		if (const auto document = item.document) {
			if (isValidDocumentSnapshot(*document)) {
				const auto path = loadDocumentSync(
					session,
					*document,
					item.fullId,
					cancelled);
				if (!path.isEmpty()) {
					result.emplace(document->id, path);
				}
			}
		} else if (const auto photo = item.photo) {
			if (!isValidPhotoSnapshot(*photo)) {
				continue;
			}
			const auto info = QFileInfo(photo->path);
			if (info.isFile() && info.size() == photo->size) {
				continue;
			}

			loadPhotoSync(
				session,
				*photo,
				item.fullId,
				cancelled);
		}
	}
	return result;
}

QString loadDocumentSync(
	not_null<Main::Session*> session,
		const DocumentSnapshot &document,
		Data::FileOrigin origin,
		const Fn<bool()> &cancelled) {
	const auto weakSession = base::make_weak(session);
	if (!weakSession
		|| !isValidDocumentSnapshot(document)
		|| (cancelled && cancelled())) {
		return {};
	}
	auto sessionUniqueId = uint64(0);
	crl::on_main_sync([&] {
		if (const auto current = weakSession.get()) {
			sessionUniqueId = current->uniqueId();
		}
	});
	if (!sessionUniqueId) {
		return {};
	}
	const auto key = DocumentDownloadKey{
		.sessionUniqueId = sessionUniqueId,
		.documentId = document.id,
	};
	const auto registration = RegisterDocumentDownload(key);
	const auto releaseConsumer = gsl::finally([&] {
		ReleaseDocumentDownloadConsumer(key, registration.state);
	});
	if (!registration.owner) {
		const auto waitResult = WaitForDownload(
				registration.state->done,
				kDocumentDownloadTimeout,
				cancelled);
		if (waitResult != DownloadWaitResult::Completed) {
			return {};
		}
		return ReadyDocumentPath(registration.state, document.size);
	}
	const auto cleanup = gsl::finally([&] {
		CancelDocumentDownload(key, registration.state, weakSession);
	});
	auto path = LoadedDocumentPath(session, document.id);
	if (FileHasExactSize(path, document.size)) {
		{
			const auto stateLock = std::lock_guard(registration.state->mutex);
			registration.state->path = path;
		}
		if (FinishDocumentDownload(key, registration.state, true)) {
			crl::on_main_sync([state = registration.state] {
				state->lifetime.destroy();
			});
		}
		return path;
	}
	const auto state = registration.state;
	const auto weakState = std::weak_ptr<ActiveDocumentDownload>(state);
	const auto documentId = document.id;
	const auto expectedSize = document.size;
	auto started = false;
	for (auto attempt = 0; attempt != kMaxDocumentPathAttempts; ++attempt) {
		if (!weakSession) {
			return {};
		}
		path = NextDocumentPath(session, document.id);
		if (path.isEmpty()) {
			return {};
		}
		if (!QDir().mkpath(QFileInfo(path).absolutePath())) {
			return {};
		}
		if (cancelled && cancelled()) {
			return {};
		}
		crl::on_main_sync([&] {
			const auto current = weakSession.get();
			if (!current) {
				return;
			}
			auto destination = std::make_unique<QFile>(path);
			if (!destination->open(QIODevice::ReadWrite | QIODevice::NewOnly)) {
				return;
			}
			{
				const auto stateLock = std::lock_guard(state->mutex);
				state->path = path;
			}
			const auto data = current->data().document(documentId);
			data->save(
				origin,
				path,
				LoadFromCloudOrLocal,
				false,
				std::move(destination));
			{
				const auto stateLock = std::lock_guard(state->mutex);
				state->allowSizeFallback = !data->loading();
			}
			if (!data->loading()) {
				started = true;
				CompleteDocumentDownload(current, key, expectedSize, state);
				return;
			}

			current->data().documentLoadProgress()
				| rpl::filter([=](not_null<DocumentData*> changed) {
					return changed->id == key.documentId
						&& !changed->loading();
				})
				| rpl::on_next([=](not_null<DocumentData*>) {
					if (const auto current = weakSession.get()) {
						if (const auto state = weakState.lock()) {
							CompleteDocumentDownload(
									current,
									key,
									expectedSize,
									state);
						}
					}
				}, state->lifetime);
			started = true;
		});
		if (started) {
			break;
		}
		if (!QFile::exists(path)) {
			return {};
		}
	}
	if (!started) {
		return {};
	}

	const auto waitResult = WaitForDownload(
			state->done,
			kDocumentDownloadTimeout,
			cancelled);
	if (waitResult != DownloadWaitResult::Completed) {
		return {};
	}
	return ReadyDocumentPath(state, document.size);
}

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

void loadPhotoSync(
	not_null<Main::Session*> session,
	const PhotoSnapshot &photo,
	Data::FileOrigin origin,
	const Fn<bool()> &cancelled) {
	const auto weakSession = base::make_weak(session);
	if (!weakSession || !isValidPhotoSnapshot(photo)) {
		return;
	}
	const auto snapshot = photo;

	auto latch = std::make_shared<TimedCountDownLatch>(1);
	auto lifetime = std::make_shared<rpl::lifetime>();

	crl::on_main([=]
	{
		const auto current = weakSession.get();
		if (!current) {
			latch->countDown();
			return;
		}
		const auto data = current->data().photo(snapshot.id);
		const auto path = snapshot.path.isEmpty()
			? filePath(current, data)
			: snapshot.path;
		if (path.isEmpty()
			|| !QDir().mkpath(QFileInfo(path).absolutePath())) {
			latch->countDown();
			return;
		}
		const auto view = data->createMediaView();
		if (!view) {
			latch->countDown();
			return;
		}
		view->wanted(Data::PhotoSize::Large, origin);

		const auto saveToFiles = [=]
		{
			if (weakSession) {
				view->saveToFile(path);
			}
		};

		if (view->loaded()) {
			saveToFiles();
			latch->countDown();
			return;
		}

		current->downloaderTaskFinished() | rpl::filter([=]
		{
			return view->loaded();
		}) | rpl::on_next([=]
								  {
									  saveToFiles();
									  latch->countDown();
								  },
								  *lifetime);
	});

	const auto waitResult = WaitForDownload(
			*latch,
			std::chrono::minutes(5),
			cancelled);
	if (waitResult != DownloadWaitResult::Completed) {
		if (waitResult == DownloadWaitResult::TimedOut) {
			LOG(("AyuSync: photo loading timed out."));
		} else {
			LOG(("AyuSync: photo loading cancelled."));
		}
	}
	crl::on_main_sync([lifetime = base::take(lifetime)]
	{
		lifetime->destroy();
	});
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
	const auto peerId = ResolveTargetPeerId(session, targetHistory);
	if (!peerId) {
		return;
	}

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
	const auto peerId = ResolveTargetPeerId(session, targetHistory);
	if (!peerId) {
		return;
	}
	const auto voiceData = data;

	crl::on_main([=]
	{
		const auto current = weakSession.get();
		if (!current) {
			return;
		}
		const auto to = FileLoadTo(
			*peerId,
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
	const auto peerId = ResolveTargetPeerId(session, targetHistory);
	if (!peerId) {
		return false;
	}

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
		const auto peer = current->data().peer(*peerId);

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
