// This is the source code of AyuGram for Desktop.
// Copyright @Radolyn, 2026
#pragma once

#include "base/weak_ptr.h"
#include "rpl/producer.h"

#include <QCache>
#include <QHash>
#include <QPair>
#include <QSet>
#include <QVector>

#include <iterator>
#include <list>
#include <memory>
#include <optional>
#include <unordered_set>
#include <utility>

namespace FiltersCacheController {

using DialogKey = QPair<uint64, uint64>;

class ItemRemovalSubscriptions final {
public:
	template <typename Session, typename Events, typename Callback>
	void track(
			Session *session,
			Events events,
			rpl::lifetime &lifetime,
			Callback callback) {
		const auto state = _state;
		const auto key = static_cast<const void*>(session);
		if (!state->sessions.emplace(key).second) {
			return;
		}
		lifetime.add([state, key] {
			state->sessions.erase(key);
		});
		std::move(events) | rpl::on_next(std::move(callback), lifetime);
	}

private:
	struct State {
		std::unordered_set<const void*> sessions;
	};

	const std::shared_ptr<State> _state = std::make_shared<State>();
};

// Callers serialize access. A live session guard prevents a reused account key
// from serving results produced before logout, even if addresses are reused.
class MessageCache final {
public:
	static constexpr auto kMaxDialogs = 128;
	static constexpr auto kMaxMessages = 512;
	static constexpr auto kMaxTrackedMatches = 512;
	static constexpr auto kMaxEvictedDialogs = 512;

	[[nodiscard]] std::optional<bool> lookup(
			DialogKey key,
			base::has_weak_ptr *owner,
			int64 message,
			QVector<int64> groupSignature = {}) {
		const auto entry = find(key, owner);
		const auto result = entry ? entry->messages.object(message) : nullptr;
		if (result && result->groupSignature == groupSignature) {
			return result->filtered;
		}
		if (entry) {
			invalidateStaleGroup(*entry, message, groupSignature);
		}
		return std::nullopt;
	}

	void put(
			DialogKey key,
			base::has_weak_ptr *owner,
			int64 message,
			bool value,
			QVector<int64> groupSignature = {}) {
		const auto entry = ensure(key, owner);
		if (value) {
			rememberFiltered(
				entry->filteredMessages,
				entry->filteredResultsMayBeEvicted,
				message,
				groupSignature);
		} else {
			entry->filteredMessages.remove(message);
		}
		if (const auto existing = entry->messages.object(message)) {
			existing->filtered = value;
			existing->groupSignature = std::move(groupSignature);
		} else {
			entry->messages.insert(message, new MessageResult{
				.filtered = value,
				.groupSignature = std::move(groupSignature),
			});
		}
	}

	void markBlocked(
			DialogKey key,
			base::has_weak_ptr *owner,
			int64 message) {
		const auto entry = ensure(key, owner);
		remember(
			entry->blockedMessages,
			entry->blockedResultsMayBeEvicted,
			message);
	}

	void unmarkBlocked(
			DialogKey key,
			base::has_weak_ptr *owner,
			int64 message) {
		if (const auto entry = find(key, owner);
			entry && !entry->blockedResultsMayBeEvicted) {
			entry->blockedMessages.remove(message);
		}
	}

	[[nodiscard]] bool hasFiltered(DialogKey key, base::has_weak_ptr *owner) {
		const auto entry = find(key, owner);
		const auto evictedForDialog = hasEvictedForDialog(key, owner);
		if (!entry) {
			return evictedForDialog;
		}
		return evictedForDialog
			|| entry->filteredResultsMayBeEvicted
			|| entry->blockedResultsMayBeEvicted
			|| !entry->filteredMessages.isEmpty()
			|| !entry->blockedMessages.isEmpty();
	}

	void remove(DialogKey key, base::has_weak_ptr *owner, int64 message) {
		if (const auto entry = find(key, owner)) {
			entry->messages.remove(message);
			entry->filteredMessages.remove(message);
			entry->blockedMessages.remove(message);
		}
	}

