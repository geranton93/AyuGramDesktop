"""Clear the story comments dot before replacing its owning button."""
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


class StoryCommentsDotLifetime(unittest.TestCase):
    def test_old_dot_and_button_are_cleared_before_new_button_creation(self):
        path = SOURCE / "history/view/controls/history_view_compose_controls.cpp"
        source = path.read_text()
        update = function_definition(
            source,
            "void ComposeControls::setToggleCommentsButton(",
        )
        clear_dot = update.index("_commentsShownNewDot = nullptr;")
        delete_button = update.index("delete base::take(_commentsShown);")
        create_button = update.index("_commentsShown = Ui::CreateChild<Ui::IconButton>")
        self.assertLess(clear_dot, delete_button)
        self.assertLess(delete_button, create_button)


if __name__ == "__main__":
    unittest.main()
