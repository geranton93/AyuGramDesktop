"""Check that preview overlays leave menu navigation usable."""
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SOURCE = ROOT / "Telegram" / "SourceFiles" / "history" / "view" / "history_view_reaction_preview.cpp"


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


class ReactionPreviewKeyboard(unittest.TestCase):
    def test_only_escape_is_consumed_when_menu_is_open(self):
        source = SOURCE.read_text()
        body = function_body(source, "void SetupOverlayKeyHandling(")
        self.assertIn("menuVisible()", body)
        self.assertIn("Qt::Key_Escape", body)
        self.assertIn("base::EventFilterResult::Continue", body)
        self.assertIn("base::EventFilterResult::Cancel", body)
        self.assertIn("state->menuWrap->isVisible()", source)


if __name__ == "__main__":
    unittest.main()
