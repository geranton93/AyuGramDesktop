// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#include "ayu/features/stt/stt_transcribe_provider.h"

#include "ayu/ayu_settings.h"
#include "ayu/features/stt/download_helper.h"
#include "ayu/features/stt/stt_manager.h"
#include "base/timer.h"
#include "base/weak_ptr.h"
#include "data/data_document.h"
#include "data/data_file_origin.h"
#include "data/data_session.h"
#include "history/history.h"
#include "history/history_item.h"
#include "main/main_session.h"

#include <QtCore/QFile>
#include <QtCore/QFileInfo>
#include <QtCore/QSaveFile>
#include <QtCore/QStandardPaths>
#include <QtCore/QUuid>

#include <memory>
#include <new>
#include <utility>

#include <rpl/rpl.h>

namespace Ayu::STT {
namespace {

constexpr crl::time kFileDownloadTimeoutMs = 60000;
constexpr qint64 kMaxTranscriptionFileBytes = 256 * 1024 * 1024;
constexpr qint64 kCopyChunkSize = 1024 * 1024;

void RemoveTemporaryFiles(
		const QString &first,
		const QString &second = QString()) {
	if (!first.isEmpty()) {
		QFile::remove(first);
	}
	if (!second.isEmpty() && second != first) {
		QFile::remove(second);
	}
}

struct DownloadWait {
	rpl::lifetime lifetime;
	base::Timer timeout;
	bool finished = false;
};

[[nodiscard]] bool CopyForTranscription(
		const QString &sourcePath,
		const QString &destinationPath) {
	try {
		const auto sourceInfo = QFileInfo(sourcePath);
		if (!sourceInfo.isFile()
			|| sourceInfo.size() <= 0
			|| sourceInfo.size() > kMaxTranscriptionFileBytes) {
			return false;
		}
		QFile source(sourcePath);
		if (!source.open(QIODevice::ReadOnly)) {
			return false;
		}
		QSaveFile destination(destinationPath);
		if (!destination.open(QIODevice::WriteOnly)) {
			return false;
		}

		qint64 copied = 0;
		while (!source.atEnd()) {
			const auto chunk = source.read(kCopyChunkSize);
			if (chunk.isEmpty()) {
				return source.error() == QFileDevice::NoError
					&& copied == sourceInfo.size()
					&& destination.commit();
			}
			if (copied > sourceInfo.size() - chunk.size()
				|| destination.write(chunk) != chunk.size()) {
				return false;
			}
			copied += chunk.size();
		}
		return copied == sourceInfo.size() && destination.commit();
	} catch (const std::bad_alloc &) {
		return false;
	}
}

} // namespace

bool ShouldTranscribeLocally(const not_null<HistoryItem*> item) {
	if (!item->isHistoryEntry() || item->isLocal()) {
		return false;
	}
	if (!AyuSettings::getInstance().sttEnabled()) {
		return false;
	}
	if (!STTManager::localEngineAvailable()) {
		return false;
	}
	const auto media = item->media();
	const auto doc = media ? media->document() : nullptr;
	if (!doc || (!doc->isVoiceMessage() && !doc->isVideoMessage())) {
		return false;
	}
#if defined(Q_OS_MAC)
	if (AyuSettings::getInstance().sttEngine() == STTEngine::AppleSpeech) {
		return true;
	}
#endif
	return true;
}

void RequestLocalTranscribe(
		const not_null<HistoryItem*> item,
		const std::function<void(QString)> &done) {
	std::function<void(QString)> completion;
	try {
		completion = done;
	} catch (const std::bad_alloc &) {
		InvokeCallbackSafely(done, QString());
		return;
	}
	const auto session = &item->history()->session();
	const auto id = item->fullId();
	const auto media = item->media();
	const auto doc = media ? media->document() : nullptr;
	if (!doc) {
		InvokeCallbackSafely(completion, QString());
		return;
	}

	const auto weakSession = base::make_weak(session);
	const auto runEngine = [=](
			QString filePath,
			QString cleanupPath = QString(),
			QString sourceCleanupPath = QString()) {
		if (!QFileInfo(filePath).isFile()) {
			RemoveTemporaryFiles(cleanupPath, sourceCleanupPath);
			if (weakSession) {
				InvokeCallbackSafely(completion, QString());
			}
			return;
		}
		try {
			STTManager::instance().transcribe(
				filePath,
				[=](QString text) {
					RemoveTemporaryFiles(cleanupPath, sourceCleanupPath);
					if (weakSession) {
						InvokeCallbackSafely(completion, std::move(text));
					}
				},
				[weakSession] { return !weakSession; });
		} catch (...) {
			RemoveTemporaryFiles(cleanupPath, sourceCleanupPath);
			if (weakSession) {
				InvokeCallbackSafely(completion, QString());
			}
		}
	};

	if (const auto ready = doc->filepath(true);
		!ready.isEmpty() && QFileInfo(ready).isFile()) {
		runEngine(ready);
		return;
	}

	std::shared_ptr<DownloadWait> state;
	try {
		state = std::make_shared<DownloadWait>();
	} catch (const std::bad_alloc &) {
		InvokeCallbackSafely(completion, QString());
		return;
	}
	const auto weakState = std::weak_ptr<DownloadWait>(state);
	const auto waitForDownload = [=](bool temporary, QString temporaryPath) {
		const auto finish = [=](QString path) {
			const auto current = weakState.lock();
			if (!current || current->finished) {
				return;
			}
			current->finished = true;
			current->timeout.cancel();
			current->lifetime.destroy();
			if (temporary && path.isEmpty()) {
				RemoveTemporaryFiles(temporaryPath);
				if (weakSession) {
					InvokeCallbackSafely(completion, QString());
				}
				return;
			}
			if (temporary) {
				const auto directory = QStandardPaths::writableLocation(
					QStandardPaths::TempLocation);
				if (directory.isEmpty()) {
					RemoveTemporaryFiles(temporaryPath);
					if (weakSession) {
						InvokeCallbackSafely(completion, QString());
					}
					return;
				}
				const auto enginePath = directory
					+ u"/ayugram_stt_engine_%1_%2.oga"_q.arg(
						QString::number(id.msg.bare),
						QUuid::createUuid().toString(QUuid::WithoutBraces));
				if (CopyForTranscription(path, enginePath)) {
					runEngine(enginePath, enginePath, temporaryPath);
				} else {
					RemoveTemporaryFiles(enginePath, temporaryPath);
					if (weakSession) {
						InvokeCallbackSafely(completion, QString());
						}
				}
			} else {
				runEngine(path);
			}
		};

		session->downloaderTaskFinished(
		) | rpl::on_next([state, weakSession, doc, finish] {
			if (state->finished || !weakSession) {
				return;
			}
			if (doc->loading()) {
				return;
			}
			const auto ready = doc->filepath(true);
			finish(
				!ready.isEmpty() && QFileInfo(ready).isFile()
					? ready
					: QString());
		}, state->lifetime);

		state->timeout.setCallback([=] {
			if (const auto current = weakState.lock();
				current && !current->finished) {
				current->finished = true;
				current->lifetime.destroy();
				if (temporary) {
					if (weakSession
						&& doc->loading()
						&& doc->loadingFilePath() == temporaryPath) {
						doc->cancel();
					}
					RemoveTemporaryFiles(temporaryPath);
				}
				if (weakSession) {
					InvokeCallbackSafely(completion, QString());
				}
			}
		});
		state->timeout.callOnce(kFileDownloadTimeoutMs);
	};

	if (doc->loading()) {
		waitForDownload(false, QString());
		return;
	}

	const auto tempDirectory = QStandardPaths::writableLocation(
		QStandardPaths::TempLocation);
	if (tempDirectory.isEmpty()) {
		InvokeCallbackSafely(completion, QString());
		return;
	}
	const auto tempPath = tempDirectory
		+ u"/ayugram_stt_%1_%2_%3_%4.oga"_q.arg(
			QString::number(session->uniqueId()),
			QString::number(item->history()->peer->id.value),
			QString::number(id.msg.bare),
			QUuid::createUuid().toString(QUuid::WithoutBraces));
	QFile::remove(tempPath);
	doc->save(id, tempPath);
	if (const auto ready = doc->filepath(true);
		!ready.isEmpty() && QFileInfo(ready).isFile()) {
		const auto enginePath = tempDirectory
			+ u"/ayugram_stt_engine_%1_%2.oga"_q.arg(
				QString::number(id.msg.bare),
				QUuid::createUuid().toString(QUuid::WithoutBraces));
		if (CopyForTranscription(ready, enginePath)) {
			runEngine(enginePath, enginePath, tempPath);
		} else {
			RemoveTemporaryFiles(enginePath, tempPath);
			InvokeCallbackSafely(completion, QString());
		}
		return;
	}
	if (!doc->loading()) {
		RemoveTemporaryFiles(tempPath);
		InvokeCallbackSafely(completion, QString());
		return;
	}
	waitForDownload(true, tempPath);
}

} // namespace Ayu::STT
