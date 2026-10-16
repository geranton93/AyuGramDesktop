"""Bound and efficiently rebuild the story author image cache."""
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


class StoryUserpicsCacheBound(unittest.TestCase):
    def test_cache_is_replaced_by_images_for_current_sources(self):
        path = SOURCE / "dialogs/ui/dialogs_stories_content.cpp"
        source = path.read_text()
        next_content = function_definition(source, "Content State::next()")

        entry_buffer = next_content.index(
            "auto userpicEntries = std::vector<Userpics::value_type>();"
        )
        loop = next_content.index("for (const auto &info : sources)")
        reuse = next_content.index("_userpics.find(peer)", loop)
        current_peer_insert = next_content.index(
            "userpicEntries.emplace_back(peer, userpic);",
            loop,
        )
        cache_replace = next_content.index(
            "_userpics = Userpics(begin(userpicEntries), end(userpicEntries));",
            loop,
        )
        return_result = next_content.index("return result;", loop)

        self.assertLess(entry_buffer, loop)
        self.assertIn("userpicEntries.reserve(sources.size());", next_content)
        self.assertLess(loop, reuse)
        self.assertLess(reuse, current_peer_insert)
        self.assertLess(current_peer_insert, cache_replace)
        self.assertLess(cache_replace, return_result)
        self.assertNotIn("_userpics.emplace(", next_content)
        self.assertNotIn("userpics.emplace(", next_content)


if __name__ == "__main__":
    unittest.main()
