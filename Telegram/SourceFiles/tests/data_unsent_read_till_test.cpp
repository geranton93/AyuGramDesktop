/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#include "data/data_unsent_read_till.h"

#include <cstdint>
#include <cstdlib>

namespace {

void Require(bool condition) {
	if (!condition) {
		std::abort();
	}
}

} // namespace

int main() {
	using State = Data::UnsentReadTill<std::uint64_t>;

	auto state = State();
	Require(!state);
	Require(state.with(10) == 10);

	state.add(0);
	Require(!state);
	state.add(10);
	state.add(5);
	Require(static_cast<bool>(state));
	Require(state.till() == 10);
	Require(state.with(7) == 10);
	Require(state.with(12) == 12);

	state.sent(9);
	Require(state.till() == 10);
	state.sent(10);
	Require(!state);

	state.add(15);
	state.sent(20);
	Require(!state);
}
