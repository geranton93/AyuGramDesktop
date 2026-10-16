#pragma once

#include <algorithm>

namespace Ayu {

struct RowsRange {
	int from = 0;
	int till = 0;
};

[[nodiscard]] inline RowsRange RowsInRange(
		int top,
		int bottom,
		int rowHeight,
		int count) {
	return {
		.from = std::clamp(top / rowHeight, 0, count),
		.till = std::clamp((bottom + rowHeight - 1) / rowHeight, 0, count),
	};
}

} // namespace Ayu
