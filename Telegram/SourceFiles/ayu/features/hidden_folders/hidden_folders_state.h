// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <span>
#include <vector>

namespace AyuFeatures {

class HiddenFoldersState final {
public:
	using SessionId = std::uint64_t;
	using FolderId = std::int32_t;
	using Sessions = std::map<SessionId, std::vector<FolderId>>;

	static constexpr auto kMaxSessions = std::size_t(16);
	static constexpr auto kMaxFoldersPerSession = std::size_t(256);

	[[nodiscard]] bool isHidden(SessionId sessionId, FolderId id) const;
	[[nodiscard]] bool setHidden(
		SessionId sessionId,
		FolderId id,
		bool hidden);
	[[nodiscard]] bool replace(
		SessionId sessionId,
		const std::vector<FolderId> &ids);
	[[nodiscard]] bool reconcile(
		SessionId sessionId,
		const std::vector<FolderId> &validIds);

	[[nodiscard]] const std::vector<FolderId> &hidden(
		SessionId sessionId) const;
	[[nodiscard]] const Sessions &sessions() const;

private:
	Sessions _sessions;

};

namespace HiddenFoldersPolicy {

[[nodiscard]] std::vector<HiddenFoldersState::FolderId> VisibleIds(
	const std::vector<HiddenFoldersState::FolderId> &fullOrder,
	std::span<const HiddenFoldersState::FolderId> hidden);

[[nodiscard]] std::vector<HiddenFoldersState::FolderId> ReorderVisible(
	std::vector<HiddenFoldersState::FolderId> fullOrder,
	std::span<const HiddenFoldersState::FolderId> hidden,
	int oldVisiblePosition,
	int newVisiblePosition);

[[nodiscard]] HiddenFoldersState::FolderId ResolveVisible(
	HiddenFoldersState::FolderId requested,
	const std::vector<HiddenFoldersState::FolderId> &fullOrder,
	std::span<const HiddenFoldersState::FolderId> hidden);

[[nodiscard]] int VisiblePrefixSize(
	const std::vector<HiddenFoldersState::FolderId> &fullOrder,
	std::span<const HiddenFoldersState::FolderId> hidden,
	int fullExclusiveEnd);

} // namespace HiddenFoldersPolicy
} // namespace AyuFeatures
