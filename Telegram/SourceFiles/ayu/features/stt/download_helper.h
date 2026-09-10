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

inline constexpr qint64 kUnknownExpectedDownloadSize = -1;

[[nodiscard]] bool IsValidSha256Hex(const QString &value);

void InvokeCallbackSafely(
	const std::function<void(QString)> &callback,
	QString text = {});

[[nodiscard]] bool VerifyModelFile(
	const QString &path,
	qint64 expectedSize,
	const QString &expectedSha256);

void DownloadWithProgress(
	const QString &url,
	const QString &destPath,
	const QString &sha256Expected,
	const std::function<void(int percent)> &onProgress,
	const std::function<void(bool ok)> &onDone);

void DownloadWithProgress(
	const QString &url,
	const QString &destPath,
	const QString &sha256Expected,
	qint64 expectedSize,
	const std::function<void(int percent)> &onProgress,
	const std::function<void(bool ok)> &onDone);

} // namespace Ayu::STT
