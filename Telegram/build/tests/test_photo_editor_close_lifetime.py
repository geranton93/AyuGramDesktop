"""Ensure a closed photo-editor owner suppresses its late completion."""
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


class PhotoEditorCloseLifetime(unittest.TestCase):
    def test_prepared_file_callback_is_guarded_by_parent(self):
        source = (SOURCE / "editor/photo_editor_layer_widget.cpp").read_text()
        opened = function_body(source, "void OpenWithPreparedFile(")
        callback_start = opened.index("auto callback = ")
        callback_end = opened.index("\n\tauto copy = ", callback_start)
        callback = opened[callback_start:callback_end]
        self.assertIn("auto callback = crl::guard(parent, [=](", callback)
        self.assertIn("Storage::UpdateImageDetails(*file", callback)
        self.assertIn("doneCallback(true);", callback)
        self.assertIn(
            "InitEditorLayer(layer.get(), raw, std::move(callback));",
            opened,
        )


if __name__ == "__main__":
    unittest.main()
