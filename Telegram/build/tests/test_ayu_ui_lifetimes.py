"""Source-shape guards, not native lifetime tests or a C++ parser.

Functions are delimited by their column-zero closing brace. Whitespace and
comments are ignored; legitimate structural refactors require guard review.
The mutation cases validate these guards, not runtime C++ behavior.
"""
import pathlib
import re
import unittest
from unittest import mock

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "SourceFiles"


class UiLifetimeContracts(unittest.TestCase):
    def function_source(self, filename, name):
        source = (SOURCE / filename).read_text(encoding="utf-8")
        match = re.search(
            r"^void " + name + r"\(.*?^}", source, re.MULTILINE | re.DOTALL
        )
        self.assertIsNotNone(match, name)
        body = re.sub(r"//[^\n]*|/\*.*?\*/", "", match.group(), flags=re.DOTALL)
        return re.sub(r"\s+", "", body)

    def test_plugin_icon_wait_belongs_to_visible_widget(self):
        source = self.function_source(
            "ayu/ui/boxes/plugin_info_box.cpp", "FillPluginInfoBox"
        )
        self.assertIn("media->checkStickerLarge();", source)
        self.assertIn("if(media->loaded()){createPlayer();}else{", source)
        self.assertIn(
            "rpl::single()|rpl::then("
            "stickerDoc->owner().session().downloaderTaskFinished()"
            ")|rpl::filter([=]{returnmedia->loaded();})"
            "|rpl::take(1)|rpl::on_next([=]{createPlayer();},widget->lifetime());",
            source,
        )

    def test_plugin_box_does_not_wait_on_session_downloads(self):
        source = self.function_source(
            "ayu/ui/boxes/plugin_info_box.cpp", "ShowPluginInfoBox"
        )
        self.assertNotIn("downloaderTaskFinished(", source)
        self.assertNotIn("createMediaView(", source)
        self.assertIn("showBox(std::move(*shared),doc);", source)

    def test_topic_completion_uses_weak_reference(self):
        source = self.function_source(
            "ayu/ui/context_menu/context_menu.cpp", "AddJumpToBeginningAction"
        )
        guard = re.search(r"constauto(\w+)=base::make_weak\(topic\);", source)
        self.assertIsNotNone(guard)
        name = guard.group(1)
        self.assertLess(guard.start(), source.index("addCallback("))
        controller_guard = re.search(
            r"constauto(\w+)=base::make_weak\(controller\);"
            r"addCallback\(tr::ayu_JumpToBeginning\(tr::now\),"
            r"\[=\]\{if\(!\1\)\{return;\}"
            r"constautotopic=" + name + r"\.get\(\);"
            r"if\(hadTopic&&!topic\)\{return;\}",
            source,
        )
        self.assertIsNotNone(controller_guard)
        self.assertIn("constautohadTopic=(topic!=nullptr);", source)
        self.assertRegex(
            source,
            r"constautotopic=" + name + r"\.get\(\);",
        )
        self.assertRegex(
            source,
            r"jumpToDate\(topic,\[=\]\(not_null<PeerData\*>,MsgId(\w+)\)\{"
            r"if\(constauto(\w+)=" + name + r"\.get\(\)\)\{"
            r"showTopic\(\2,\1\);\}\}\);",
        )


class UiLifetimeGuardMutations(unittest.TestCase):
    def assert_rejected(self, filename, old, new, method):
        path = SOURCE / filename
        source = path.read_text(encoding="utf-8")
        self.assertIn(old, source)
        mutated = source.replace(old, new, 1)
        with mock.patch.object(
            pathlib.Path, "read_text", return_value=mutated
        ), self.assertRaises(AssertionError):
            getattr(UiLifetimeContracts(), method)()

    def test_rejects_session_owned_icon_wait(self):
        self.assert_rejected(
            "ayu/ui/boxes/plugin_info_box.cpp",
            "createPlayer();\n\t\t\t}, widget->lifetime());",
            "createPlayer();\n\t\t\t}, stickerDoc->owner().session().lifetime());",
            "test_plugin_icon_wait_belongs_to_visible_widget",
        )

    def test_rejects_repeated_player_creation(self):
        self.assert_rejected(
            "ayu/ui/boxes/plugin_info_box.cpp",
            "| rpl::take(1)",
            "",
            "test_plugin_icon_wait_belongs_to_visible_widget",
        )

    def test_rejects_missing_sticker_load(self):
        self.assert_rejected(
            "ayu/ui/boxes/plugin_info_box.cpp",
            "media->checkStickerLarge();",
            "// media->checkStickerLarge();",
            "test_plugin_icon_wait_belongs_to_visible_widget",
        )

    def test_rejects_unguarded_topic_navigation(self):
        self.assert_rejected(
            "ayu/ui/context_menu/context_menu.cpp",
            "if (const auto topic = weakTopic.get())",
            "if (true)",
            "test_topic_completion_uses_weak_reference",
        )

    def test_rejects_missing_menu_controller_guard(self):
        self.assert_rejected(
            "ayu/ui/context_menu/context_menu.cpp",
            "if (!weakController)",
            "if (false)",
            "test_topic_completion_uses_weak_reference",
        )

    def test_rejects_missing_menu_topic_guard(self):
        self.assert_rejected(
            "ayu/ui/context_menu/context_menu.cpp",
            "if (hadTopic && !topic)",
            "if (false)",
            "test_topic_completion_uses_weak_reference",
        )

    def test_accepts_topic_local_renaming_and_whitespace(self):
        source = (SOURCE / "ayu/ui/context_menu/context_menu.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("weakTopic", source)
        source = source.replace("weakTopic", "pendingTopic")
        source = source.replace("\t", "    ").replace("\n", "\r\n")
        with mock.patch.object(pathlib.Path, "read_text", return_value=source):
            UiLifetimeContracts().test_topic_completion_uses_weak_reference()

    def test_accepts_whitespace_and_comments_in_icon_wait(self):
        source = (SOURCE / "ayu/ui/boxes/plugin_info_box.cpp").read_text(
            encoding="utf-8"
        )
        source = source.replace("widget->lifetime()", "widget -> lifetime( )")
        source = source.replace("createPlayer();", "createPlayer(/* ready */);")
        with mock.patch.object(pathlib.Path, "read_text", return_value=source):
            UiLifetimeContracts().test_plugin_icon_wait_belongs_to_visible_widget()

    def test_rejects_missing_topic_navigation(self):
        self.assert_rejected(
            "ayu/ui/context_menu/context_menu.cpp",
            "showTopic(topic, id);",
            "return;",
            "test_topic_completion_uses_weak_reference",
        )


if __name__ == "__main__":
    unittest.main()
