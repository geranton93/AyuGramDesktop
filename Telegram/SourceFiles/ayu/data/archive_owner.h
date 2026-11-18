// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
// Copyright @Radolyn, 2026
#pragma once

#if defined __clang__
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#endif
#include "ayu/libs/sqlite/sqlite_orm.h"
#if defined __clang__
#pragma clang diagnostic pop
#endif

namespace AyuDatabase {

template <typename Message>
[[nodiscard]] auto ArchiveOwnedBy(
		long long accountId,
		long long userId,
		long long userIdMask) {
	using namespace sqlite_orm;

	const auto account = column<Message>(&Message::accountId);
	const auto owner = column<Message>(&Message::userId);
	const auto validAccount = accountId > 0
		&& userId > 0
		&& (accountId & userIdMask) == userId;
	// Older records stored only the masked session ID in userId.
	// A previous migration assigned the first reader's accountId to all
	// such records. An accountId with a different masked ID is therefore
	// invalid for the stored owner, just like the original zero value.
	return is_equal(validAccount, true)
		and is_equal(owner, userId)
		and (is_equal(account, accountId)
			or is_not_equal(bitwise_and(account, userIdMask), owner));
}

} // namespace AyuDatabase
