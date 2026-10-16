// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#pragma once

#include "base/weak_ptr.h"
#include "data/data_types.h"
#include "rpl/event_stream.h"

#include <vector>

namespace Data {
class ChatFilter;
} // namespace Data

namespace Main {
class Session;
} // namespace Main

namespace Ui::Menu {
struct MenuCallback;
} // namespace Ui::Menu

namespace AyuFeatures {

class HiddenFolders final : public base::has_weak_ptr {
public:
	explicit HiddenFolders(not_null<Main::Session*> session);

	[[nodiscard]] bool isHidden(FilterId id) const;
	bool toggle(FilterId id);
	void reconcile(const std::vector<Data::ChatFilter> &list);

	[[nodiscard]] std::vector<FilterId> visibleIds(
		const std::vector<Data::ChatFilter> &list) const;
	[[nodiscard]] std::vector<FilterId> reorderVisible(
		const std::vector<Data::ChatFilter> &list,
		int oldVisiblePosition,
		int newVisiblePosition) const;
	[[nodiscard]] FilterId resolveVisible(
		FilterId requested,
		const std::vector<Data::ChatFilter> &list) const;
	[[nodiscard]] int visiblePrefixSize(
		const std::vector<Data::ChatFilter> &list,
		int fullExclusiveEnd) const;

	void addToggleAction(
		const Ui::Menu::MenuCallback &addAction,
		FilterId id);
	[[nodiscard]] rpl::producer<> changes() const;

private:
	const uint64 _sessionId = 0;
	rpl::event_stream<> _changes;

};

} // namespace AyuFeatures
