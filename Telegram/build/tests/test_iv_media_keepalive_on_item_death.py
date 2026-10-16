"""Release kept-alive instant-view media before its owner item dies."""
import os
import pathlib
import unittest

SOURCE = pathlib.Path(os.environ.get(
    "AYU_SOURCE_ROOT",
    pathlib.Path(__file__).resolve().parents[2] / "SourceFiles",
))


def function_body(source, signature):
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
    return source[opening + 1:cursor - 1]


def item_death_callback(function):
    start = function.index("_host->itemDeath() | rpl::on_next([this] {")
    end = function.index("}, _itemDeathLifetime);", start)
    return function[start:end]


class IvMediaKeepaliveOnItemDeath(unittest.TestCase):
    def test_block_drops_media_then_keepalive_on_item_death(self):
        source = (SOURCE / "iv/markdown/iv_markdown_history_view_media.cpp").read_text()
        self.assertEqual(
            source.count("const std::vector<std::shared_ptr<void>> _keepAlive;"),
            0,
        )
        self.assertEqual(
            source.count("std::vector<std::shared_ptr<void>> _keepAlive;"),
            2,
        )

        block = function_body(source, "IvHistoryViewBlock::IvHistoryViewBlock(")
        callback = item_death_callback(block)
        self.assertIn("_keepAlive.clear();", callback)
        self.assertLess(
            callback.index("_media = nullptr;"),
            callback.index("_keepAlive.clear();"),
        )

    def test_slideshow_drops_slides_then_keepalive_on_item_death(self):
        source = (SOURCE / "iv/markdown/iv_markdown_history_view_media.cpp").read_text()
        slideshow = function_body(
            source,
            "IvHistoryViewSlideshowBlock::IvHistoryViewSlideshowBlock(",
        )
        callback = item_death_callback(slideshow)
        self.assertIn("for (auto &media : _slides)", callback)
        self.assertIn("media = nullptr;", callback)
        self.assertIn("_keepAlive.clear();", callback)
        self.assertLess(
            callback.index("media = nullptr;"),
            callback.index("_keepAlive.clear();"),
        )


if __name__ == "__main__":
    unittest.main()
