import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SOURCE = ROOT / "Telegram" / "SourceFiles" / "media" / "player"


def function(source, signature):
    start = source.find(signature)
    if start < 0:
        raise AssertionError(f"Missing production function: {signature}")
    opening = source.find("{", start)
    if opening < 0:
        raise AssertionError(f"Missing body for production function: {signature}")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"Unclosed body for production function: {signature}")


class MediaPlayerDropdownExclusion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dropdown = (SOURCE / "media_player_dropdown.cpp").read_text()
        cls.widget = (SOURCE / "media_player_widget.cpp").read_text()

    def test_fast_hide_cancels_show_and_hide_before_completion(self):
        hide = function(self.dropdown, "void Dropdown::hideFast()")
        self.assertLess(hide.index("_showTimer.cancel()"), hide.index("hidingFinished()"))
        self.assertLess(hide.index("_hideTimer.cancel()"), hide.index("hidingFinished()"))
        self.assertIn("_a_appearance.stop()", hide)
        self.assertIn("if (!isHidden())", hide)

    def test_controller_closes_only_for_a_different_visible_control(self):
        show = function(self.dropdown, "void WithDropdownController::showMenu()")
        move = function(
            self.dropdown,
            "void WithDropdownController::handleMenuMove(",
        )
        self.assertIn("QEvent::MouseMove", show)
        self.assertIn("handleMenuMove(mouse->globalPos())", show)
        self.assertIn("if (!_menu || !_otherDropdownCheck)", move)
        self.assertIn("_button->rect().contains(local)", move)
        self.assertIn("|| !_otherDropdownCheck(globalPosition)", move)
        self.assertIn("_menu->hideFast()", move)

    def test_widget_hides_other_menus_when_pointer_enters_a_control(self):
        setup_start = self.widget.index("hidePlaylistOn(_playPause)")
        setup_end = self.widget.index("setType(AudioMsgId::Type::Song)", setup_start)
        setup = self.widget[setup_start:setup_end]
        enter = function(self.widget, "void Widget::hideDropdownsOn(")
        controls = function(
            self.widget,
            "bool Widget::overDropdownControl(",
        )
        hide = function(self.widget, "void Widget::hideDropdowns(")
        self.assertIn("_orderController->setOtherDropdownCheck", setup)
        self.assertIn("_speedController->setOtherDropdownCheck", setup)
        self.assertIn("e->type() == QEvent::Enter", enter)
        self.assertIn("hideDropdowns(widget)", enter)
        self.assertIn("over(_volumeToggle)", controls)
        self.assertIn("over(_orderToggle)", controls)
        self.assertIn("over(_speedToggle)", controls)
        self.assertIn("_volume->hideFast()", hide)
        self.assertIn("menu->hideFast()", hide)


if __name__ == "__main__":
    unittest.main()
