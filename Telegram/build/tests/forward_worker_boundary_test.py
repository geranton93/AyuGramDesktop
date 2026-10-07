"""Source-contract regressions for the forwarding worker boundary.

These deliberately check threading/lifetime contracts, not GUI runtime behavior.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3] / "Telegram" / "SourceFiles"
FORWARD = ROOT / "ayu/features/forward"


def function_body(text, name):
    start = text.index("void " + name + "(")
    opening = text.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


class ForwardWorkerBoundaryTest(unittest.TestCase):
    def test_resolved_continuation_runs_on_worker_with_weak_session(self):
        body = function_body((FORWARD / "ayu_forward.cpp").read_text(), "intelligentForward")
        continuation = body[body.index("if (resolvedIds.empty())"):]
        self.assertIn("crl::async(", continuation)
        self.assertNotIn("weakSession.get()", continuation)
        self.assertRegex(continuation, r"ContinueIntelligentForward\(\s*weakSession,")

    def test_context_menu_passes_existing_weak_handles(self):
        source = (ROOT / "ayu/ui/context_menu/context_menu.cpp").read_text()
        for name in ("forwardRichMessage", "forwardMessages", "intelligentForward"):
            calls = re.findall(r"AyuForward::" + name + r"\(\s*([^,]+),", source)
            self.assertTrue(calls, name)
            self.assertTrue(all(arg.strip() == "sourceSession" for arg in calls), name)

    def test_intermediate_chunks_do_not_finish_operation(self):
        body = function_body((FORWARD / "ayu_forward.cpp").read_text(), "ContinueIntelligentForward")
        self.assertNotIn("ForwardState::State::Finished", body)
        self.assertTrue(body.rstrip().endswith("FinishForward(weakSession, state, peerId);\n}"))


if __name__ == "__main__":
    unittest.main()
