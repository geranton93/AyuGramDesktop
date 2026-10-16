"""Ensure read messages do not autoplay emoji interactions again."""
import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "SourceFiles"


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


def compile_and_run(cpp):
    root = os.environ.get("AYU_TEST_PERSIST_TEMP_ROOT")
    if root:
        directory = pathlib.Path(tempfile.mkdtemp(
            prefix="ayu-emoji-autoplay-",
            dir=root,
        ))
        _compile_and_run_in(cpp, directory)
    else:
        with tempfile.TemporaryDirectory(prefix="ayu-emoji-autoplay-") as directory:
            _compile_and_run_in(cpp, pathlib.Path(directory))


def _compile_and_run_in(cpp, directory):
    test_source = directory / "test.cpp"
    binary = directory / "test"
    test_source.write_text(cpp)
    compiler = shlex.split(os.environ.get("CXX", "c++"))
    subprocess.run(compiler + [
        "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-pedantic",
        str(test_source), "-o", str(binary),
    ], check=True, timeout=60)
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise AssertionError(
            f"exit {result.returncode}\n{result.stdout}{result.stderr}",
        )


class EmojiInteractionReadAutoplay(unittest.TestCase):
    def test_watched_flag_is_idempotent(self):
        source = (SOURCE / "history/history_item.cpp").read_text()
        method = function_definition(
            source,
            "bool HistoryItem::markEmojiInteractionWatched()",
        )
        cpp = r'''#include <cstdint>
enum class MessageFlag : std::uint64_t {
    EmojiInteractionWatched = (1ULL << 28),
};
struct Flags {
    bool operator&(MessageFlag flag) const {
        return (value & static_cast<std::uint64_t>(flag)) != 0;
    }
    Flags &operator|=(MessageFlag flag) {
        value |= static_cast<std::uint64_t>(flag);
        return *this;
    }
    std::uint64_t value = 0;
};
struct HistoryItem {
    bool markEmojiInteractionWatched();
    Flags _flags;
};
''' + method + r'''
int main() {
    auto item = HistoryItem();
    if (!item.markEmojiInteractionWatched()) {
        return 1;
    }
    if (item.markEmojiInteractionWatched()) {
        return 2;
    }
    return item._flags.value
        & static_cast<std::uint64_t>(MessageFlag::EmojiInteractionWatched)
        ? 0
        : 3;
}
'''
        compile_and_run(cpp)

    def test_read_state_blocks_autoplay_before_media_view_creation(self):
        history_item = (SOURCE / "history/history_item.cpp").read_text()
        initial = function_definition(
            history_item,
            "void HistoryItem::applyInitialEffectWatched()",
        )
        unread_known = function_definition(
            history_item,
            "void HistoryItem::applyEffectWatchedOnUnreadKnown()",
        )
        autoplay = function_definition(
            (SOURCE / "chat_helpers/emoji_interactions.cpp").read_text(),
            "void EmojiInteractions::startAutoplay(",
        )
        mark = function_definition(
            history_item,
            "bool HistoryItem::markEmojiInteractionWatched()",
        )
        gate = autoplay.index(
            "list.empty() || !item->markEmojiInteractionWatched()",
        )
        create_media_view = autoplay.index("document->createMediaView()")
        self.assertLess(gate, create_media_view)
        cpp = r'''#include <cstdint>
enum class MessageFlag : std::uint64_t {
    EmojiInteractionWatched = (1ULL << 28),
    EffectWatched = (1ULL << 29),
};
struct Flags {
    bool operator&(MessageFlag flag) const {
        return (value & static_cast<std::uint64_t>(flag)) != 0;
    }
    Flags &operator|=(MessageFlag flag) {
        value |= static_cast<std::uint64_t>(flag);
        return *this;
    }
    std::uint64_t value = 0;
};
struct History {
    int readTill = 0;
    int inboxReadTillId() const { return readTill; }
};
struct HistoryItem {
    History *_history = nullptr;
    bool _out = false;
    bool _unread = true;
    bool _effect = false;
    Flags _flags;
    bool out() const { return _out; }
    bool unread(History*) const { return _unread; }
    bool effectId() const { return _effect; }
    bool has(MessageFlag flag) const { return _flags & flag; }
    bool markEmojiInteractionWatched();
    void applyInitialEffectWatched();
    void applyEffectWatchedOnUnreadKnown();
};
''' + mark + initial + unread_known + r'''
int main() {
    auto knownRead = HistoryItem();
    auto knownReadHistory = History{ .readTill = 5 };
    knownRead._history = &knownReadHistory;
    knownRead._unread = false;
    knownRead.applyInitialEffectWatched();
    if (!knownRead.has(MessageFlag::EmojiInteractionWatched)
        || knownRead.has(MessageFlag::EffectWatched)) {
        return 1;
    }

    auto notRead = HistoryItem();
    auto notReadHistory = History{ .readTill = 5 };
    notRead._history = &notReadHistory;
    notRead._unread = true;
    notRead._effect = true;
    notRead.applyInitialEffectWatched();
    if (notRead.has(MessageFlag::EmojiInteractionWatched)
        || notRead.has(MessageFlag::EffectWatched)) {
        return 2;
    }

    auto outgoing = HistoryItem();
    auto outgoingHistory = History();
    outgoing._history = &outgoingHistory;
    outgoing._out = true;
    outgoing.applyInitialEffectWatched();
    if (!outgoing.has(MessageFlag::EmojiInteractionWatched)) {
        return 3;
    }

    auto resolvedRead = HistoryItem();
    auto resolvedReadHistory = History();
    resolvedRead._history = &resolvedReadHistory;
    resolvedRead._unread = false;
    resolvedRead._effect = true;
    resolvedRead.applyEffectWatchedOnUnreadKnown();
    if (!resolvedRead.has(MessageFlag::EmojiInteractionWatched)
        || !resolvedRead.has(MessageFlag::EffectWatched)) {
        return 4;
    }

    auto resolvedUnread = HistoryItem();
    auto resolvedUnreadHistory = History();
    resolvedUnread._history = &resolvedUnreadHistory;
    resolvedUnread._unread = true;
    resolvedUnread._effect = true;
    resolvedUnread.applyEffectWatchedOnUnreadKnown();
    return (!resolvedUnread.has(MessageFlag::EmojiInteractionWatched)
        && !resolvedUnread.has(MessageFlag::EffectWatched)) ? 0 : 5;
}
'''
        compile_and_run(cpp)


if __name__ == "__main__":
    unittest.main()
