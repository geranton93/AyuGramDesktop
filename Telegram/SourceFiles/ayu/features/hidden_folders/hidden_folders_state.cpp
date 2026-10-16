// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#include "ayu/features/hidden_folders/hidden_folders_state.h"

#include <algorithm>
#include <iterator>
#include <utility>

namespace AyuFeatures {
namespace {

constexpr auto kMaxInspectedFolderIds
	= HiddenFoldersState::kMaxFoldersPerSession * 4;

[[nodiscard]] std::vector<HiddenFoldersState::FolderId> Normalize(
		const std::vector<HiddenFoldersState::FolderId> &ids) {
	auto result = std::vector<HiddenFoldersState::FolderId>();
	result.reserve(std::min(
		ids.size(),
		HiddenFoldersState::kMaxFoldersPerSession));
	const auto inspected = std::min(ids.size(), kMaxInspectedFolderIds);
	for (auto i = std::size_t(0); i != inspected; ++i) {
		if (ids[i] > 0) {
			result.push_back(ids[i]);
		}
	}
	std::sort(result.begin(), result.end());
	result.erase(std::unique(result.begin(), result.end()), result.end());
	if (result.size() > HiddenFoldersState::kMaxFoldersPerSession) {
		result.resize(HiddenFoldersState::kMaxFoldersPerSession);
	}
	return result;
}

[[nodiscard]] bool Contains(
		std::span<const HiddenFoldersState::FolderId> ids,
		HiddenFoldersState::FolderId id) {
	return id > 0 && std::find(ids.begin(), ids.end(), id) != ids.end();
}

} // namespace

bool HiddenFoldersState::isHidden(SessionId sessionId, FolderId id) const {
	return Contains(hidden(sessionId), id);
}

bool HiddenFoldersState::setHidden(
		SessionId sessionId,
		FolderId id,
		bool hiddenValue) {
	if (!sessionId || id <= 0) {
		return false;
	}
	const auto existing = _sessions.find(sessionId);
	if (existing == _sessions.end()) {
		if (!hiddenValue || _sessions.size() >= kMaxSessions) {
			return false;
		}
		_sessions.emplace(sessionId, std::vector<FolderId>{ id });
		return true;
	}

	auto &ids = existing->second;
	const auto position = std::lower_bound(ids.begin(), ids.end(), id);
	if (hiddenValue) {
		if ((position != ids.end() && *position == id)
			|| ids.size() >= kMaxFoldersPerSession) {
			return false;
		}
		ids.insert(position, id);
		return true;
	}
	if (position == ids.end() || *position != id) {
		return false;
	}
	ids.erase(position);
	if (ids.empty()) {
		_sessions.erase(existing);
	}
	return true;
}

bool HiddenFoldersState::replace(
		SessionId sessionId,
		const std::vector<FolderId> &ids) {
	if (!sessionId) {
		return false;
	}
	auto normalized = Normalize(ids);
	const auto existing = _sessions.find(sessionId);
	if (normalized.empty()) {
		if (existing == _sessions.end()) {
			return false;
		}
		_sessions.erase(existing);
		return true;
	}
	if (existing == _sessions.end()) {
		if (_sessions.size() >= kMaxSessions) {
			return false;
		}
		_sessions.emplace(sessionId, std::move(normalized));
		return true;
	}
	if (existing->second == normalized) {
		return false;
	}
	existing->second = std::move(normalized);
	return true;
}

bool HiddenFoldersState::reconcile(
		SessionId sessionId,
		const std::vector<FolderId> &validIds) {
	const auto existing = _sessions.find(sessionId);
	if (existing == _sessions.end()) {
		return false;
	}
	const auto valid = Normalize(validIds);
	auto kept = std::vector<FolderId>();
	kept.reserve(existing->second.size());
	std::set_intersection(
		existing->second.begin(),
		existing->second.end(),
		valid.begin(),
		valid.end(),
		std::back_inserter(kept));
	return replace(sessionId, kept);
}

const std::vector<HiddenFoldersState::FolderId> &HiddenFoldersState::hidden(
		SessionId sessionId) const {
	static const auto empty = std::vector<FolderId>();
	const auto i = _sessions.find(sessionId);
	return (i != _sessions.end()) ? i->second : empty;
}

const HiddenFoldersState::Sessions &HiddenFoldersState::sessions() const {
	return _sessions;
}

namespace HiddenFoldersPolicy {

std::vector<HiddenFoldersState::FolderId> VisibleIds(
		const std::vector<HiddenFoldersState::FolderId> &fullOrder,
		std::span<const HiddenFoldersState::FolderId> hidden) {
	auto result = std::vector<HiddenFoldersState::FolderId>();
	result.reserve(fullOrder.size());
	for (const auto id : fullOrder) {
		if (!Contains(hidden, id)) {
			result.push_back(id);
		}
	}
	return result;
}

std::vector<HiddenFoldersState::FolderId> ReorderVisible(
		std::vector<HiddenFoldersState::FolderId> fullOrder,
		std::span<const HiddenFoldersState::FolderId> hidden,
		int oldVisiblePosition,
		int newVisiblePosition) {
	if (oldVisiblePosition == newVisiblePosition) {
		return fullOrder;
	}
	auto visiblePositions = std::vector<int>();
	visiblePositions.reserve(fullOrder.size());
	for (auto i = 0; i != int(fullOrder.size()); ++i) {
		if (!Contains(hidden, fullOrder[i])) {
			visiblePositions.push_back(i);
		}
	}
	if (oldVisiblePosition < 0
		|| oldVisiblePosition >= int(visiblePositions.size())
		|| newVisiblePosition < 0
		|| newVisiblePosition >= int(visiblePositions.size())) {
		return fullOrder;
	}

	auto visible = std::vector<HiddenFoldersState::FolderId>();
	visible.reserve(visiblePositions.size());
	for (const auto position : visiblePositions) {
		visible.push_back(fullOrder[position]);
	}
	const auto moved = visible[oldVisiblePosition];
	visible.erase(visible.begin() + oldVisiblePosition);
	visible.insert(visible.begin() + newVisiblePosition, moved);
	for (auto i = 0; i != int(visiblePositions.size()); ++i) {
		fullOrder[visiblePositions[i]] = visible[i];
	}
	return fullOrder;
}

HiddenFoldersState::FolderId ResolveVisible(
		HiddenFoldersState::FolderId requested,
		const std::vector<HiddenFoldersState::FolderId> &fullOrder,
		std::span<const HiddenFoldersState::FolderId> hidden) {
	if (!requested) {
		return requested;
	}
	const auto requestedExists = std::find(
		fullOrder.begin(),
		fullOrder.end(),
		requested) != fullOrder.end();
	if (requestedExists && !Contains(hidden, requested)) {
		return requested;
	}
	const auto visible = VisibleIds(fullOrder, hidden);
	const auto all = std::find(visible.begin(), visible.end(), 0);
	return (all != visible.end())
		? 0
		: !visible.empty()
		? visible.front()
		: 0;
}

int VisiblePrefixSize(
		const std::vector<HiddenFoldersState::FolderId> &fullOrder,
		std::span<const HiddenFoldersState::FolderId> hidden,
		int fullExclusiveEnd) {
	const auto end = std::clamp(fullExclusiveEnd, 0, int(fullOrder.size()));
	auto result = 0;
	for (auto i = 0; i != end; ++i) {
		if (!Contains(hidden, fullOrder[i])) {
			++result;
		}
	}
	return result;
}

} // namespace HiddenFoldersPolicy
} // namespace AyuFeatures
