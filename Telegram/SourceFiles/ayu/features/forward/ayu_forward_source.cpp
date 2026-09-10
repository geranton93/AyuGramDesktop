// This is the source code of AyuGram for Desktop.

#include "ayu/features/forward/ayu_forward_source.h"

#include "apiwrap.h"
#include "base/debug_log.h"
#include "base/timer.h"
#include "base/weak_ptr.h"
#include "data/data_channel.h"
#include "data/data_peer.h"
#include "data/data_session.h"
#include "history/history_item.h"
#include "main/main_session.h"

#include <chrono>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <unordered_map>
#include <unordered_set>
#include <utility>

namespace AyuForward {

[[nodiscard]] bool isSourceForwardRestricted(not_null<HistoryItem*> item) {
	const auto from = item->from();
	return item->isAyuNoForwards()
		|| item->history()->peer->isAyuNoForwards()
		|| (from && from->isAyuNoForwards());
}

namespace {

constexpr auto kMaxConcurrentForwardSourceResolutions = std::size_t(4);
constexpr auto kMaxForwardSourceResolutionTime = std::chrono::minutes(2);
constexpr auto kMaxSingleForwardSourceResolutionTime
	= std::chrono::seconds(15);

struct ForwardSource {
	not_null<ChannelData*> peer;
	MsgId id = 0;
};

struct ForwardSourceCacheKey {
	uint64 sessionUniqueId = 0;
	FullMsgId fullId;

	friend inline auto operator<=>(
		ForwardSourceCacheKey,
		ForwardSourceCacheKey) = default;
};

struct ForwardSourceCacheKeyHash {
	size_t operator()(const ForwardSourceCacheKey &value) const noexcept {
		const auto first = std::hash<uint64>()(value.sessionUniqueId);
		const auto second = std::hash<FullMsgId>()(value.fullId);
		return first ^ (second + 0x9e3779b97f4a7c15ULL
			+ (first << 6)
			+ (first >> 2));
	}
};

class UnavailableForwardSources final {
public:
	static constexpr auto kMaxEntries = std::size_t(1024);

	[[nodiscard]] bool contains(const ForwardSourceCacheKey &key) const {
		const auto lock = std::lock_guard(_mutex);
		return _values.contains(key);
	}

	void erase(const ForwardSourceCacheKey &key) {
		const auto lock = std::lock_guard(_mutex);
		_values.erase(key);
	}

