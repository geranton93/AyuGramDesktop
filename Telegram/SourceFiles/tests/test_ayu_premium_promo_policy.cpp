/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#include "ayu/premium_promo_policy.h"

int main() {
	if (Ayu::ShouldShowNonPremiumLimitPromo(true, false)) {
		return 1;
	}
	if (Ayu::ShouldShowNonPremiumLimitPromo(true, true)) {
		return 2;
	}
	if (!Ayu::ShouldShowNonPremiumLimitPromo(false, false)) {
		return 3;
	}
	if (Ayu::ShouldShowNonPremiumLimitPromo(false, true)) {
		return 4;
	}
	return 0;
}
