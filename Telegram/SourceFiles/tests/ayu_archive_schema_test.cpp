/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#include "ayu/libs/sqlite/sqlite_orm.h"

#include <cstdio>
#include <string>
#include <vector>

namespace {

using namespace sqlite_orm;

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

	// Archived rows belong to the account that wrote them: a query scoped to
	// one account must not return another account's rows, and a row written
	// before accounts were tracked must end up owned by exactly one account.
	{
		const auto path = TempPath("scoped");
		std::remove(path.c_str());
		auto storage = make_storage(
			path,
			CurrentTable());
		storage.sync_schema();
		storage.insert(ArchiveRow{
			.fakeId = 0,
			.accountId = 1,
			.userId = 42,
			.dialogId = 7,
			.messageId = 1,
			.text = "first",
			.mediaDc = 0,
			.mediaSize = 0,
		});
		storage.insert(ArchiveRow{
			.fakeId = 0,
			.accountId = 2,
			.userId = 42,
			.dialogId = 7,
			.messageId = 2,
			.text = "second",
			.mediaDc = 0,
			.mediaSize = 0,
		});
		storage.insert(ArchiveRow{
			.fakeId = 0,
			.accountId = 0,
			.userId = 42,
			.dialogId = 7,
			.messageId = 3,
			.text = "legacy",
			.mediaDc = 0,
			.mediaSize = 0,
		});

		const auto first = storage.get_all<ArchiveRow>(
			where(column<ArchiveRow>(&ArchiveRow::accountId) == 1));
		if (first.size() != 1) {
			return Fail(4, "account scoped query returned rows of another account");
		}
		if (first.front().messageId != 1) {
			return Fail(5, "account scoped query returned the wrong row");
		}

		storage.update_all(
			set(c(&ArchiveRow::accountId) = 1),
			where(c(&ArchiveRow::accountId) == 0));
		const auto claimed = storage.get_all<ArchiveRow>(
			where(column<ArchiveRow>(&ArchiveRow::accountId) == 1));
		if (claimed.size() != 2) {
			return Fail(6, "unattributed rows were not claimed by the account");
		}
		const auto other = storage.get_all<ArchiveRow>(
			where(column<ArchiveRow>(&ArchiveRow::accountId) == 2));
		if (other.size() != 1) {
			return Fail(7, "the second account lost its own row");
		}
		if (other.front().messageId != 2) {
			return Fail(8, "the second account got the wrong row");
		}
		std::remove(path.c_str());
	}

	return 0;
}
