"""Guard cached chat rows against synchronous video-userpic invalidation."""
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


class ChatListVideoUserpicCacheInvalidation(unittest.TestCase):
    def test_reacquires_cached_row_after_video_userpic_paint(self):
        source = (SOURCE / "dialogs/dialogs_inner_widget.cpp").read_text()
        paint = function_body(
            source,
            "void InnerWidget::paintCachedRowOverlays(",
        )
        self.assertIn("auto i = _cachedRows.find(rowId);", paint)
        self.assertNotIn("const auto i = _cachedRows.find(rowId);", paint)

        userpic = paint.index("row->paintUserpic(")
        reacquire = paint.find("i = _cachedRows.find(rowId);", userpic)
        self.assertNotEqual(reacquire, -1)
        guard = paint.find("if (i == end(_cachedRows))", reacquire)
        self.assertNotEqual(guard, -1)
        cache_use = paint.index("i->second.", userpic)
        self.assertLess(userpic, reacquire)
        self.assertLess(reacquire, guard)
        self.assertLess(guard, cache_use)
        guard_end = paint.index("}", guard)
        self.assertIn("return;", paint[guard:guard_end])


if __name__ == "__main__":
    unittest.main()
