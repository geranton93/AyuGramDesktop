// This is the source code of AyuGram for Desktop.

#pragma once

#include "ayu/features/forward/ayu_forward.h"

#include <functional>

namespace AyuForward {

[[nodiscard]] bool isSourceForwardRestricted(not_null<HistoryItem*> item);

void resolveForwardSources(
	not_null<Main::Session*> session,
	MessageIdsList itemIds,
	std::function<void(MessageIdsList)> callback);

} // namespace AyuForward
