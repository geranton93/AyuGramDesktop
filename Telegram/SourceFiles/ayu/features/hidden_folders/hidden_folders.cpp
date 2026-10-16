// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#include "ayu/features/hidden_folders/hidden_folders.h"

#include "ayu/features/hidden_folders/hidden_folders_state.h"
#include "ayu/ayu_settings.h"
#include "data/data_chat_filters.h"
#include "lang/lang_keys.h"
#include "main/main_session.h"
#include "ui/widgets/menu/menu_add_action_callback.h"

#include "styles/style_ayu_icons.h"

namespace AyuFeatures {
namespace {

[[nodiscard]] std::vector<FilterId> FullOrder(
		const std::vector<Data::ChatFilter> &list) {
	auto result = std::vector<FilterId>();
	result.reserve(list.size());
	for (const auto &filter : list) {
		result.push_back(filter.id());
	}
	return result;
}

} // namespace

HiddenFolders::HiddenFolders(not_null<Main::Session*> session)
: _sessionId(session->uniqueId()) {
}

bool HiddenFolders::isHidden(FilterId id) const {
	return AyuSettings::getInstance().isFolderHidden(_sessionId, id);
}

bool HiddenFolders::toggle(FilterId id) {
	if (id <= 0) {
		return false;
	}
	auto &settings = AyuSettings::getInstance();
	const auto hidden = !settings.isFolderHidden(_sessionId, id);
	if (!settings.setFolderHidden(_sessionId, id, hidden)) {
		return false;
	}
	_changes.fire({});
	return true;
}

void HiddenFolders::reconcile(const std::vector<Data::ChatFilter> &list) {
	AyuSettings::getInstance().reconcileHiddenFolders(
		_sessionId,
		FullOrder(list));
}

std::vector<FilterId> HiddenFolders::visibleIds(
		const std::vector<Data::ChatFilter> &list) const {
	return HiddenFoldersPolicy::VisibleIds(
		FullOrder(list),
		AyuSettings::getInstance().hiddenFolderIds(_sessionId));
}

std::vector<FilterId> HiddenFolders::reorderVisible(
		const std::vector<Data::ChatFilter> &list,
		int oldVisiblePosition,
		int newVisiblePosition) const {
	return HiddenFoldersPolicy::ReorderVisible(
		FullOrder(list),
		AyuSettings::getInstance().hiddenFolderIds(_sessionId),
		oldVisiblePosition,
		newVisiblePosition);
}

FilterId HiddenFolders::resolveVisible(
		FilterId requested,
		const std::vector<Data::ChatFilter> &list) const {
	return HiddenFoldersPolicy::ResolveVisible(
		requested,
		FullOrder(list),
		AyuSettings::getInstance().hiddenFolderIds(_sessionId));
}

int HiddenFolders::visiblePrefixSize(
		const std::vector<Data::ChatFilter> &list,
		int fullExclusiveEnd) const {
	return HiddenFoldersPolicy::VisiblePrefixSize(
		FullOrder(list),
		AyuSettings::getInstance().hiddenFolderIds(_sessionId),
		fullExclusiveEnd);
}

void HiddenFolders::addToggleAction(
		const Ui::Menu::MenuCallback &addAction,
		FilterId id) {
	if (id <= 0) {
		return;
	}
	const auto hidden = isHidden(id);
	addAction(
		hidden ? tr::ayu_ShowFolder(tr::now) : tr::ayu_HideFolder(tr::now),
		crl::guard(this, [=] { toggle(id); }),
		hidden ? &st::ayuFolderShowIcon : &st::ayuFolderHideIcon);
}

rpl::producer<> HiddenFolders::changes() const {
	return _changes.events();
}

} // namespace AyuFeatures
