#include "ayu/features/filters/filter_regex.h"

#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>

namespace {

void Require(bool condition) {
	if (!condition) {
		std::abort();
	}
}

std::unique_ptr<icu::RegexPattern> Compile(const char *text) {
	auto status = U_ZERO_ERROR;
	auto result = std::unique_ptr<icu::RegexPattern>(
		icu::RegexPattern::compile(icu::UnicodeString::fromUTF8(text), 0, status));
	Require(U_SUCCESS(status) && result != nullptr);
	return result;
}

} // namespace

int main() {
	const auto simple = Compile("hello");
	const auto hello = icu::UnicodeString::fromUTF8("hello world");
	const auto other = icu::UnicodeString::fromUTF8("other");
	auto status = U_ZERO_ERROR;
	Require(FiltersController::MatchPattern(*simple, false, hello, status));
	Require(U_SUCCESS(status));
	Require(!FiltersController::MatchPattern(*simple, true, hello, status));
	Require(!FiltersController::MatchPattern(*simple, false, other, status));
	Require(FiltersController::MatchPattern(*simple, true, other, status));

	// Local synthetic input only: provoke ICU's bounded backtracking failure.
	const auto expensive = Compile("(a+)+$");
	const auto input = icu::UnicodeString::fromUTF8(std::string(10000, 'a') + "!");
	for (const auto reversed : { false, true }) {
		status = U_ZERO_ERROR;
		Require(!FiltersController::MatchPattern(*expensive, reversed, input, status));
		Require(status == U_REGEX_TIME_OUT);
	}
	std::puts("filter_regex_test: normal/reversed matches and timeouts passed");
}
