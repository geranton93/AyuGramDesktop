"""Guard story sibling text rendering when its layout has zero width."""
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


class StorySiblingNameImage(unittest.TestCase):
    def test_null_canvas_returns_before_painter_creation(self):
        path = SOURCE / "media/stories/media_stories_sibling.cpp"
        source = path.read_text()
        name_image = function_definition(source, "QImage Sibling::nameImage(")
        allocation = name_image.index("_nameImage = QImage(")
        null_guard = name_image.index("if (_nameImage.isNull())", allocation)
        early_return = name_image.index("return _nameImage;", null_guard)
        painter = name_image.index("auto p = Painter(&_nameImage);", allocation)
        self.assertLess(allocation, null_guard)
        self.assertLess(null_guard, early_return)
        self.assertLess(early_return, painter)


if __name__ == "__main__":
    unittest.main()
