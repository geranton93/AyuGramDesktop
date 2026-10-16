// This is the source code of AyuGram for Desktop.
// Copyright @Radolyn, 2026
#pragma once

#include "unicode/regex.h"

#include <memory>

namespace FiltersController {

// ICU limits execution steps in approximate milliseconds, not wall-clock time.
// This is a per-pattern bound; a list of patterns is not a single 10ms budget.
inline constexpr auto kRegexTimeLimit = 10;

[[nodiscard]] inline bool MatchPattern(
		const icu::RegexPattern &pattern,
		bool reversed,
		const icu::UnicodeString &text,
		UErrorCode &status) {
	const auto matcher = std::unique_ptr<icu::RegexMatcher>(
		pattern.matcher(text, status));
	if (U_FAILURE(status) || !matcher) {
		return false;
	}
	matcher->setTimeLimit(kRegexTimeLimit, status);
	if (U_FAILURE(status)) {
		return false;
	}
	const auto match = matcher->find(status);
	// Errors must not become matches when the filter is reversed.
	return U_SUCCESS(status) && (reversed ? !match : match);
}

} // namespace FiltersController
