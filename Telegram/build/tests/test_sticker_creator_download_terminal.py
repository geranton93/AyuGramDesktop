"""Check that sticker adaptation handles terminal document load states."""
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SOURCE = ROOT / "Telegram" / "SourceFiles" / "boxes" / "sticker_creator_box.cpp"


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


class StickerCreatorDownloadTerminal(unittest.TestCase):
    def test_document_media_wait_observes_success_failure_and_cancel(self):
        source = SOURCE.read_text()
        body = function_body(source, "void LoadDocumentMedia(")
        self.assertIn("documentLoadProgress()", body)
        self.assertIn("updated.get() != document.get()", body)
        self.assertIn("state->media->loaded()", body)
        self.assertIn("!document->loading()", body)
        self.assertIn("state->lifetime.destroy()", body)
        self.assertIn("failed();", body)
        self.assertNotIn("downloaderTaskFinished()", body)

    def test_sticker_and_gif_adaptation_supply_failure_feedback(self):
        source = SOURCE.read_text()
        for signature in ("bool AdaptStickerToEmoji(", "bool AdaptGifToSet("):
            body = function_body(source, signature)
            self.assertIn("show->showToast(tr::lng_attach_failed(tr::now));", body)


if __name__ == "__main__":
    unittest.main()
