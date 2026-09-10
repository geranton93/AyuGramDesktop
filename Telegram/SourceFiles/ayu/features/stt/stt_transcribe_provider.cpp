// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#include "ayu/features/stt/stt_transcribe_provider.h"

#include "ayu/ayu_settings.h"
#include "ayu/features/stt/stt_manager.h"
#include "base/timer.h"
#include "base/debug_log.h"
#include "base/weak_ptr.h"
#include "data/data_document.h"
#include "data/data_file_origin.h"
#include "data/data_session.h"
#include "history/history.h"
#include "history/history_item.h"
#include "main/main_session.h"

#include <QtCore/QFile>
#include <QtCore/QFileInfo>
#include <QtCore/QStandardPaths>
#include <QtCore/QUuid>

#include <memory>
#include <new>
#include <utility>

#include <rpl/rpl.h>

namespace Ayu::STT {
namespace {

constexpr crl::time kFileDownloadTimeoutMs = 60000;

struct DownloadWait {
	rpl::lifetime lifetime;
	base::Timer timeout;
	bool finished = false;
};

void InvokeCallbackSafely(
		const std::function<void(QString)> &callback,
		QString text) {
	if (!callback) {
		return;
	}
	try {
		callback(std::move(text));
	} catch (...) {
		LOG(("RequestLocalTranscribe: completion callback failed"));
	}
}

} // namespace

bool ShouldTranscribeLocally(const not_null<HistoryItem*> item) {
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
	const auto runEngine = [=](const QString &filePath, bool temporary) {
		if (!QFileInfo(filePath).isFile()) {
			if (temporary) {
				QFile::remove(filePath);
			}
			if (weakSession) {
				InvokeCallbackSafely(completion, QString());
			}
			return;
		}
		try {
			STTManager::instance().transcribe(filePath, [=](QString text) {
				if (temporary) {
					QFile::remove(filePath);
				}
				if (weakSession) {
					InvokeCallbackSafely(completion, std::move(text));
				}
			});
		} catch (...) {
			if (temporary) {
				QFile::remove(filePath);
			}
			if (weakSession) {
				InvokeCallbackSafely(completion, QString());
			}
		}
	};

	if (const auto ready = doc->filepath(true);
		!ready.isEmpty() && QFileInfo(ready).isFile()) {
		runEngine(ready, false);
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
			runEngine(path, temporary);
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
				if (weakSession && temporary && doc->loading()) {
					doc->cancel();
				}
				if (!temporaryPath.isEmpty()) {
					QFile::remove(temporaryPath);
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
		runEngine(ready, true);
		return;
	}
	if (!doc->loading()) {
		QFile::remove(tempPath);
		InvokeCallbackSafely(completion, QString());
		return;
	}
	waitForDownload(true, tempPath);
}

} // namespace Ayu::STT
