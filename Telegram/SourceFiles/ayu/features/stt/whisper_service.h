// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#pragma once

#include "base/timer.h"

#include <functional>
#include <vector>
#include <atomic>
#include <QtCore/QMutex>
#include <QtCore/QString>

struct whisper_context;

namespace Ayu::STT {

class WhisperService {
public:
	static WhisperService &instance();

	void transcribeOnDemand(
		const QString &filePath,
		const QString &modelPath,
		qint64 modelSize,
		const QString &modelSha256,
		const QString &language,
		std::function<void(QString)> callback);
	static std::vector<float> decodeAudioToPcm(const QString &filePath);
	void scheduleFreeContext();
	void freeContext();
	bool isQuitPrevent();

private:
	WhisperService();
	void releaseContext(whisper_context *context);

	QMutex _ctxMutex;
	whisper_context *_cachedCtx = nullptr; // guarded by _ctxMutex
	QString _cachedModelPath; // guarded by _ctxMutex
	std::atomic<int> _activeJobs = 0;
	base::Timer _idleTimer; // main thread only
	bool _freeInFlight = false; // main thread only
};

} // namespace Ayu::STT
