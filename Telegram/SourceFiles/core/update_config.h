/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#pragma once

#include <QtCore/QString>
#include <QtCore/QUrl>

namespace Core::UpdateConfig {

inline constexpr char kRepository[] = "geranton93/AyuGramDesktop";
inline constexpr char kFeedPrefix[] =
	"https://raw.githubusercontent.com/geranton93/AyuGramDesktop/dev/updates/";
inline constexpr char kReleaseDownloadPrefix[] =
	"https://github.com/geranton93/AyuGramDesktop/releases/download/";
inline constexpr char kReleasePage[] =
	"https://github.com/geranton93/AyuGramDesktop/releases";

inline constexpr bool kUseMtprotoFallback = false;
inline constexpr bool kAllowLegacyV1 = false;

[[nodiscard]] inline bool IsSafeReleaseComponent(const QString &component) {
	if (component.isEmpty()) {
		return false;
	}
	for (const auto character : component) {
		const auto ascii = character.unicode();
		if (!((ascii >= 'a' && ascii <= 'z')
			|| (ascii >= 'A' && ascii <= 'Z')
			|| (ascii >= '0' && ascii <= '9')
			|| ascii == '.'
			|| ascii == '_'
			|| ascii == '-')) {
			return false;
		}
	}
	return true;
}

[[nodiscard]] inline bool IsForkReleaseUrl(const QString &url) {
	const auto prefix = QString::fromLatin1(kReleaseDownloadPrefix);
	if (!url.startsWith(prefix)
		|| url.contains('?')
		|| url.contains('#')) {
		return false;
	}

	const auto parsed = QUrl(url);
	if (!parsed.isValid()
		|| parsed.scheme() != QStringLiteral("https")
		|| parsed.host() != QStringLiteral("github.com")
		|| !parsed.userInfo().isEmpty()
		|| parsed.port() != -1) {
		return false;
	}

	const auto components = url.mid(prefix.size()).split('/');
	return components.size() == 2
		&& components.front().startsWith('v')
		&& IsSafeReleaseComponent(components.front())
		&& IsSafeReleaseComponent(components.back());
}

} // namespace Core::UpdateConfig
