// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "history/history.h"
#include "main/main_session.h"
#include "base/weak_ptr.h"

#include <atomic>
#include <optional>

namespace AyuForward {
bool isForwarding(const Main::Session &session, const PeerId &id);
void cancelForward(const PeerId &id, const Main::Session &session);
std::pair<QString, QString> stateName(
	const Main::Session &session,
	const PeerId &id);

class ForwardState
{
public:
	enum class State
	{
		Preparing,
		Downloading,
		Sending,
		Finished
	};

	explicit ForwardState(
		int totalChunks,
		not_null<Main::Session*> session)
	: totalChunks(totalChunks)
	, owner(base::make_weak(session)) {
	}

	ForwardState(const ForwardState &other)
	: totalChunks(other.totalChunks)
	, owner(other.owner)
	, currentChunk(other.currentChunk.load())
	, totalMessages(other.totalMessages.load())
	, sentMessages(other.sentMessages.load())
	, state(other.state.load())
	, stopRequested(other.stopRequested.load()) {
	}

	void updateBottomBar(const Main::Session &session, const PeerId *peer, const State &st);

	// Written from a crl::async background thread (forwardMessages /
	// intelligentForward) and read from the main thread (isForwarding,
	// stateName, cancelForward) concurrently, so every field that crosses
	// threads must be atomic. totalChunks is set once in the constructor
	// and never mutated afterwards, so it doesn't need to be.
	const int totalChunks = 0;
	base::weak_ptr<Main::Session> owner;
	std::atomic<int> currentChunk = 0;
	std::atomic<int> totalMessages = 0;
	std::atomic<int> sentMessages = 0;

	std::atomic<State> state = State::Preparing;
	std::atomic<bool> stopRequested = false;

};

struct ForwardTargetSnapshot {
	uint64 sessionUniqueId = 0;
	PeerId peerId;
	bool slowmodeApplied = false;
};

[[nodiscard]] std::optional<ForwardTargetSnapshot> SnapshotForwardTarget(
	not_null<Main::Session*> session,
	const base::weak_ptr<History> &targetHistory);

bool isAyuForwardNeeded(const std::vector<not_null<HistoryItem*>> &items);
bool isAyuForwardNeeded(not_null<HistoryItem*> item);
bool isFullAyuForwardNeeded(const std::vector<not_null<HistoryItem*>> &items);
bool isFullAyuForwardNeeded(not_null<HistoryItem*> item);
void intelligentForward(
	not_null<Main::Session*> session,
	const Api::SendAction &action,
	const MessageIdsList &itemIds,
	Data::ForwardOptions options,
	base::weak_ptr<History> targetHistory);
void forwardMessages(
	not_null<Main::Session*> session,
	const Api::SendAction &action,
	bool forwardState,
	const MessageIdsList &itemIds,
	Data::ForwardOptions options,
	base::weak_ptr<History> targetHistory);

}
