// This is the source code of AyuGram for Desktop.

#include "ayu/features/forward/ayu_sync.h"
#include "ayu/features/forward/ayu_sync_wait.h"

#include "ayu/utils/ayu_mapper.h"
#include "ayu/utils/telegram_helpers.h"
#include "base/base_file_utilities.h"
#include "base/weak_ptr.h"
#include "core/application.h"
#include "core/core_settings.h"
#include "core/file_utilities.h"
#include "data/data_document.h"
#include "data/data_file_origin.h"
#include "data/data_photo.h"
#include "data/data_photo_media.h"
#include "data/data_session.h"
#include "main/main_session.h"
#include "storage/localimageloader.h"

#include <QtCore/QDir>
#include <QtCore/QFile>
#include <QtCore/QFileInfo>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <optional>
#include <utility>

namespace AyuSync {
namespace {

constexpr auto kDocumentDownloadTimeout = std::chrono::minutes(15);
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

struct ActiveDocumentDownload {
	QString path;
	QString readyPath;
	TimedCountDownLatch done{ 1 };
	rpl::lifetime lifetime;
	base::weak_ptr<Main::Session> session;
	std::mutex mutex;
	int consumers = 1;
	bool allowSizeFallback = false;
	bool started = false;
	std::atomic_bool finished = false;
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
		DocumentDownloadKey key,
		base::weak_ptr<Main::Session> weakSession) {
	const auto lock = std::lock_guard(DocumentDownloadsMutex());
	auto &active = DocumentDownloads();
	const auto i = active.find(key);
	if (i != active.end()) {
		if (!i->second->finished.load(std::memory_order_acquire)) {
			++i->second->consumers;
			return { i->second, false };
		}
		active.erase(i);
	}
	auto state = std::make_shared<ActiveDocumentDownload>();
	state->session = std::move(weakSession);
	active.emplace(key, state);
	return { std::move(state), true };
}

[[nodiscard]] bool FinishDocumentDownload(
		DocumentDownloadKey key,
		const std::shared_ptr<ActiveDocumentDownload> &state,
		bool ready,
		bool removePath = true,
		QString *finishedPath = nullptr) {
	auto path = QString();
	{
		const auto stateLock = std::lock_guard(state->mutex);
		if (state->finished.load(std::memory_order_acquire)) {
			return false;
		}
		state->finished.store(true, std::memory_order_release);
		path = state->path;
		if (finishedPath) {
			*finishedPath = path;
		}
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

[[nodiscard]] std::shared_ptr<ActiveDocumentDownload>
ReplacedDocumentDownload(
		DocumentDownloadKey key,
		const std::shared_ptr<ActiveDocumentDownload> &state) {
	const auto lock = std::lock_guard(DocumentDownloadsMutex());
	const auto i = DocumentDownloads().find(key);
	return (i != DocumentDownloads().end() && i->second != state)
		? i->second
		: nullptr;
}

void CancelDocumentDownload(
		DocumentDownloadKey key,
		const std::shared_ptr<ActiveDocumentDownload> &state) {
	auto path = QString();
	if (!FinishDocumentDownload(
			key,
			state,
			false,
			false,
			&path)) {
		return;
	}
	const auto weakSession = state->session;
	crl::on_main_sync([=] {
		const auto replacement = ReplacedDocumentDownload(key, state);
		auto replacementStarted = false;
		if (replacement) {
			const auto replacementLock = std::lock_guard(replacement->mutex);
			replacementStarted = replacement->started;
		}
		auto removePath = true;
		if (const auto current = weakSession.get()) {
			const auto document = current->data().document(key.documentId);
			const auto loadingPath = document->loadingFilePath();
			const auto loadedPath = document->filepath(true);
			const auto pathIsCurrent = !path.isEmpty()
				&& ((!loadingPath.isEmpty()
					&& SameFilePath(path, loadingPath))
					|| (!loadedPath.isEmpty()
						&& SameFilePath(path, loadedPath)));
			if (document->loading()
				&& !path.isEmpty()
				&& !loadingPath.isEmpty()
				&& SameFilePath(path, loadingPath)
				&& !replacementStarted) {
				document->cancel();
				removePath = false;
			} else {
				removePath = !pathIsCurrent;
			}
		}
		if (removePath && !path.isEmpty()) {
			QFile::remove(path);
		}
		state->lifetime.destroy();
	});
}

void ReleaseDocumentDownloadConsumer(
		DocumentDownloadKey key,
		const std::shared_ptr<ActiveDocumentDownload> &state) {
	auto cancel = false;
	{
		const auto lock = std::lock_guard(DocumentDownloadsMutex());
		Expects(state->consumers > 0);
		--state->consumers;
		if (state->consumers == 0) {
			auto &active = DocumentDownloads();
			const auto i = active.find(key);
			if (i == active.end()) {
				cancel = true;
			} else if (i->second == state) {
				active.erase(i);
				cancel = true;
			}
		}
	}
	if (cancel) {
		CancelDocumentDownload(key, state);
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
		.video = document->isVideoFile() || document->isGifv(),
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
	const auto registration = RegisterDocumentDownload(key, weakSession);
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
		CancelDocumentDownload(key, registration.state);
	});
	auto path = LoadedDocumentPath(session, document.id);
	if (FileHasExactSize(path, document.size)) {
		{
			const auto stateLock = std::lock_guard(registration.state->mutex);
			registration.state->path = path;
			registration.state->started = true;
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
				state->started = true;
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

} // namespace AyuSync
