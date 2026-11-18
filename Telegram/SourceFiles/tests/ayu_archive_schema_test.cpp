/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#include "ayu/data/archive_owner.h"

#include <cstdio>
#include <string>
#include <vector>

namespace {

using namespace sqlite_orm;
using ID = long long;

struct ArchiveRow {
	long long fakeId = 0;
	long long accountId = 0;
	long long userId = 0;
	long long dialogId = 0;
	int messageId = 0;
	std::string text;
	int mediaDc = 0;
	long long mediaSize = 0;
};

[[nodiscard]] auto LegacyTable() {
	return make_table<ArchiveRow>(
		"Archive",
		make_column("fakeId", &ArchiveRow::fakeId, primary_key().autoincrement()),
		make_column("userId", &ArchiveRow::userId),
		make_column("dialogId", &ArchiveRow::dialogId),
		make_column("messageId", &ArchiveRow::messageId),
		make_column("text", &ArchiveRow::text));
}

[[nodiscard]] auto CurrentTable() {
	return make_table<ArchiveRow>(
		"Archive",
		make_column("fakeId", &ArchiveRow::fakeId, primary_key().autoincrement()),
		make_column("accountId", &ArchiveRow::accountId, default_value(0)),
		make_column("userId", &ArchiveRow::userId),
		make_column("dialogId", &ArchiveRow::dialogId),
		make_column("messageId", &ArchiveRow::messageId),
		make_column("text", &ArchiveRow::text),
		make_column("mediaDc", &ArchiveRow::mediaDc, default_value(0)),
		make_column("mediaSize", &ArchiveRow::mediaSize, default_value(0)));
}

[[nodiscard]] auto CurrentTableWithoutDefaults() {
	return make_table<ArchiveRow>(
		"Archive",
		make_column("fakeId", &ArchiveRow::fakeId, primary_key().autoincrement()),
		make_column("accountId", &ArchiveRow::accountId),
		make_column("userId", &ArchiveRow::userId),
		make_column("dialogId", &ArchiveRow::dialogId),
		make_column("messageId", &ArchiveRow::messageId),
		make_column("text", &ArchiveRow::text),
		make_column("mediaDc", &ArchiveRow::mediaDc),
		make_column("mediaSize", &ArchiveRow::mediaSize));
}

[[nodiscard]] std::string TempPath(const char *name) {
	return std::string("/tmp/ayu-archive-schema-") + name + ".db";
}

int Fail(int code, const char *what) {
	std::fprintf(stderr, "FAIL: %s\n", what);
	return code;
}

} // namespace

int main() {
	// An archive written by an older build must survive the schema sync of a
	// newer build: a column added without a default makes sqlite_orm drop and
	// recreate the table, which silently destroys every archived row.
	{
		const auto path = TempPath("preserve");
		std::remove(path.c_str());
		{
			auto storage = make_storage(
				path,
				LegacyTable());
			storage.sync_schema();
			for (auto i = 0; i < 3; ++i) {
				storage.insert(ArchiveRow{
					.fakeId = 0,
					.accountId = 0,
					.userId = 42,
					.dialogId = 7,
					.messageId = i + 1,
					.text = "archived",
					.mediaDc = 0,
					.mediaSize = 0,
				});
			}
		}
		{
			auto storage = make_storage(
				path,
				CurrentTable());
			storage.sync_schema(true);
			const auto rows = storage.get_all<ArchiveRow>();
			if (rows.size() != 3) {
				return Fail(1, "rows were lost while adding columns with defaults");
			}
			for (const auto &row : rows) {
				if (row.accountId != 0 || row.mediaDc != 0 || row.mediaSize != 0) {
					return Fail(2, "added columns did not take their default value");
				}
			}
		}
		std::remove(path.c_str());
	}

	// The control: the same migration without defaults must destroy the rows.
	// If this stops holding, the preservation case above is no longer proving
	// anything.
	{
		const auto path = TempPath("destroy");
		std::remove(path.c_str());
		{
			auto storage = make_storage(
				path,
				LegacyTable());
			storage.sync_schema();
			for (auto i = 0; i < 3; ++i) {
				storage.insert(ArchiveRow{
					.fakeId = 0,
					.accountId = 0,
					.userId = 42,
					.dialogId = 7,
					.messageId = i + 1,
					.text = "archived",
					.mediaDc = 0,
					.mediaSize = 0,
				});
			}
		}
		{
			auto storage = make_storage(
				path,
				CurrentTableWithoutDefaults());
			storage.sync_schema(true);
			const auto rows = storage.get_all<ArchiveRow>();
			if (!rows.empty()) {
				return Fail(3, "columns without defaults no longer drop the table");
			}
		}
		std::remove(path.c_str());
	}

	{
		constexpr auto kFirstUser = 42LL;
		constexpr auto kSecondUser = 43LL;
		constexpr auto kUserIdMask = 0xFFFFFFFFFFFFLL;
		constexpr auto kExplicitAccount = (1LL << 48) | kSecondUser;
		auto storage = make_storage("", CurrentTable());
		storage.sync_schema();
		const auto insert = [&](ID accountId, ID userId, int messageId) {
			storage.insert(ArchiveRow{
				.accountId = accountId,
				.userId = userId,
				.dialogId = 7,
				.messageId = messageId,
			});
		};
		insert(kFirstUser, kFirstUser, 1);
		insert(kSecondUser, kSecondUser, 2);
		insert(0, kFirstUser, 3);
		insert(0, kSecondUser, 4);
		insert(kFirstUser, kSecondUser, 5);
		insert(kFirstUser, 0, 6);
		insert(kExplicitAccount, kSecondUser, 7);

		const auto owned = [](ID accountId, ID userId) {
			return AyuDatabase::ArchiveOwnedBy<ArchiveRow>(
				accountId, userId, kUserIdMask);
		};
		const auto readIds = [&](ID accountId, ID userId) {
			return storage.select(
				&ArchiveRow::messageId,
				where(owned(accountId, userId)),
				order_by(&ArchiveRow::messageId));
		};
		if (readIds(kFirstUser, kFirstUser) != std::vector<int>{ 1, 3 }) {
			return Fail(4, "first account read another owner's row");
		}
		if (readIds(kSecondUser, kSecondUser) != std::vector<int>{ 2, 4, 5 }) {
			return Fail(5, "second account lost a legacy row");
		}
		if (readIds(kExplicitAccount, kSecondUser) != std::vector<int>{ 4, 5, 7 }) {
			return Fail(6, "explicitly scoped row became available to the wrong account");
		}
		if (!readIds(0, 0).empty()
			|| !readIds(0, kSecondUser).empty()
			|| !readIds(kFirstUser, kSecondUser).empty()) {
			return Fail(7, "invalid account identity exposed archived rows");
		}
		const auto accounts = storage.select(
			&ArchiveRow::accountId, order_by(&ArchiveRow::messageId));
		if (accounts != std::vector<ID>{
				kFirstUser, kSecondUser, 0, 0, kFirstUser, kFirstUser, kExplicitAccount }) {
			return Fail(8, "reading the archive reassigned stored rows");
		}
		storage.remove_all<ArchiveRow>(where(owned(kFirstUser, kFirstUser)));
		if (!readIds(kFirstUser, kFirstUser).empty()
			|| readIds(kSecondUser, kSecondUser) != std::vector<int>{ 2, 4, 5 }) {
			return Fail(9, "purging the first account left rows or removed another owner's row");
		}
	}

	return 0;
}
