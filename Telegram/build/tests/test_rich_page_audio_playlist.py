import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SOURCE = ROOT / "Telegram" / "SourceFiles"


def function(source, signature, last=False):
    start = source.rfind(signature) if last else source.find(signature)
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


def compile_and_run(source, prefix):
    root = pathlib.Path(os.environ["AYU_TEST_PERSIST_TEMP_ROOT"])
    directory = pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=root))
    cpp = directory / "main.cpp"
    binary = directory / "test"
    cpp.write_text(source)
    compiler = shlex.split(os.environ.get("CXX", "clang++"))
    compiled = subprocess.run(
        [*compiler, "-std=c++20", str(cpp), "-o", str(binary)],
        check=False,
        capture_output=True,
        text=True,
    )
    if compiled.returncode:
        raise AssertionError(compiled.stdout + compiled.stderr)
    executed = subprocess.run(
        [str(binary)],
        check=False,
        capture_output=True,
        text=True,
    )
    if executed.returncode:
        raise AssertionError(executed.stdout + executed.stderr)


class RichPageAudioPlaylist(unittest.TestCase):
    def test_nested_audio_collector_preserves_order_and_deduplicates(self):
        rich_page = (SOURCE / "iv" / "iv_rich_page.cpp").read_text()
        cpp = r'''
#include <algorithm>
#include <vector>
#include <utility>

template <typename T>
class not_null {
public:
    explicit not_null(T value) : _value(value) {}
    [[nodiscard]] T get() const { return _value; }
    [[nodiscard]] T operator->() const { return _value; }
    friend bool operator==(const not_null &, const not_null &) = default;
private:
    T _value;
};
template <typename T> not_null(T) -> not_null<T>;

namespace ranges {
template <typename Range, typename Value>
bool contains(const Range &range, const Value &value) {
    return std::find(range.begin(), range.end(), value) != range.end();
}
}

namespace Iv {
struct DocumentData {
    bool audio = false;
    bool voice = false;
    bool isAudioFile() const { return audio; }
    bool isVoiceMessage() const { return voice; }
};
struct RichPage {
    enum class BlockKind { Audio, File, Paragraph, Quote };
    struct Block;
    struct ListItem { std::vector<Block> blocks; };
    struct Block {
        BlockKind kind = BlockKind::Paragraph;
        DocumentData *document = nullptr;
        std::vector<Block> blocks;
        std::vector<ListItem> listItems;
    };
    std::vector<Block> blocks;
};
using BlockKind = RichPage::BlockKind;
using Block = RichPage::Block;
bool RichDocumentIsAudio(DocumentData *document);
bool RichBlockIsDocumentRow(BlockKind kind);
void CollectBlocksAudio(
    const std::vector<Block> &blocks,
    std::vector<not_null<DocumentData*>> &result);
'''
        cpp += function(
            rich_page,
            "void CollectBlockAudio(",
        )
        cpp += function(
            rich_page,
            "void CollectBlocksAudio(",
            last=True,
        )
        cpp += function(
            rich_page,
            "std::vector<not_null<DocumentData*>> CollectRichPageAudio(",
        )
        cpp += function(
            rich_page,
            "bool RichDocumentIsAudio(",
        )
        cpp += function(
            rich_page,
            "bool RichBlockIsDocumentRow(",
        )
        cpp += r'''
}
int main() {
    auto song = Iv::DocumentData{ .audio = true };
    auto voice = Iv::DocumentData{ .voice = true };
    auto page = Iv::RichPage();

    auto first = Iv::RichPage::Block();
    first.kind = Iv::RichPage::BlockKind::Audio;
    first.document = &song;
    page.blocks.push_back(first);

    auto ignored = Iv::RichPage::Block();
    ignored.kind = Iv::RichPage::BlockKind::Paragraph;
    ignored.document = &voice;
    page.blocks.push_back(ignored);

    auto nested = Iv::RichPage::Block();
    nested.kind = Iv::RichPage::BlockKind::Quote;
    auto second = Iv::RichPage::Block();
    second.kind = Iv::RichPage::BlockKind::File;
    second.document = &voice;
    nested.blocks.push_back(second);
    auto duplicate = Iv::RichPage::Block();
    duplicate.kind = Iv::RichPage::BlockKind::Audio;
    duplicate.document = &song;
    nested.listItems.push_back({ { duplicate } });
    page.blocks.push_back(nested);

    const auto tracks = Iv::CollectRichPageAudio(page);
    return (tracks.size() == 2
        && tracks[0].get() == &song
        && tracks[1].get() == &voice)
        ? 0
        : 1;
}
'''
        compile_and_run(cpp, "ayu-rich-page-audio-collector-")

    def test_player_filters_track_type_and_honors_reverse_order(self):
        player = (SOURCE / "media" / "player" / "media_player_instance.cpp").read_text()
        header = (SOURCE / "media" / "player" / "media_player_instance.h").read_text()
        self.assertIn("currentTracks = item", player)
        self.assertIn("moveInItem(data, delta, autonext)", player)
        self.assertIn("trackInItem(data, -1)", player)
        self.assertIn("trackInItem(data, 1)", player)
        self.assertIn("currentTracks;", header)

        cpp = r'''
#include <algorithm>
#include <vector>
using std::begin;
using std::end;

template <typename T>
class not_null {
public:
    not_null(T value) : _value(value) {}
    [[nodiscard]] T get() const { return _value; }
    [[nodiscard]] T operator->() const { return _value; }
    friend bool operator==(const not_null &, const not_null &) = default;
private:
    T _value;
};
template <typename T> not_null(T) -> not_null<T>;
namespace ranges {
template <typename Range, typename Predicate>
auto find_if(const Range &range, Predicate predicate) {
    return std::find_if(range.begin(), range.end(), predicate);
}
}

struct FullMsgId {
    int value = 0;
    friend bool operator==(FullMsgId, FullMsgId) = default;
};
struct DocumentData;
struct AudioMsgId {
    enum class Type { Song, Voice };
    DocumentData *document = nullptr;
    FullMsgId context;
    AudioMsgId() = default;
    AudioMsgId(DocumentData *value, FullMsgId id)
    : document(value), context(id) {}
    AudioMsgId(not_null<DocumentData*> value, FullMsgId id)
    : document(value.get()), context(id) {}
    explicit operator bool() const { return document != nullptr; }
    DocumentData *audio() const { return document; }
    FullMsgId contextId() const { return context; }
    Type type() const;
};
struct DocumentData {
    AudioMsgId::Type type;
};
AudioMsgId::Type AudioMsgId::type() const { return document->type; }
struct HistoryItem {
    std::vector<not_null<DocumentData*>> audio;
};
std::vector<not_null<DocumentData*>> ItemRichPageAudio(
        not_null<HistoryItem*> item) {
    return item->audio;
}
'''
        cpp += function(
            player,
            "[[nodiscard]] std::vector<not_null<DocumentData*>> ItemPlaylistTracks(",
        )
        cpp += r'''
enum class OrderMode { Forward, Reverse };
struct Data {
    std::vector<not_null<DocumentData*>> currentTracks;
    AudioMsgId current;
    OrderMode mode = OrderMode::Forward;
};
struct Instance {
    [[nodiscard]] OrderMode order(not_null<const Data*> data) const {
        return data->mode;
    }
    [[nodiscard]] AudioMsgId trackInItem(
        not_null<const Data*> data,
        int delta) const {
'''
        cpp += function(
            player,
            "AudioMsgId Instance::trackInItem(",
        ).split("{", 1)[1].rsplit("}", 1)[0]
        cpp += r'''
    }
};
int main() {
    auto first = DocumentData{ AudioMsgId::Type::Song };
    auto voice = DocumentData{ AudioMsgId::Type::Voice };
    auto second = DocumentData{ AudioMsgId::Type::Song };
    auto item = HistoryItem{ { &first, &voice, &second } };
    const auto songs = ItemPlaylistTracks(&item, AudioMsgId::Type::Song);
    const auto voices = ItemPlaylistTracks(&item, AudioMsgId::Type::Voice);
    if (songs.size() != 2 || songs[0] != &first || songs[1] != &second) {
        return 1;
    }
    if (voices.size() != 1 || voices[0] != &voice) return 2;

    auto data = Data();
    data.currentTracks = songs;
    const auto context = FullMsgId{ 42 };
    data.current = AudioMsgId(&first, context);
    auto player = Instance();
    const auto next = player.trackInItem(&data, 1);
    if (next.audio() != &second || next.contextId() != context) return 3;
    if (player.trackInItem(&data, -1)) return 4;

    data.current = AudioMsgId(&second, context);
    data.mode = OrderMode::Reverse;
    const auto previous = player.trackInItem(&data, 1);
    if (previous.audio() != &first || previous.contextId() != context) return 5;
    if (player.trackInItem(&data, -1)) return 6;
    return 0;
}
'''
        compile_and_run(cpp, "ayu-rich-page-audio-playlist-")


if __name__ == "__main__":
    unittest.main()
