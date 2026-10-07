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
	static constexpr auto kMaxTrackedFilteredMessages = kMaxMessages;
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
		updateFilteredMessage(key, owner, message, value);
		if (const auto existing = entry->messages.object(message)) {
			*existing = value;
		} else {
			entry->messages.insert(message, new bool(value));
		}
	}

	void markBlocked(DialogKey key, base::has_weak_ptr *owner) {
		const auto entry = ensure(key, owner);
		entry->blocked = true;
		if (const auto seen = findSeen(key, owner)) {
			seen->blocked = true;
		}
	}

	[[nodiscard]] bool hasFiltered(DialogKey key, base::has_weak_ptr *owner) {
		const auto entry = find(key, owner);
		const auto seen = findSeen(key, owner);
		return (entry && entry->blocked)
			|| (seen && (seen->blocked
				|| seen->hasUntrackedFilteredMessages
				|| !seen->filteredMessages.isEmpty()));
	}

	void remove(DialogKey key, base::has_weak_ptr *owner, int64 message) {
		if (const auto entry = find(key, owner)) {
			entry->messages.remove(message);
		}
		if (const auto seen = findSeen(key, owner)) {
			seen->filteredMessages.remove(message);
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
	struct SeenDialog {
		base::weak_ptr<base::has_weak_ptr> owner;
		QSet<int64> filteredMessages;
		bool hasUntrackedFilteredMessages = false;
		bool blocked = false;
	};

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
			_dialogsSeen.remove(key);
		}
		return entry;
	}

	[[nodiscard]] Entry *ensure(DialogKey key, base::has_weak_ptr *owner) {
		const auto existing = find(key, owner);
		remember(key, owner);
		if (existing) {
			return existing;
		}
		const auto entry = new Entry();
		entry->owner = owner;
		_entries.insert(key, entry);
		return entry;
	}

	[[nodiscard]] SeenDialog *findSeen(
			DialogKey key,
			base::has_weak_ptr *owner) {
		const auto seen = _dialogsSeen.object(key);
		if (!seen) {
			return nullptr;
		}
		if (seen->owner.get() != owner) {
			_dialogsSeen.remove(key);
			return nullptr;
		}
		return seen;
	}

	void remember(DialogKey key, base::has_weak_ptr *owner) {
		if (findSeen(key, owner)) {
			return;
		}
		const auto seen = new SeenDialog();
		seen->owner = owner;
		_dialogsSeen.insert(key, seen);
	}

	void updateFilteredMessage(
			DialogKey key,
			base::has_weak_ptr *owner,
			int64 message,
			bool value) {
		const auto seen = findSeen(key, owner);
		if (!seen) {
			return;
		}
		if (!value) {
			seen->filteredMessages.remove(message);
			return;
		}
		if (seen->filteredMessages.contains(message)) {
			return;
		}
		if (seen->filteredMessages.size() < kMaxTrackedFilteredMessages) {
			seen->filteredMessages.insert(message);
		} else {
			seen->hasUntrackedFilteredMessages = true;
		}
	}

	// Positive message ids outlive the result LRU so edits can retract a stale
	// menu action. Both caches are bounded; overflow keeps the action available
	// conservatively until the next filter rebuild.
	QCache<DialogKey, SeenDialog> _dialogsSeen{ kMaxSeenDialogs };
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
