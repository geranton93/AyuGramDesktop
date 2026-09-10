// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#pragma once

#include <functional>
#include <QtCore/QString>

namespace Ayu::STT {

class STTManager {
public:
	static STTManager &instance();
	void transcribe(
		const QString &filePath,
		std::function<void(QString)> callback,
		std::function<bool()> cancelled = {});
	static void requestPermission();
	[[nodiscard]] static QString modelPath(int modelType);
	[[nodiscard]] static QString modelUrl(int modelType);
	[[nodiscard]] static QString modelSha256(int modelType);
	[[nodiscard]] static qint64 modelSize(int modelType);
	[[nodiscard]] static qint64 maxAudioDurationMs();
	[[nodiscard]] static bool modelExists(int modelType);
	[[nodiscard]] static QString modelsDirectory();
	[[nodiscard]] static bool localEngineAvailable();

private:
	STTManager() = default;
};

} // namespace Ayu::STT