	void insert(const ForwardSourceCacheKey &key) {
		const auto lock = std::lock_guard(_mutex);
		if (_values.contains(key)) {
			return;
		}
		if (_values.size() >= kMaxEntries) {
			_values.erase(_values.begin());
		}
		_values.emplace(key);
	}

private:
	mutable std::mutex _mutex;
	std::unordered_set<ForwardSourceCacheKey, ForwardSourceCacheKeyHash> _values;
};

UnavailableForwardSources unavailableForwardSources;

[[nodiscard]] bool canUseForwardSource(not_null<ChannelData*> channel) {
	return channel->isPublic() || channel->amIn();
}

[[nodiscard]] ForwardSourceCacheKey forwardSourceCacheKey(
		not_null<Main::Session*> session,
		const ForwardSource &source) {
	return ForwardSourceCacheKey{
		.sessionUniqueId = session->uniqueId(),
		.fullId = FullMsgId(source.peer->id, source.id),
	};
}

void clearUnavailableForwardSource(
		not_null<Main::Session*> session,
		const ForwardSource &source) {
	unavailableForwardSources.erase(forwardSourceCacheKey(session, source));
}

void markUnavailableForwardSource(
		not_null<Main::Session*> session,
		const ForwardSource &source) {
	unavailableForwardSources.insert(forwardSourceCacheKey(session, source));
}

[[nodiscard]] std::optional<ForwardSource> forwardSourceData(
		not_null<HistoryItem*> item) {
	if (item->isDeleted()
		|| item->unsupportedTTL()
		|| (item->media() && item->media()->ttlSeconds())
		|| !isSourceForwardRestricted(item)) {
		return std::nullopt;
	}
	const auto originalSender = item->originalSender();
	const auto originalId = item->originalId();
	const auto originalChannel = originalSender
		? originalSender->asChannel()
		: nullptr;
	if (!originalChannel || !IsServerMsgId(originalId)
		|| !canUseForwardSource(originalChannel)
		|| originalSender->isAyuNoForwards()
		|| !originalSender->allowsForwarding()) {
		return std::nullopt;
	}
	const auto source = ForwardSource{
		.peer = originalChannel,
		.id = originalId,
	};
	const auto session = &item->history()->session();
	if (unavailableForwardSources.contains(
		forwardSourceCacheKey(session, source))) {
		return std::nullopt;
	}
	return source;
}

struct ForwardSourceLookup {
	PeerId sourcePeerId;
	MsgId sourceId = 0;
	MTPInputPeer historyInput;
	MsgId historyItemId = 0;
};

class ForwardSourceResolver final
	: public std::enable_shared_from_this<ForwardSourceResolver> {
public:
	using Callback = std::function<void(MessageIdsList)>;

	static void Start(
			not_null<Main::Session*> session,
			MessageIdsList itemIds,
			Callback callback) {
		const auto weakSession = base::make_weak(session);
		std::shared_ptr<Callback> callbackHolder;
		try {
			callbackHolder = std::make_shared<Callback>(std::move(callback));
		} catch (...) {
			try {
				callback({});
			} catch (...) {
				LOG(("AyuForward: source resolution callback failed"));
			}
			return;
		}
		const auto fail = [callbackHolder] {
			if (!callbackHolder || !*callbackHolder) {
				return;
			}
			auto callback = std::move(*callbackHolder);
			try {
				callback({});
			} catch (...) {
				LOG(("AyuForward: source resolution callback failed"));
			}
		};
		try {
			crl::on_main([
					weakSession,
					itemIds = std::move(itemIds),
					callbackHolder]() mutable {
				const auto current = weakSession.get();
				if (!current) {
					return;
				}
				std::shared_ptr<ForwardSourceResolver> resolver;
			try {
				resolver = std::make_shared<ForwardSourceResolver>(
					weakSession,
					std::move(itemIds),
					std::move(*callbackHolder));
				const auto self = resolver;
				resolver->_timeout.setCallback([self] {
					self->finish({});
				});
				resolver->_timeout.callOnce(
					static_cast<crl::time>(
						std::chrono::duration_cast<std::chrono::milliseconds>(
							kMaxForwardSourceResolutionTime).count()));
				resolver->pump();
			} catch (...) {
				if (resolver) {
					resolver->finish({});
				} else {
					auto callback = std::move(*callbackHolder);
					if (!callback) {
						return;
					}
					try {
						callback({});
					} catch (...) {
						LOG(("AyuForward: source resolution callback failed"));
					}
				}
			}
			});
		} catch (...) {
			fail();
		}
	}

public:
	ForwardSourceResolver(
			base::weak_ptr<Main::Session> session,
			MessageIdsList itemIds,
			Callback callback)
	: _session(std::move(session))
	, _itemIds(std::move(itemIds))
	, _resolvedIds(_itemIds.size())
	, _callback(std::move(callback)) {
	}

	~ForwardSourceResolver() = default;

private:

	void pump() {
		if (_finished) {
			return;
		}
		try {
			if (!_session
				|| std::chrono::steady_clock::now()
					>= _deadline) {
				finish({});
				return;
			}
			const auto maxItemsPerPump = std::size_t(64);
			auto processed = std::size_t(0);
			while (_nextIndex < _itemIds.size()
				&& _inFlight < kMaxConcurrentForwardSourceResolutions
				&& processed++ < maxItemsPerPump) {
				const auto index = _nextIndex++;
				if (!startItem(index)) {
					finish({});
					return;
				}
			}
			if (_nextIndex < _itemIds.size()
				&& _inFlight < kMaxConcurrentForwardSourceResolutions) {
				schedulePump();
			} else if (_nextIndex == _itemIds.size() && !_inFlight) {
				finish(std::move(_resolvedIds));
			}
		} catch (...) {
			finish({});
		}
	}

	[[nodiscard]] bool startItem(const std::size_t index) {
		const auto current = _session.get();
		if (!current) {
			return false;
		}
		const auto item = current->data().message(_itemIds[index]);
		if (!item) {
			return false;
		}
		const auto source = forwardSourceData(item);
		if (!source) {
			_resolvedIds[index] = item->fullId();
			return true;
		}
		if (const auto existing = current->data().message(
				source->peer,
				source->id)) {
			clearUnavailableForwardSource(current, *source);
			_resolvedIds[index] = existing->fullId();
			return true;
		}

		const auto lookup = ForwardSourceLookup{
			.sourcePeerId = source->peer->id,
			.sourceId = source->id,
			.historyInput = item->history()->peer->input(),
			.historyItemId = item->id,
		};
		if (!current->data().peer(lookup.sourcePeerId)->asChannel()) {
			return false;
		}

		const auto self = shared_from_this();
		++_inFlight;
		auto requestId = mtpRequestId(0);
		try {
			requestId = current->api().request(MTPchannels_GetMessages(
				MTP_inputChannelFromMessage(
					lookup.historyInput,
					MTP_int(lookup.historyItemId),
					MTP_long(peerToChannel(lookup.sourcePeerId).bare)),
				MTP_vector<MTPInputMessage>(
					1,
					MTP_inputMessageID(MTP_int(lookup.sourceId)))
			)).done([
					self,
					index,
					sourcePeerId = lookup.sourcePeerId,
					sourceId = lookup.sourceId](
						const MTPmessages_Messages &result,
						mtpRequestId requestId) {
				self->handleSuccess(
					index,
					sourcePeerId,
					sourceId,
					requestId,
					result);
			}).fail([
					self](const MTP::Error &, mtpRequestId requestId) {
				self->handleFailure(requestId);
			}).send();
			const auto timeoutId = _requestTimeouts.call(
				static_cast<crl::time>(
					std::chrono::duration_cast<std::chrono::milliseconds>(
						kMaxSingleForwardSourceResolutionTime).count()),
				[self, requestId] {
					self->handleTimeout(requestId);
				});
			_requests.emplace(requestId, timeoutId);
		} catch (...) {
			if (requestId) {
				current->api().request(requestId).cancel();
			}
			--_inFlight;
			return false;
		}
		return true;
	}

	void schedulePump() {
		try {
			const auto self = shared_from_this();
			crl::on_main([self] {
				self->pump();
			});
		} catch (...) {
			finish({});
		}
	}

	[[nodiscard]] bool takeRequest(mtpRequestId requestId) {
		const auto i = _requests.find(requestId);
		if (i == _requests.end()) {
			return false;
		}
		_requestTimeouts.cancel(i->second);
		_requests.erase(i);
		return true;
	}

	void handleSuccess(
			std::size_t index,
			PeerId sourcePeerId,
			MsgId sourceId,
			mtpRequestId requestId,
			const MTPmessages_Messages &result) {
		if (!takeRequest(requestId)) {
			return;
		}
		if (_inFlight) {
			--_inFlight;
		}
		if (_finished) {
			return;
		}
		try {
			const auto current = _session.get();
			if (!current) {
				finish({});
				return;
			}
			const auto sourcePeer = current->data().peer(
				sourcePeerId)->asChannel();
			if (!sourcePeer) {
				finish({});
				return;
			}
			current->data().processExistingMessages(sourcePeer, result);
			if (const auto resolved = current->data().message(
					sourcePeerId,
					sourceId)) {
				clearUnavailableForwardSource(
					current,
					ForwardSource{ sourcePeer, sourceId });
				_resolvedIds[index] = resolved->fullId();
				pump();
			} else {
				markUnavailableForwardSource(
					current,
					ForwardSource{ sourcePeer, sourceId });
				finish({});
			}
		} catch (...) {
			finish({});
		}
	}

	void handleFailure(mtpRequestId requestId) {
		if (!takeRequest(requestId)) {
			return;
		}
		if (_inFlight) {
			--_inFlight;
		}
		finish({});
	}

	void handleTimeout(mtpRequestId requestId) {
		if (!takeRequest(requestId)) {
			return;
		}
		if (const auto current = _session.get()) {
			current->api().request(requestId).cancel();
		}
		if (_inFlight) {
			--_inFlight;
		}
		finish({});
	}

	void finish(MessageIdsList result) {
		if (_finished) {
			return;
		}
		_finished = true;
		_timeout.cancel();
		_timeout.setCallback(nullptr);
		const auto requests = std::move(_requests);
		_requests.clear();
		for (const auto &[requestId, timeoutId] : requests) {
			_requestTimeouts.cancel(timeoutId);
			if (const auto current = _session.get()) {
				current->api().request(requestId).cancel();
			}
		}
		auto callback = std::move(_callback);
		if (callback) {
			try {
				callback(std::move(result));
			} catch (...) {
				LOG(("AyuForward: source resolution callback failed"));
			}
		}
	}

	base::weak_ptr<Main::Session> _session;
	MessageIdsList _itemIds;
	MessageIdsList _resolvedIds;
	Callback _callback;
	base::Timer _timeout;
	base::DelayedCallTimer _requestTimeouts;
	std::unordered_map<mtpRequestId, int> _requests;
	const std::chrono::steady_clock::time_point _deadline
		= std::chrono::steady_clock::now()
		+ kMaxForwardSourceResolutionTime;
	std::size_t _nextIndex = 0;
	std::size_t _inFlight = 0;
	bool _finished = false;
};


} // namespace

bool isFullAyuForwardNeeded(not_null<HistoryItem*> item) {
	return isSourceForwardRestricted(item) && !forwardSourceData(item);
}

void resolveForwardSources(
	not_null<Main::Session*> session,
	MessageIdsList itemIds,
	std::function<void(MessageIdsList)> callback) {
	ForwardSourceResolver::Start(
		session,
		std::move(itemIds),
		std::move(callback));
}

} // namespace AyuForward
