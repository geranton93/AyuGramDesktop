// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#include "ayu/features/hidden_folders/hidden_folders.h"

#include "ayu/ayu_settings.h"
#include "data/data_chat_filters.h"
#include "lang/lang_keys.h"
#include "ui/widgets/menu/menu_add_action_callback.h"

#include "styles/style_menu_icons.h"

#include <algorithm>

namespace AyuFeatures::HiddenFolders {

bool IsHidden(const uint64 accountId, const FilterId id) {
	return id && accountId
		&& AyuSettings::getInstance().isFolderHidden(accountId, id);
}

bool ToggleHidden(const uint64 accountId, const FilterId id) {
	if (!accountId || !id) {
		return false;
	}
	const auto hidden = IsHidden(accountId, id);
	auto &settings = AyuSettings::getInstance();
	if (hidden) {
		settings.removeHiddenFolder(accountId, id);
	} else {
		settings.addHiddenFolder(accountId, id);
	}
	return IsHidden(accountId, id);
}

std::vector<FilterId> VisibleIds(
		const uint64 accountId,
		const std::vector<Data::ChatFilter> &list) {
	auto result = std::vector<FilterId>();
	result.reserve(list.size());
	for (const auto &filter : list) {
		if (!IsHidden(accountId, filter.id())) {
			result.push_back(filter.id());
		}
	}
	return result;
}

std::vector<Data::ChatFilter> VisibleOnly(
		const uint64 accountId,
		const std::vector<Data::ChatFilter> &list) {
	auto result = std::vector<Data::ChatFilter>();
	result.reserve(list.size());
	for (const auto &filter : list) {
		if (!IsHidden(accountId, filter.id())) {
			result.push_back(filter);
		}
	}
	return result;
}

int VisiblePremiumFrom(
		const uint64 accountId,
		const std::vector<Data::ChatFilter> &list,
		const int premiumFrom) {
	const auto count = std::min(premiumFrom, int(list.size()));
	auto result = 0;
	for (auto i = 0; i < count; ++i) {
		if (!IsHidden(accountId, list[i].id())) {
			++result;
		}
	}
	return result;
}

void AddToggleAction(
		const Ui::Menu::MenuCallback &addAction,
		const uint64 accountId,
		const FilterId id) {
	if (!accountId || !id) {
		return;
	}
	const auto hidden = IsHidden(accountId, id);
	addAction(
		hidden ? tr::ayu_ShowFolder(tr::now) : tr::ayu_HideFolder(tr::now),
		[=] {
			[[maybe_unused]] const auto result = ToggleHidden(accountId, id);
		},
		hidden ? &st::menuIconUserShow : &st::menuIconUserHide);
}

} // namespace AyuFeatures::HiddenFolders
