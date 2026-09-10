// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#pragma once

#include "data/data_types.h"

#include <cstddef>
#include <vector>

namespace Data {
class ChatFilter;
} // namespace Data

namespace Ui::Menu {
struct MenuCallback;
} // namespace Ui::Menu

namespace AyuFeatures::HiddenFolders {

// Hidden folders remain in the data model so their membership, unread state,
// and per-dialog notifications stay intact. Only folder navigation surfaces
// hide them. The settings folder list and folder-management dialogs keep them
// available so a hidden folder can always be restored.

[[nodiscard]] bool IsHidden(uint64 accountId, FilterId id);
[[nodiscard]] bool ToggleHidden(uint64 accountId, FilterId id);
[[nodiscard]] std::vector<FilterId> VisibleIds(
	uint64 accountId,
	const std::vector<Data::ChatFilter> &list);
[[nodiscard]] std::vector<Data::ChatFilter> VisibleOnly(
	uint64 accountId,
	const std::vector<Data::ChatFilter> &list);
[[nodiscard]] int VisiblePremiumFrom(
	uint64 accountId,
	const std::vector<Data::ChatFilter> &list,
	int premiumFrom);
void AddToggleAction(
	const Ui::Menu::MenuCallback &addAction,
	uint64 accountId,
	FilterId id);

template <typename Id, typename IsHiddenFn>
[[nodiscard]] std::vector<Id> ReorderVisible(
		std::vector<Id> fullOrder,
		int oldVisiblePosition,
		int newVisiblePosition,
		const IsHiddenFn &isHidden) {
	if (oldVisiblePosition == newVisiblePosition) {
		return fullOrder;
	}

	auto visibleAt = std::vector<std::size_t>();
	visibleAt.reserve(fullOrder.size());
	for (auto i = std::size_t(0); i != fullOrder.size(); ++i) {
		if (!isHidden(fullOrder[i])) {
			visibleAt.push_back(i);
		}
	}
	if (oldVisiblePosition < 0
		|| newVisiblePosition < 0
		|| std::size_t(oldVisiblePosition) >= visibleAt.size()
		|| std::size_t(newVisiblePosition) >= visibleAt.size()) {
		return fullOrder;
	}

	auto visibleIds = std::vector<Id>();
	visibleIds.reserve(visibleAt.size());
	for (const auto index : visibleAt) {
		visibleIds.push_back(fullOrder[index]);
	}
	const auto moved = visibleIds[oldVisiblePosition];
	visibleIds.erase(visibleIds.begin() + oldVisiblePosition);
	visibleIds.insert(
		visibleIds.begin() + newVisiblePosition,
		moved);

	auto result = fullOrder;
	auto cursor = 0;
	for (auto i = std::size_t(0); i != result.size(); ++i) {
		if (!isHidden(fullOrder[i])) {
			result[i] = visibleIds[cursor++];
		}
	}
	return result;
}

} // namespace AyuFeatures::HiddenFolders
