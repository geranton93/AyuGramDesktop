"""Guard saved-message sublist teardown against dangling message links."""

import os
import pathlib
import unittest

SOURCE = pathlib.Path(os.environ.get(
    "AYU_SOURCE_ROOT",
    pathlib.Path(__file__).resolve().parents[2] / "SourceFiles",
))


def function_definition(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    cursor = opening + 1
    while depth:
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
        cursor += 1
    return source[start:cursor]


class SavedSublistTeardownOrder(unittest.TestCase):
    def test_messages_and_change_stream_are_retired_before_owner(self):
        source = (SOURCE / "data/data_saved_messages.cpp").read_text()
        teardown = function_definition(
            source,
            "void SavedMessages::applySublistDeleted(")
        destroy_messages = teardown.index("history->destroyMessagesBySublist(")
        drop_updates = teardown.index("session().changes().sublistRemoved(raw);")
        erase_owner = teardown.index("_sublists.erase(i);")
        self.assertLess(destroy_messages, drop_updates)
        self.assertLess(drop_updates, erase_owner)

        item_source = (SOURCE / "history/history_item.cpp").read_text()
        saved_sublist = function_definition(
            item_source,
            "Data::SavedSublist *HistoryItem::savedSublist() const")
        self.assertIn("monoforum->sublist(peer).get()", saved_sublist)
        sublist = function_definition(
            source,
            "not_null<SavedSublist*> SavedMessages::sublist(")
        self.assertIn("_sublists.emplace(", sublist)


if __name__ == "__main__":
    unittest.main()
