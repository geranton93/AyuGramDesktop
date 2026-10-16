"""Keep rotated video frames inside the requested photo-editor export size."""
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


class RotatedVideoExportGeometry(unittest.TestCase):
    def test_requested_bounds_are_transposed_before_frame_scaling(self):
        path = SOURCE / "ffmpeg/ffmpeg_frame_generator.cpp"
        source = path.read_text()
        render = function_definition(
            source,
            "FrameGenerator::Frame FrameGenerator::Impl::renderCurrent(",
        )
        transpose = render.index("size.transpose()")
        scale = render.index("QSize(width, height).scaled(size, mode)")
        self.assertLess(transpose, scale)
        self.assertNotIn("scaled.transpose()", render)


if __name__ == "__main__":
    unittest.main()
