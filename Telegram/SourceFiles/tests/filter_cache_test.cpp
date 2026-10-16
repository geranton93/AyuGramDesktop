#include "ayu/features/filters/filter_cache.h"

#include <cstdio>
#include <cstdlib>
#include <memory>

namespace {

void Require(bool condition) {
	if (!condition) {
		std::abort();
	}
}

} // namespace

int main() {
	using namespace FiltersCacheController;
	auto cache = MessageCache();
	auto owner = std::make_unique<base::has_weak_ptr>();
	auto other = base::has_weak_ptr();
	const auto alice = DialogKey(1, 42);
	const auto bob = DialogKey(2, 42);
	Require(!cache.lookup(alice, owner.get(), 7).has_value());
	cache.put(alice, owner.get(), 7, true);
	Require(cache.lookup(alice, owner.get(), 7) == true);
	Require(!cache.lookup(bob, &other, 7).has_value());
	cache.put(bob, &other, 7, false);
	Require(cache.lookup(bob, &other, 7) == false);
	Require(cache.hasFiltered(alice, owner.get()));
	Require(!cache.hasFiltered(bob, &other));
	cache.remove(alice, owner.get(), 7);
	Require(!cache.lookup(alice, owner.get(), 7).has_value());
	cache.put(alice, owner.get(), 7, true);
	for (auto id = 1000; id != 1000 + MessageCache::kMaxMessages; ++id) {
		cache.put(alice, owner.get(), id, false);
	}
	Require(!cache.lookup(alice, owner.get(), 7).has_value());
	// A filtered match that gets evicted is not a permanent fact about the
	// dialog. It falls back to recomputation, so the action must not linger.
	Require(!cache.hasFiltered(alice, owner.get()));
	cache.markBlocked(alice, owner.get());
	Require(cache.hasFiltered(alice, owner.get()));

	for (auto id = 0; id != MessageCache::kMaxMessages + 1; ++id) {
		cache.put(alice, owner.get(), id, false);
	}
	Require(!cache.lookup(alice, owner.get(), 0).has_value());
	Require(cache.lookup(alice, owner.get(), MessageCache::kMaxMessages) == false);
	// Eviction of message results must not erase the blocked marker.
	Require(cache.hasFiltered(alice, owner.get()));
	for (auto id = 0; id != MessageCache::kMaxDialogs; ++id) {
		cache.markBlocked(DialogKey(1, id + 100), owner.get());
	}
	Require(cache.size() == MessageCache::kMaxDialogs);
	// alice was cached and then evicted, so it keeps the conservative answer
	// even though its entry is gone.
	Require(cache.hasFiltered(alice, owner.get()));
	// A dialog that never held a result must not inherit it from the eviction.
	Require(!cache.hasFiltered(DialogKey(1, 9999), owner.get()));
	// Nor may a different account, even for a key the first account cached.
	Require(!cache.hasFiltered(DialogKey(2, 101), owner.get()));
	cache.put(alice, owner.get(), 7, true);
	owner.reset();
	// The same account/peer key must miss after session replacement.
	Require(!cache.lookup(alice, &other, 7).has_value());
	Require(!cache.hasFiltered(alice, &other));
	cache.markBlocked(alice, &other);
	cache.clear();
	Require(cache.size() == 0);
	Require(!cache.hasFiltered(alice, &other));

	auto reusedOwner = std::optional<base::has_weak_ptr>(std::in_place);
	const auto address = &*reusedOwner;
	cache.put(alice, address, 7, true);
	cache.markBlocked(alice, address);
	reusedOwner.reset();
	reusedOwner.emplace();
	Require(&*reusedOwner == address);
	Require(!cache.lookup(alice, &*reusedOwner, 7).has_value());
	Require(!cache.hasFiltered(alice, &*reusedOwner));

	auto shown = ShownDialogs();
	shown.toggle(alice);
	Require(shown.contains(alice));
	Require(!shown.contains(bob));
	for (auto id = 0; id != MessageCache::kMaxDialogs + 1; ++id) {
		shown.toggle(DialogKey(1, id + 100));
	}
	Require(shown.contains(alice));
	shown.toggle(alice);
	Require(!shown.contains(alice));
	std::puts("filter_cache_test: bounds, isolation, lifetime, eviction scope, "
		"blocked-marker, overrides passed");
}
