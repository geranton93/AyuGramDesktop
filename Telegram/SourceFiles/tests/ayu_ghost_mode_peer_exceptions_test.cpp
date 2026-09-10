// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/ghost_mode_peer_exceptions.h"

#include <cstdint>
#include <cstdlib>
#include <optional>

namespace {

void Require(bool condition) {
	if (!condition) {
		std::abort();
	}
}

} // namespace

int main() {
	using Exceptions = Ayu::GhostModePeerExceptions;

	constexpr auto alice = std::uint64_t(101);
	constexpr auto bob = std::uint64_t(202);

	auto exceptions = Exceptions();
	Require(exceptions.shouldSend(true, std::nullopt));
	Require(exceptions.shouldSend(true, alice));
	Require(!exceptions.shouldSend(false, std::nullopt));
	Require(!exceptions.shouldSend(false, alice));
	Require(!exceptions.set(0, true));

	Require(exceptions.set(alice, true));
	Require(!exceptions.set(alice, true));
	Require(exceptions.contains(alice));
	Require(exceptions.shouldSend(false, alice));
	Require(!exceptions.shouldSend(false, bob));

	Require(exceptions.set(alice, false));
	Require(!exceptions.set(alice, false));
	Require(!exceptions.contains(alice));
	Require(!exceptions.shouldSend(false, alice));

	Exceptions::Values values;
	values.emplace(0);
	values.emplace(alice);
	for (auto id = std::uint64_t(1); id <= 300; ++id) {
		values.emplace(id + 1000);
	}
	exceptions.replace(std::move(values));
	Require(exceptions.values().size() == Exceptions::kMaxValues);
	Require(!exceptions.contains(0));
}
