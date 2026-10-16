// This is the source code of AyuGram for Desktop.
// Copyright @Radolyn, 2026
#pragma once

#include "base/weak_ptr.h"

#include <QCache>
#include <QSet>

#include <optional>
#include <utility>

namespace FiltersCacheController {

using DialogKey = std::pair<uint64, uint64>;

// Callers serialize access. A live session guard prevents a reused account key
// from serving results produced before logout, even if addresses are reused.
class MessageCache final {
public:
	static constexpr auto kMaxDialogs = 128;
	static constexpr auto kMaxMessages = 512;
	// Dialogs remembered for the conservative answer after _entries evicted
	// them. Kept several times wider than _entries so the answer survives a
	// browsing session that revisits more dialogs than the entry cache holds.
	static constexpr auto kMaxSeenDialogs = 4 * kMaxDialogs;

	[[nodiscard]] std::optional<bool> lookup(
			DialogKey key,
			base::has_weak_ptr *owner,
			int64 message) {
		const auto entry = find(key, owner);
		const auto result = entry ? entry->messages.object(message) : nullptr;
		return result ? std::optional<bool>(*result) : std::nullopt;
	}

	void put(DialogKey key, base::has_weak_ptr *owner, int64 message, bool value) {
		const auto entry = ensure(key, owner);
		if (const auto existing = entry->messages.object(message)) {
			*existing = value;
		} else {
			entry->messages.insert(message, new bool(value));
		}
	}

	void markBlocked(DialogKey key, base::has_weak_ptr *owner) {
		ensure(key, owner)->blocked = true;
	}

	[[nodiscard]] bool hasFiltered(DialogKey key, base::has_weak_ptr *owner) {
		const auto entry = find(key, owner);
		if (!entry) {
			return _dialogsSeen.object(key) != nullptr;
		} else if (entry->blocked) {
			return true;
		}
		for (const auto message : entry->messages.keys()) {
			if (*entry->messages.object(message)) {
				return true;
			}
		}
		return false;
	}

	void remove(DialogKey key, base::has_weak_ptr *owner, int64 message) {
		if (const auto entry = find(key, owner)) {
			entry->messages.remove(message);
		}
	}

	void clear() {
		_entries.clear();
		_dialogsSeen.clear();
	}

	[[nodiscard]] int size() const {
		return _entries.size();
	}

private:
	struct Entry {
		base::weak_ptr<base::has_weak_ptr> owner;
		QCache<int64, bool> messages{ kMaxMessages };
		// Set only by markBlocked, for messages removed because the account was
		// blocked, so the "show filtered messages" action stays available for
		// that dialog. Deliberately not derived from a filtered match: a
		// removable filter must stop offering the action once it is removed,
		// and a match is already covered by messages.
		bool blocked = false;
	};

	[[nodiscard]] Entry *find(DialogKey key, base::has_weak_ptr *owner) {
		const auto entry = _entries.object(key);
		if (entry && entry->owner.get() != owner) {
			entry->messages.clear();
			entry->blocked = false;
			entry->owner = owner;
		}
		return entry;
	}

	[[nodiscard]] Entry *ensure(DialogKey key, base::has_weak_ptr *owner) {
		if (const auto entry = find(key, owner)) {
			return entry;
		}
		if (!_dialogsSeen.object(key)) {
			_dialogsSeen.insert(key, new bool(true));
		}
		const auto entry = new Entry();
		entry->owner = owner;
		_entries.insert(key, entry);
		return entry;
	}

	// Match results can be evicted, and eviction must not remove the only way to
	// reveal hidden messages. Remember the dialogs that took part in this
	// generation so a miss for one of them is answered conservatively until a
	// filter rebuild clears the list. Scoping it to the dialogs that were
	// actually cached keeps the answer away from dialogs that never held a
	// result, and away from other accounts. Bounded and LRU like _entries: once
	// it overflows the oldest participants are forgotten, which costs the extra
	// action for a long-visited dialog but never adds one anywhere else.
	// This changes only menu availability, never whether a message is hidden.
	QCache<DialogKey, bool> _dialogsSeen{ kMaxSeenDialogs };
	QCache<DialogKey, Entry> _entries{ kMaxDialogs };
};

// Explicit user choices are not a cache: never evict them under memory pressure
// from browsing other dialogs. The account identity is part of every key.
class ShownDialogs final {
public:
	[[nodiscard]] bool contains(DialogKey key) const {
		return _values.contains(key);
	}

	void toggle(DialogKey key) {
		if (!_values.remove(key)) {
			_values.insert(key);
		}
	}

private:
	QSet<DialogKey> _values;

};

} // namespace FiltersCacheController
