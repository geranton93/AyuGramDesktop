// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

namespace Ayu {

[[nodiscard]] constexpr bool ShouldShowNonPremiumLimitPromo(
		bool adsDisabled,
		bool premium) {
	return !adsDisabled && !premium;
}

} // namespace Ayu
