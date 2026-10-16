#include "ayu/features/filters/filter_cache.h"
#include "rpl/event_stream.h"

#include <cstdio>
#include <cstdlib>
#include <memory>
#include <utility>

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
	const auto originalGroup = QVector<int64>{ 7, 8 };
	const auto groupDialog = DialogKey(1, 43);
	cache.put(groupDialog, owner.get(), 7, true, originalGroup);
	cache.put(groupDialog, owner.get(), 8, true, originalGroup);
	Require(cache.lookup(groupDialog, owner.get(), 7, originalGroup) == true);
	Require(cache.hasFiltered(groupDialog, owner.get()));
	Require(!cache.lookup(groupDialog, owner.get(), 8).has_value());
	Require(!cache.hasFiltered(groupDialog, owner.get()));
	Require(!cache.lookup(groupDialog, owner.get(), 7).has_value());
	cache.put(groupDialog, owner.get(), 8, false);
	Require(cache.lookup(groupDialog, owner.get(), 8) == false);
	Require(!cache.hasFiltered(groupDialog, owner.get()));
	const auto expandedGroup = QVector<int64>{ 70, 71 };
	const auto standaloneDialog = DialogKey(1, 44);
	cache.put(standaloneDialog, owner.get(), 70, true);
	cache.put(standaloneDialog, owner.get(), 71, true);
	Require(cache.hasFiltered(standaloneDialog, owner.get()));
	Require(!cache.lookup(
		standaloneDialog,
		owner.get(),
		70,
		expandedGroup).has_value());
	Require(!cache.hasFiltered(standaloneDialog, owner.get()));
	Require(!cache.lookup(
		standaloneDialog,
		owner.get(),
		71,
		expandedGroup).has_value());
	const auto evictedGroup = QVector<int64>{ 80, 81 };
	const auto changedEvictedGroup = QVector<int64>{ 80, 82 };
	const auto evictedGroupDialog = DialogKey(1, 45);
	cache.put(evictedGroupDialog, owner.get(), 80, true, evictedGroup);
	cache.put(evictedGroupDialog, owner.get(), 81, true, evictedGroup);
	for (auto id = 1000; id != 1000 + MessageCache::kMaxMessages; ++id) {
		cache.put(evictedGroupDialog, owner.get(), id, false);
	}
	Require(!cache.lookup(
		evictedGroupDialog,
		owner.get(),
		80,
		evictedGroup).has_value());
	Require(cache.hasFiltered(evictedGroupDialog, owner.get()));
	Require(!cache.lookup(
		evictedGroupDialog,
		owner.get(),
		80,
		changedEvictedGroup).has_value());
	Require(!cache.hasFiltered(evictedGroupDialog, owner.get()));
	cache.put(
		evictedGroupDialog,
		owner.get(),
		80,
		false,
		changedEvictedGroup);
	Require(!cache.hasFiltered(evictedGroupDialog, owner.get()));
	Require(!cache.lookup(evictedGroupDialog, owner.get(), 81).has_value());
	const auto evictedAlbumDialog = DialogKey(1, 46);
	const auto oldEvictedAlbum = QVector<int64>{ 85, 86 };
	cache.put(evictedAlbumDialog, owner.get(), 85, true, oldEvictedAlbum);
	cache.put(evictedAlbumDialog, owner.get(), 86, true, oldEvictedAlbum);
	for (auto id = 2000; id != 2000 + MessageCache::kMaxMessages; ++id) {
		cache.put(evictedAlbumDialog, owner.get(), id, false);
	}
	Require(!cache.lookup(evictedAlbumDialog, owner.get(), 86).has_value());
	Require(!cache.hasFiltered(evictedAlbumDialog, owner.get()));
	const auto evictedStandaloneDialog = DialogKey(1, 47);
	cache.put(evictedStandaloneDialog, owner.get(), 87, true);
	cache.put(evictedStandaloneDialog, owner.get(), 88, true);
	for (auto id = 3000; id != 3000 + MessageCache::kMaxMessages; ++id) {
		cache.put(evictedStandaloneDialog, owner.get(), id, false);
	}
	const auto newlyLoadedAlbum = QVector<int64>{ 87, 88 };
	Require(!cache.lookup(
		evictedStandaloneDialog,
		owner.get(),
		87,
		newlyLoadedAlbum).has_value());
	Require(!cache.hasFiltered(evictedStandaloneDialog, owner.get()));
	Require(!cache.lookup(
		evictedStandaloneDialog,
		owner.get(),
		88,
		newlyLoadedAlbum).has_value());
	const auto partiallyRefreshedDialog = DialogKey(1, 48);
	const auto previousAlbum = QVector<int64>{ 90, 91 };
	const auto currentAlbum = QVector<int64>{ 90, 91, 92 };
	cache.put(partiallyRefreshedDialog, owner.get(), 90, true, previousAlbum);
	cache.put(partiallyRefreshedDialog, owner.get(), 91, true, currentAlbum);
	Require(!cache.lookup(
		partiallyRefreshedDialog,
		owner.get(),
		90,
		currentAlbum).has_value());
	Require(cache.hasFiltered(partiallyRefreshedDialog, owner.get()));
	Require(cache.lookup(
		partiallyRefreshedDialog,
		owner.get(),
		91,
		currentAlbum) == true);
	Require(!cache.lookup(bob, &other, 7).has_value());
	cache.put(bob, &other, 7, false);
	Require(cache.lookup(bob, &other, 7) == false);
	cache.markBlocked(bob, &other, 7);
	cache.unmarkBlocked(bob, &other, 7);
	Require(cache.lookup(bob, &other, 7) == false);
	Require(!cache.hasFiltered(bob, &other));
	cache.put(bob, &other, 10, true);
	cache.put(bob, &other, 10, false);
	Require(cache.lookup(bob, &other, 10) == false);
	Require(!cache.hasFiltered(bob, &other));
	cache.markBlocked(bob, &other, 8);
	cache.markBlocked(bob, &other, 9);
	cache.unmarkBlocked(bob, &other, 8);
	Require(cache.hasFiltered(bob, &other));
	Require(cache.hasFiltered(alice, owner.get()));
	cache.remove(bob, &other, 9);
	Require(!cache.hasFiltered(bob, &other));
	cache.remove(alice, owner.get(), 7);
	Require(!cache.lookup(alice, owner.get(), 7).has_value());
	Require(!cache.hasFiltered(alice, owner.get()));
	const auto removedAlbumDialog = DialogKey(1, 49);
	const auto removedAlbum = QVector<int64>{ 90, 91, 92 };
	for (const auto message : removedAlbum) {
		cache.put(removedAlbumDialog, owner.get(), message, true, removedAlbum);
	}
	Require(cache.hasFiltered(removedAlbumDialog, owner.get()));
	cache.removeGroup(removedAlbumDialog, owner.get(), removedAlbum);
	Require(!cache.hasFiltered(removedAlbumDialog, owner.get()));
	for (const auto message : removedAlbum) {
		Require(!cache.lookup(
			removedAlbumDialog,
			owner.get(),
			message).has_value());
	}
	const auto trackedSession = 1;
	const auto trackedAlbumDialog = DialogKey(1, 50);
	const auto trackedAlbum = QVector<int64>{ 93, 94, 95 };
	auto removalEvents = rpl::event_stream<int64>();
	auto removalSubscriptions = ItemRemovalSubscriptions();
	auto removalCount = 0;
	auto duplicateRemovalCount = 0;
	const auto onRemoved = [&](int64 message) {
		++removalCount;
		cache.removeForItem(
			trackedAlbumDialog,
			owner.get(),
			message,
			trackedAlbum);
	};
	for (const auto message : trackedAlbum) {
		cache.put(trackedAlbumDialog, owner.get(), message, true, trackedAlbum);
	}
	cache.markBlocked(trackedAlbumDialog, owner.get(), trackedAlbum[1]);
	{
		auto lifetime = rpl::lifetime();
		removalSubscriptions.track(
			&trackedSession,
			removalEvents.events(),
			lifetime,
			onRemoved);
		removalSubscriptions.track(
			&trackedSession,
			removalEvents.events(),
			lifetime,
			[&](int64) { ++duplicateRemovalCount; });
		auto firstRemoved = trackedAlbum[0];
		removalEvents.fire(std::move(firstRemoved));
		Require(removalCount == 1);
		Require(duplicateRemovalCount == 0);
		Require(!cache.hasFiltered(trackedAlbumDialog, owner.get()));
	}
	auto secondRemoved = trackedAlbum[1];
	removalEvents.fire(std::move(secondRemoved));
	Require(removalCount == 1);
	{
		auto lifetime = rpl::lifetime();
		removalSubscriptions.track(
			&trackedSession,
			removalEvents.events(),
			lifetime,
			onRemoved);
		auto retriedRemoved = trackedAlbum[1];
		removalEvents.fire(std::move(retriedRemoved));
		Require(removalCount == 2);
	}
	cache.put(alice, owner.get(), 7, true);
	for (auto id = 1000; id != 1000 + MessageCache::kMaxMessages; ++id) {
		cache.put(alice, owner.get(), id, false);
	}
	Require(!cache.lookup(alice, owner.get(), 7).has_value());
	Require(cache.hasFiltered(alice, owner.get()));
	cache.remove(alice, owner.get(), 7);
	Require(!cache.hasFiltered(alice, owner.get()));
	cache.markBlocked(alice, owner.get(), 7);
	for (auto id = 2000; id != 2000 + MessageCache::kMaxMessages; ++id) {
		cache.put(alice, owner.get(), id, false);
	}
	Require(cache.hasFiltered(alice, owner.get()));
	cache.remove(alice, owner.get(), 7);
	Require(!cache.hasFiltered(alice, owner.get()));

	for (auto id = 0; id <= MessageCache::kMaxTrackedMatches; ++id) {
		cache.put(alice, owner.get(), id, true);
	}
	for (auto id = 0; id <= MessageCache::kMaxTrackedMatches; ++id) {
		cache.remove(alice, owner.get(), id);
	}
	Require(cache.hasFiltered(alice, owner.get()));
	cache.clear();
	for (auto id = 0; id <= MessageCache::kMaxTrackedMatches; ++id) {
		cache.markBlocked(alice, owner.get(), id);
	}
	for (auto id = 0; id <= MessageCache::kMaxTrackedMatches; ++id) {
		cache.unmarkBlocked(alice, owner.get(), id);
	}
	Require(cache.hasFiltered(alice, owner.get()));
	cache.clear();
	cache.markBlocked(alice, owner.get(), 7);

	for (auto id = 0; id != MessageCache::kMaxMessages + 1; ++id) {
		cache.put(alice, owner.get(), id, false);
	}
	Require(!cache.lookup(alice, owner.get(), 0).has_value());
	Require(cache.lookup(alice, owner.get(), MessageCache::kMaxMessages) == false);
	// Eviction of message results must not erase the blocked marker.
	Require(cache.hasFiltered(alice, owner.get()));
	for (auto id = 0; id != MessageCache::kMaxDialogs; ++id) {
		cache.markBlocked(DialogKey(1, id + 100), owner.get(), id);
	}
	Require(cache.size() == MessageCache::kMaxDialogs);
	Require(cache.hasFiltered(alice, owner.get()));
	cache.put(alice, owner.get(), 7, true);
	owner.reset();
	// The same account/peer key must miss after session replacement.
	Require(!cache.lookup(alice, &other, 7).has_value());
	Require(!cache.hasFiltered(alice, &other));
	cache.markBlocked(alice, &other, 7);
	cache.clear();
	Require(cache.size() == 0);
	Require(!cache.hasFiltered(alice, &other));

	auto fallbackCache = MessageCache();
	auto fallbackOwner = std::make_unique<base::has_weak_ptr>();
	fallbackCache.put(alice, fallbackOwner.get(), 99, true);
	for (auto id = 0; id != MessageCache::kMaxDialogs; ++id) {
		fallbackCache.put(DialogKey(3, id), fallbackOwner.get(), id, false);
	}
	fallbackCache.put(alice, fallbackOwner.get(), 7, true);
	fallbackCache.put(alice, fallbackOwner.get(), 7, false);
	Require(fallbackCache.lookup(alice, fallbackOwner.get(), 7) == false);
	Require(fallbackCache.hasFiltered(alice, fallbackOwner.get()));

	auto crossAccountCache = MessageCache();
	auto firstAccount = std::make_unique<base::has_weak_ptr>();
	auto secondAccount = std::make_unique<base::has_weak_ptr>();
	crossAccountCache.put(DialogKey(10, 1), firstAccount.get(), 7, true);
	for (auto id = 0; id != MessageCache::kMaxDialogs - 1; ++id) {
		crossAccountCache.put(DialogKey(11, id), secondAccount.get(), id, false);
	}
	crossAccountCache.put(
		DialogKey(11, MessageCache::kMaxDialogs),
		secondAccount.get(),
		MessageCache::kMaxDialogs,
		false);
	Require(crossAccountCache.size() == MessageCache::kMaxDialogs);
	Require(crossAccountCache.hasFiltered(
		DialogKey(10, 1),
		firstAccount.get()));
	Require(!crossAccountCache.hasFiltered(
		DialogKey(10, 2),
		firstAccount.get()));
	Require(!crossAccountCache.hasFiltered(
		DialogKey(11, MessageCache::kMaxDialogs + 1),
		secondAccount.get()));

	auto overflowCache = MessageCache();
	auto overflowOwner = std::make_unique<base::has_weak_ptr>();
	for (auto id = 0;
		id != MessageCache::kMaxDialogs + MessageCache::kMaxEvictedDialogs + 1;
		++id) {
		overflowCache.put(DialogKey(12, id), overflowOwner.get(), id, true);
	}
	Require(overflowCache.hasFiltered(
		DialogKey(12, MessageCache::kMaxDialogs
			+ MessageCache::kMaxEvictedDialogs + 2),
		overflowOwner.get()));
	Require(!overflowCache.hasFiltered(
		DialogKey(13, 1),
		secondAccount.get()));

	auto reusedOwner = std::optional<base::has_weak_ptr>(std::in_place);
	const auto address = &*reusedOwner;
	cache.put(alice, address, 7, true);
	cache.markBlocked(alice, address, 7);
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
	std::puts("filter_cache_test: bounds, isolation, lifetime, overrides passed");
}
