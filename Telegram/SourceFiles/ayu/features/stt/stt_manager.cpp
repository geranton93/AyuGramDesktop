// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#include "ayu/features/stt/stt_manager.h"

#include "ayu/ayu_settings.h"
#include "ayu/features/stt/download_helper.h"

#include <QtCore/QFileInfo>
#include <QtCore/QStandardPaths>

#include <array>
#include <mutex>

#if defined(Q_OS_MAC)
#include "ayu/features/stt/platform/stt_mac.h"
#endif

#if defined(HAVE_WHISPER)
#include "ayu/features/stt/whisper_service.h"
#endif

namespace Ayu::STT {

namespace {

struct ModelSpec {
	const char *fileName;
	const char *sha256;
	qint64 size;
	const char *url;
};

constexpr std::array<ModelSpec, 3> kModels = {{
	{
		"ggml-tiny.bin",
		"be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21",
		77691713,
		"https://huggingface.co/ggerganov/whisper.cpp/resolve/"
		"5359861c739e955e79d9a303bcbc70fb988958b1/ggml-tiny.bin",
	},
	{
		"ggml-base.bin",
		"60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
		147951465,
		"https://huggingface.co/ggerganov/whisper.cpp/resolve/"
		"5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base.bin",
	},
	{
		"ggml-small.bin",
		"1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
		487601967,
		"https://huggingface.co/ggerganov/whisper.cpp/resolve/"
		"5359861c739e955e79d9a303bcbc70fb988958b1/ggml-small.bin",
	},
}};

struct ModelValidation final {
	QString path;
	qint64 size = 0;
	qint64 modified = 0;
	bool valid = false;
};

std::array<ModelValidation, kModels.size()> ModelValidationCache;
std::mutex ModelValidationCacheMutex;

[[nodiscard]] const ModelSpec *Model(int modelType) {
	return (modelType >= 0
		&& modelType < static_cast<int>(kModels.size()))
		? &kModels[modelType]
		: nullptr;
}

} // namespace

STTManager &STTManager::instance() {
	static STTManager self;
	return self;
}

QString STTManager::modelsDirectory() {
	const auto base = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
	return base.isEmpty() ? QString() : base + u"/whisper_models"_q;
}

QString STTManager::modelPath(const int modelType) {
	const auto model = Model(modelType);
	if (!model) {
		return {};
	}
	const auto directory = modelsDirectory();
	return directory.isEmpty() ? QString() : directory + u"/"_q + model->fileName;
}

QString STTManager::modelUrl(const int modelType) {
	const auto model = Model(modelType);
	return model ? QString::fromUtf8(model->url) : QString();
}

QString STTManager::modelSha256(const int modelType) {
	const auto model = Model(modelType);
	return model ? QString::fromUtf8(model->sha256) : QString();
}

qint64 STTManager::modelSize(const int modelType) {
	const auto model = Model(modelType);
	return model ? model->size : 0;
}

qint64 STTManager::maxAudioDurationMs() {
	return 15 * 60 * 1000;
}

bool STTManager::modelExists(const int modelType) {
	const auto model = Model(modelType);
	const auto path = modelPath(modelType);
	const auto expectedSize = modelSize(modelType);
	if (!model || path.isEmpty() || expectedSize <= 0) {
		return false;
	}
	const auto info = QFileInfo(path);
	if (!info.isFile() || info.size() != expectedSize) {
		return false;
	}
	const auto modified = info.lastModified().toMSecsSinceEpoch();
	{
		const auto lock = std::lock_guard(ModelValidationCacheMutex);
		const auto &cached = ModelValidationCache[modelType];
		if (cached.path == path
			&& cached.size == expectedSize
			&& cached.modified == modified) {
			return cached.valid;
		}
	}

	const auto valid = VerifyModelFile(
		path,
		expectedSize,
		QString::fromUtf8(model->sha256));
	{
		const auto lock = std::lock_guard(ModelValidationCacheMutex);
		auto &cached = ModelValidationCache[modelType];
		cached.path = path;
		cached.size = expectedSize;
		cached.modified = modified;
		cached.valid = valid;
	}
	return valid;
}

bool STTManager::localEngineAvailable() {
#if defined(Q_OS_MAC)
	if (AyuSettings::getInstance().sttEngine() == STTEngine::AppleSpeech) {
		return Mac::isAvailable();
	}
#endif
#if defined(HAVE_WHISPER)
	return true;
#else
	return false;
#endif
}

void STTManager::requestPermission() {
#if defined(Q_OS_MAC)
	if (AyuSettings::getInstance().sttEngine() == STTEngine::AppleSpeech) {
		Mac::requestSpeechPermission();
	}
#endif
}

void STTManager::transcribe(
		const QString &filePath,
		std::function<void(QString)> callback,
		std::function<bool()> cancelled) {
	const auto &settings = AyuSettings::getInstance();

#if defined(Q_OS_MAC)
	if (settings.sttEngine() == STTEngine::AppleSpeech) {
		Mac::transcribeFile(
			filePath,
			settings.sttLanguage(),
			std::move(callback),
			std::move(cancelled));
		return;
	}
#endif

#if defined(HAVE_WHISPER)
	const auto modelType = static_cast<int>(settings.whisperModelType());
	const auto path = modelPath(modelType);
	const auto language = settings.sttLanguage();
	if (!modelExists(modelType)) {
		InvokeCallbackSafely(callback);
		return;
	}

	WhisperService::instance().transcribeOnDemand(
		filePath,
		path,
		modelSize(modelType),
		modelSha256(modelType),
		language,
		std::move(callback),
		std::move(cancelled));
	return;
#endif

	// No engine available for the selected configuration: report failure
	// instead of silently dropping the callback (which would hang the spinner).
	InvokeCallbackSafely(callback);
}

} // namespace Ayu::STT
