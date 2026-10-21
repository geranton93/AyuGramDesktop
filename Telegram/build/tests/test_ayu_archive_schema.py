import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATABASE = ROOT / "Telegram" / "SourceFiles" / "ayu" / "data" / "ayu_database.cpp"
STORAGE = ROOT / "Telegram" / "SourceFiles" / "ayu" / "data" / "messages_storage.cpp"

FIRST_RELEASE_COLUMNS = {
    "fakeId", "userId", "dialogId", "groupedId", "peerId", "fromId", "topicId",
    "messageId", "date", "flags", "editDate", "views", "fwdFlags", "fwdFromId",
    "fwdName", "fwdDate", "fwdPostAuthor", "replyFlags", "replyMessageId",
    "replyPeerId", "replyTopId", "replyForumTopic", "replySerialized",
    "entityCreateDate", "text", "textEntities", "mediaPath", "hqThumbPath",
    "documentType", "documentSerialized", "thumbsSerialized",
    "documentAttributesSerialized", "mimeType",
}

TABLE_PATTERN = re.compile(
    r"make_table<(?P<table>DeletedMessage|EditedMessage)>\((?P<body>.*?)\n\t\),",
    re.S,
)
COLUMN_PATTERN = re.compile(
    r"make_column\(\s*\"(?P<name>\w+)\"\s*,\s*&(?P<table>\w+)::(?P<field>\w+)\s*(?P<rest>[^)]*)\)",
    re.S,
)


class ArchiveSchemaTests(unittest.TestCase):
    def setUp(self):
        self.source = DATABASE.read_text(encoding="utf-8")
        self.tables = {}
        for match in TABLE_PATTERN.finditer(self.source):
            self.tables[match.group("table")] = match.group("body")

    def test_archive_tables_exist(self):
        self.assertIn("DeletedMessage", self.tables)
        self.assertIn("EditedMessage", self.tables)

    def test_every_column_added_after_the_first_release_has_a_default(self):
        # A column added to an existing table without a default makes
        # sqlite_orm drop and recreate the table on the next sync, which
        # destroys every archived row written by the previous build. The
        # columns of the first release are the only ones allowed to have no
        # default: they were present when the table was created.
        for table, body in self.tables.items():
            columns = list(COLUMN_PATTERN.finditer(body))
            self.assertTrue(columns, f"no columns parsed for {table}")
            for column in columns:
                name = column.group("name")
                rest = column.group("rest")
                if "primary_key" in rest:
                    continue
                if name in FIRST_RELEASE_COLUMNS:
                    continue
                self.assertIn(
                    "default_value(",
                    rest,
                    f"{table}.{name} has no default value: adding it would "
                    f"drop and recreate the table, losing archived rows",
                )

    def test_first_release_columns_are_unchanged(self):
        for table, body in self.tables.items():
            names = {column.group("name") for column in COLUMN_PATTERN.finditer(body)}
            missing = FIRST_RELEASE_COLUMNS - names
            self.assertFalse(
                missing,
                f"{table} lost columns of the first release: {sorted(missing)}",
            )

    def test_archive_rows_are_scoped_to_an_account(self):
        self.assertIn("accountId", self.tables["DeletedMessage"])
        self.assertIn("accountId", self.tables["EditedMessage"])
        for table in ("DeletedMessage", "EditedMessage"):
            self.assertIn(
                f"column<{table}>(&{table}::accountId)",
                self.source,
                f"{table} queries are not scoped by account",
            )

    def test_writes_record_the_account(self):
        storage = STORAGE.read_text(encoding="utf-8")
        self.assertIn("message.accountId", storage)
        self.assertIn("session.userId().bare", storage)


if __name__ == "__main__":
    unittest.main()
