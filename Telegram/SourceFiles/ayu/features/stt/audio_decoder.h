// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#pragma once

#include <QtCore/QString>

#include <vector>

namespace Ayu::STT {

[[nodiscard]] std::vector<float> decodeAudioToPcm(const QString &filePath);

} // namespace Ayu::STT
