// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <unordered_set>
#include <utility>

namespace Ayu {

class GhostModePeerExceptions final {
public:
	using SerializedPeerId = std::uint64_t;
	using Values = std::unordered_set<SerializedPeerId>;

	static constexpr auto kMaxValues = std::size_t(256);

	[[nodiscard]] bool shouldSend(
			bool globallyEnabled,
			std::optional<SerializedPeerId> peerId) const {
		return globallyEnabled || (peerId && contains(*peerId));
	}

	[[nodiscard]] bool contains(SerializedPeerId peerId) const {
		return _values.contains(peerId);
	}

	bool set(SerializedPeerId peerId, bool enabled) {
		if (!peerId) {
			return false;
		}
		if (!enabled) {
			return _values.erase(peerId) != 0;
		}
		if (_values.contains(peerId) || _values.size() >= kMaxValues) {
			return false;
		}
		_values.emplace(peerId);
		return true;
	}

	[[nodiscard]] const Values &values() const {
		return _values;
	}

	void replace(Values values) {
		auto limited = Values();
		limited.reserve(std::min(values.size(), kMaxValues));
		for (const auto value : values) {
			if (!value || limited.size() >= kMaxValues) {
				continue;
			}
			limited.emplace(value);
		}
		_values = std::move(limited);
	}

private:
	Values _values;

};

} // namespace Ayu
