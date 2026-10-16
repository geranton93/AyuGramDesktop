"""Guard both late shared-media jump completion paths."""
import os
import pathlib
import re
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


class SharedMediaProviderRequestLifetime(unittest.TestCase):
    def test_done_and_fail_callbacks_are_guarded_by_provider_lifetime(self):
        source = (SOURCE / "info/media/info_media_provider.cpp").read_text()
        jump = function_body(source, "void Provider::jumpToMessage(")
        compact = re.sub(r"\s+", " ", jump)
        self.assertIn(
            ").done(crl::guard(this, [=](const Api::SearchRequestResult &result) {",
            compact,
        )
        self.assertIn(
            "})).fail(crl::guard(this, [=] {",
            compact,
        )
        self.assertEqual(compact.count("crl::guard(this,"), 2)


if __name__ == "__main__":
    unittest.main()