	void removeGroup(
			DialogKey key,
			base::has_weak_ptr *owner,
			const QVector<int64> &messages) {
		if (const auto entry = find(key, owner)) {
			for (const auto message : messages) {
				entry->messages.remove(message);
				entry->filteredMessages.remove(message);
				entry->blockedMessages.remove(message);
			}
		}
	}

	void removeForItem(
			DialogKey key,
			base::has_weak_ptr *owner,
			int64 message,
			const QVector<int64> &groupSignature) {
		if (groupSignature.isEmpty()) {
			remove(key, owner, message);
		} else {
			removeGroup(key, owner, groupSignature);
		}
	}

	void clear() {
		_entries.clear();
		_lru.clear();
		_evictedSessions.clear();
		_trackedEvictedDialogs = 0;
	}

	[[nodiscard]] int size() const {
		return _entries.size();
	}

private:
	struct MessageResult {
		bool filtered = false;
		QVector<int64> groupSignature;
	};
	struct Entry {
		DialogKey key;
		base::weak_ptr<base::has_weak_ptr> owner;
		QCache<int64, MessageResult> messages{ kMaxMessages };
		QHash<int64, QVector<int64>> filteredMessages;
		QSet<int64> blockedMessages;
		bool filteredResultsMayBeEvicted = false;
		bool blockedResultsMayBeEvicted = false;
	};
	struct SessionFallback {
		base::weak_ptr<base::has_weak_ptr> owner;
		QSet<uint64> evictedDialogs;
		bool allDialogsMayBeFiltered = false;
	};

	static void invalidateStaleGroup(
			Entry &entry,
			int64 message,
			const QVector<int64> &groupSignature) {
		auto staleSignatures = QVector<QVector<int64>>();
		const auto collectStale = [&](int64 member) {
			if (const auto result = entry.messages.object(member);
				result
				&& result->groupSignature != groupSignature
				&& !result->groupSignature.isEmpty()) {
				staleSignatures.push_back(result->groupSignature);
			}
			if (const auto ledger = entry.filteredMessages.constFind(member);
				ledger != entry.filteredMessages.cend()
				&& ledger.value() != groupSignature
				&& !ledger.value().isEmpty()) {
				staleSignatures.push_back(ledger.value());
			}
		};
		collectStale(message);
		for (const auto member : groupSignature) {
			collectStale(member);
		}

		const auto removeIfStale = [&](int64 member) {
			if (const auto result = entry.messages.object(member);
				result && result->groupSignature != groupSignature) {
				entry.messages.remove(member);
			}
			if (const auto ledger = entry.filteredMessages.find(member);
				ledger != entry.filteredMessages.end()
				&& ledger.value() != groupSignature) {
				entry.filteredMessages.erase(ledger);
			}
		};
		for (const auto &signature : staleSignatures) {
			for (const auto member : signature) {
				removeIfStale(member);
			}
		}
		for (const auto member : groupSignature) {
			removeIfStale(member);
		}
		removeIfStale(message);
	}

	static void remember(
			QSet<int64> &messages,
			bool &resultsMayBeEvicted,
			int64 message) {
		if (resultsMayBeEvicted || messages.contains(message)) {
			return;
		}
		if (messages.size() >= kMaxTrackedMatches) {
			messages.clear();
			resultsMayBeEvicted = true;
			return;
		}
		messages.insert(message);
	}

	static void rememberFiltered(
			QHash<int64, QVector<int64>> &messages,
			bool &resultsMayBeEvicted,
			int64 message,
			const QVector<int64> &groupSignature) {
		if (resultsMayBeEvicted) {
			return;
		}
		if (messages.contains(message)) {
			messages[message] = groupSignature;
			return;
		}
		if (messages.size() >= kMaxTrackedMatches) {
			messages.clear();
			resultsMayBeEvicted = true;
			return;
		}
		messages.insert(message, groupSignature);
	}

