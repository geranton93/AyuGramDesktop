"""Disarm media TTLs before service-message conversion drops media state."""
import os
import pathlib
import unittest

SOURCE = pathlib.Path(os.environ.get(
    "AYU_SOURCE_ROOT",
    pathlib.Path(__file__).resolve().parents[2] / "SourceFiles",
))


def block_body(source, marker):
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 1
    cursor = opening + 1
    while depth:
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
        cursor += 1
    return source[opening + 1:cursor - 1]


class HistoryClearSelfDestruct(unittest.TestCase):
    def test_clear_and_service_replacement_disarm_media_destroy(self):
        source = (SOURCE / "history/history_item.cpp").read_text()
        edition = block_body(
            source,
            "void HistoryItem::applyEdition(const MTPDmessageService &message)",
        )
        branches = (
            (
                "history clear",
                block_body(
                    edition,
                    "if (message.vaction().type() == mtpc_messageActionHistoryClear)",
                ),
            ),
            (
                "service replacement",
                block_body(edition, "else if (isService())"),
            ),
        )
        for name, branch in branches:
            with self.subTest(branch=name):
                self.assertIn("unarmMediaDestroy();", branch)
                self.assertIn("UpdateComponents(0);", branch)
                self.assertLess(
                    branch.index("unarmMediaDestroy();"),
                    branch.index("UpdateComponents(0);"),
                )


if __name__ == "__main__":
    unittest.main()
