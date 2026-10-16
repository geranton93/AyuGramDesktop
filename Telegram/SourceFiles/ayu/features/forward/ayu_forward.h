// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "api/api_common.h"
#include "history/history.h"
#include "main/main_session.h"
#include "base/weak_ptr.h"

#include <atomic>
#include <mutex>
#include <optional>

namespace Api {
enum class ForwardMessagesResult;
} // namespace Api

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
	struct Progress {
		int currentChunk = 0;
		int totalMessages = 0;
		int sentMessages = 0;
	};

	explicit ForwardState(
		int totalChunks,
		const base::weak_ptr<Main::Session> &session)
	: totalChunks(totalChunks)
	, owner(session) {
	}

	ForwardState(const ForwardState &other)
	: totalChunks(other.totalChunks)
	, owner(other.owner)
	, state(other.state.load())
	, stopRequested(other.stopRequested.load())
	, _progress(other.progress()) {
	}

	void updateBottomBar(const PeerId *peer, const State &st);
	[[nodiscard]] Progress progress() const {
		const auto lock = std::lock_guard(_progressMutex);
		return _progress;
	}
	void setProgress(Progress progress) {
		const auto lock = std::lock_guard(_progressMutex);
		_progress = progress;
	}
	void setMessageProgress(int totalMessages, int sentMessages) {
		const auto lock = std::lock_guard(_progressMutex);
		_progress.totalMessages = totalMessages;
		_progress.sentMessages = sentMessages;
	}
	void setSentMessages(int sentMessages) {
		const auto lock = std::lock_guard(_progressMutex);
		_progress.sentMessages = sentMessages;
	}
	void addSentMessages(int sentMessages) {
		const auto lock = std::lock_guard(_progressMutex);
		_progress.sentMessages += sentMessages;
	}

	// Progress is read and written under one lock so the UI never combines
	// counters from different chunks. State and cancellation remain atomic
	// because they are queried independently across the main and worker threads.
	const int totalChunks = 0;
	base::weak_ptr<Main::Session> owner;
	std::atomic<State> state = State::Preparing;
	std::atomic<bool> stopRequested = false;

private:
	mutable std::mutex _progressMutex;
	Progress _progress;

};

struct ForwardTargetSnapshot {
	uint64 sessionUniqueId = 0;
	PeerId peerId;
	bool slowmodeApplied = false;
};

[[nodiscard]] std::optional<ForwardTargetSnapshot> SnapshotForwardTarget(
	const base::weak_ptr<Main::Session> &session,
	const base::weak_ptr<History> &targetHistory);

bool isAyuForwardNeeded(const std::vector<not_null<HistoryItem*>> &items);
bool isAyuForwardNeeded(not_null<HistoryItem*> item);
bool isFullAyuForwardNeeded(const std::vector<not_null<HistoryItem*>> &items);
bool isFullAyuForwardNeeded(not_null<HistoryItem*> item);
void intelligentForward(
	const base::weak_ptr<Main::Session> &session,
	const Api::SendAction &action,
	const MessageIdsList &itemIds,
	Data::ForwardOptions options,
	base::weak_ptr<History> targetHistory,
	Fn<void(Api::ForwardMessagesResult)> done = nullptr,
	Fn<bool()> cancelled = nullptr);
Api::ForwardMessagesResult forwardMessages(
	const base::weak_ptr<Main::Session> &session,
	const Api::SendAction &action,
	bool forwardState,
	const MessageIdsList &itemIds,
	Data::ForwardOptions options,
	base::weak_ptr<History> targetHistory,
	bool trackProgress = true,
	Fn<bool()> cancelled = nullptr);

}