	[[nodiscard]] Entry *find(DialogKey key, base::has_weak_ptr *owner) {
		const auto i = _entries.find(key);
		if (i == _entries.end()) {
			return nullptr;
		}
		_lru.splice(_lru.end(), _lru, i.value());
		const auto entry = &*i.value();
		if (entry->owner.get() != owner) {
			entry->messages.clear();
			entry->filteredMessages.clear();
			entry->blockedMessages.clear();
			entry->filteredResultsMayBeEvicted = false;
			entry->blockedResultsMayBeEvicted = false;
			entry->owner = owner;
		}
		return entry;
	}

	[[nodiscard]] bool hasEvictedForDialog(
			DialogKey key,
			base::has_weak_ptr *owner) {
		const auto i = _evictedSessions.find(key.first);
		if (i == _evictedSessions.end()) {
			return false;
		}
		if (i->owner.get() != owner) {
			_trackedEvictedDialogs -= i->evictedDialogs.size();
			_evictedSessions.erase(i);
			return false;
		}
		return i->allDialogsMayBeFiltered
			|| i->evictedDialogs.contains(key.second);
	}

	void pruneExpiredSessions() {
		for (auto i = _evictedSessions.begin(); i != _evictedSessions.end();) {
			if (!i->owner.get()) {
				_trackedEvictedDialogs -= i->evictedDialogs.size();
				i = _evictedSessions.erase(i);
			} else {
				++i;
			}
		}
	}

	static bool mayContainFiltered(const Entry &entry) {
		return entry.filteredResultsMayBeEvicted
			|| entry.blockedResultsMayBeEvicted
			|| !entry.filteredMessages.isEmpty()
			|| !entry.blockedMessages.isEmpty();
	}

	void rememberEvicted(DialogKey key, const Entry &entry) {
		const auto owner = entry.owner.get();
		if (!owner || !mayContainFiltered(entry)) {
			return;
		}
		const auto i = _evictedSessions.find(key.first);
		if (i == _evictedSessions.end() || i->owner.get() != owner) {
			if (i != _evictedSessions.end()) {
				_trackedEvictedDialogs -= i->evictedDialogs.size();
				_evictedSessions.erase(i);
			}
			_evictedSessions.insert(key.first, SessionFallback{
				.owner = entry.owner,
			});
		}
		auto session = _evictedSessions.find(key.first);
		if (session->allDialogsMayBeFiltered
			|| session->evictedDialogs.contains(key.second)) {
			return;
		}
		if (_trackedEvictedDialogs >= kMaxEvictedDialogs) {
			_trackedEvictedDialogs -= session->evictedDialogs.size();
			session->evictedDialogs.clear();
			session->allDialogsMayBeFiltered = true;
		} else {
			session->evictedDialogs.insert(key.second);
			++_trackedEvictedDialogs;
		}
	}

	[[nodiscard]] Entry *ensure(DialogKey key, base::has_weak_ptr *owner) {
		if (const auto entry = find(key, owner)) {
			return entry;
		}
		pruneExpiredSessions();
		if (_entries.size() >= kMaxDialogs) {
			const auto &evicted = _lru.front();
			rememberEvicted(evicted.key, evicted);
			_entries.remove(evicted.key);
			_lru.pop_front();
		}
		_lru.emplace_back();
		const auto entry = std::prev(_lru.end());
		entry->key = key;
		entry->owner = owner;
		_entries.insert(key, entry);
		return &*entry;
	}

	// Exact matching IDs survive the result cache's LRU eviction and can be
	// removed when a message changes. If their bounded ledgers overflow, keep
	// the menu action visible until rebuild because older matches are unknown.
	// Evicted peers are tracked exactly up to one global bound. If that ledger
	// overflows, widen only the affected session until rebuild so cache churn
	// cannot grow memory with the number of historical dialogs or accounts.
	QHash<DialogKey, std::list<Entry>::iterator> _entries;
	std::list<Entry> _lru;
	QHash<uint64, SessionFallback> _evictedSessions;
	qsizetype _trackedEvictedDialogs = 0;

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
