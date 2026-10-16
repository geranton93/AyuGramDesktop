"""Guard topic-scoped shared media updates and cache retirement."""
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
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


def compact_source(source):
    return re.sub(r"\s+", " ", source)


class SharedMediaTopicLifecycle(unittest.TestCase):
    def test_unpin_noop_does_not_remove_shared_media(self):
        source = (SOURCE / "history/history_item.cpp").read_text()
        set_pinned = function_definition(
            source,
            "void HistoryItem::setIsPinned(bool pinned)")
        self.assertIn(
            "if (!changed || (_flags & MessageFlag::StoryItem)) {",
            set_pinned)

    def test_service_edition_reindexes_after_replacing_content(self):
        source = (SOURCE / "history/history_item.cpp").read_text()
        edition = function_definition(
            source,
            "void HistoryItem::applyEdition(const MTPDmessageService &message)")
        start = edition.index("} else if (isService()) {")
        service_branch = edition[start:]
        remove = service_branch.find("removeFromSharedMediaIndex();")
        self.assertNotEqual(remove, -1)
        replace = service_branch.index("createServiceFromMtp(message);")
        add = service_branch.find("addToSharedMediaIndex();")
        self.assertNotEqual(add, -1)
        finish = service_branch.index("finishEdition(-1);")
        self.assertLess(remove, replace)
        self.assertLess(replace, add)
        self.assertLess(add, finish)

    def test_sent_message_reindexes_only_changed_types_and_keys(self):
        source = (SOURCE / "history/history_item.cpp").read_text()
        sent = function_definition(
            source,
            "void HistoryItem::applySentMessage(const MTPDmessage &data)")
        was_types = sent.index("const auto wasTypes = sharedMediaTypes();")
        was_topic = sent.index("const auto wasTopicRootId = topicRootId();")
        was_sublist = sent.index("const auto wasSublistPeerId = sublistPeerId();")
        update = sent.index("updateSentContent(data);")
        remove = sent.index("if (wasTypes && goneTypes) {")
        add = sent.index("if (addedTypes) {")
        pin = sent.index("setIsPinned(data.is_pinned());")
        self.assertLess(was_types, update)
        self.assertLess(was_topic, update)
        self.assertLess(was_sublist, update)
        self.assertGreater(remove, update)
        self.assertLess(remove, add)
        self.assertLess(add, pin)
        self.assertNotIn("removeFromSharedMediaIndex();", sent)

    def test_chat_photo_topic_search_uses_peer_wide_key(self):
        shared = (SOURCE / "data/data_shared_media.cpp").read_text()
        self.assertIn(
            "bool SharedMediaThreadFilterSupported(",
            shared)
        supported = function_definition(
            shared,
            "bool SharedMediaThreadFilterSupported(")
        loadable = function_definition(
            shared,
            "Storage::SharedMediaKey SharedMediaLoadableKey(")
        viewer = function_definition(
            shared,
            "rpl::producer<SparseIdsSlice> SharedMediaViewer(")
        self.assertIn(
            "key = SharedMediaLoadableKey(\n"
            "\t\tsession->data().peer(key.peerId),",
            viewer)

        search = (SOURCE / "data/data_search_controller.cpp").read_text()
        request = function_definition(
            search,
            "std::optional<SearchRequest> PrepareSearchRequest(")
        self.assertIn(
            "!SharedMediaThreadFilterSupported(peer, topicRootId, type)",
            request)

        provider = (SOURCE / "info/media/info_media_provider.cpp").read_text()
        jump = function_definition(provider, "void Provider::jumpToMessage(")
        self.assertIn("SharedMediaLoadableKey(peer,", jump)
        self.assertIn("key.topicRootId", jump)
        self.assertIn("key.type", jump)

        cpp = r'''#include <cstdint>
namespace Storage {
enum class SharedMediaType {
    ChatPhoto,
    RoundVoiceFile,
    RoundFile,
    VoiceFile,
};
struct SharedMediaKey {
    int peerId = 0;
    int topicRootId = 0;
    int monoforumPeerId = 0;
    SharedMediaType type = SharedMediaType::RoundFile;
    int messageId = 0;
};
}
using MsgId = int;
namespace { using Type = Storage::SharedMediaType; }
struct PeerData {
    explicit PeerData(bool channel) : channel(channel) {}
    bool isChannel() const { return channel; }
    bool channel = false;
};
template <typename T>
class not_null {
public:
    explicit not_null(T pointer) : pointer(pointer) {}
    T operator->() const { return pointer; }
private:
    T pointer;
};
''' + supported + loadable + r'''
int main() {
    auto channel = PeerData(true);
    auto user = PeerData(false);
    const auto channelPointer = not_null<PeerData*>(&channel);
    const auto userPointer = not_null<PeerData*>(&user);

    if (!SharedMediaThreadFilterSupported(
                channelPointer, 42, Type::RoundVoiceFile)
        || SharedMediaThreadFilterSupported(
                channelPointer, 42, Type::ChatPhoto)
        || !SharedMediaThreadFilterSupported(
                channelPointer, 42, Type::VoiceFile)
        || !SharedMediaThreadFilterSupported(
                channelPointer, 42, Type::RoundFile)
        || !SharedMediaThreadFilterSupported(
            userPointer, 42, Type::RoundFile)
        || !SharedMediaThreadFilterSupported(
            channelPointer, 0, Type::RoundFile)) {
        return 1;
    }

    auto photo = Storage::SharedMediaKey();
    photo.peerId = 1;
    photo.topicRootId = 42;
    photo.type = Storage::SharedMediaType::ChatPhoto;
    auto normalizedPhoto = SharedMediaLoadableKey(channelPointer, photo);
    if (normalizedPhoto.topicRootId != 0
        || normalizedPhoto.peerId != 1
        || normalizedPhoto.type != Storage::SharedMediaType::ChatPhoto) {
        return 1;
    }

    auto roundVoice = Storage::SharedMediaKey();
    roundVoice.topicRootId = 42;
    roundVoice.type = Storage::SharedMediaType::RoundVoiceFile;
    const auto normalizedVoice = SharedMediaLoadableKey(
        channelPointer, roundVoice);
    if (normalizedVoice.topicRootId != 42
        || normalizedVoice.type != Storage::SharedMediaType::RoundVoiceFile) {
        return 2;
    }

    auto roundVideo = Storage::SharedMediaKey();
    roundVideo.topicRootId = 42;
    roundVideo.type = Storage::SharedMediaType::RoundFile;
    const auto normalizedVideo = SharedMediaLoadableKey(
        channelPointer, roundVideo);
    return (normalizedVideo.topicRootId == 42
        && normalizedVideo.type == Storage::SharedMediaType::RoundFile)
        ? 0
        : 3;
}
'''
        with tempfile.TemporaryDirectory(prefix="ayu-topic-chat-photo-") as directory:
            directory = pathlib.Path(directory)
            test_source = directory / "test.cpp"
            binary = directory / "test"
            test_source.write_text(cpp)
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run(compiler + [
                "-std=c++20", "-O0", "-Wall", "-Wextra", "-pedantic",
                str(test_source), "-o", str(binary),
            ], check=True, timeout=60)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_round_video_topic_query_keeps_round_video_filter_and_topic(self):
        search = (SOURCE / "data/data_search_controller.cpp").read_text()
        prepare_filter = compact_source(function_definition(
            search,
            "MTPMessagesFilter PrepareSearchFilter("))
        self.assertIn(
            "case Type::RoundFile: return MTP_inputMessagesFilterRoundVideo();",
            prepare_filter)
        self.assertIn(
            "case Type::RoundVoiceFile: return MTP_inputMessagesFilterRoundVoice();",
            prepare_filter)
        request = compact_source(function_definition(
            search,
            "std::optional<SearchRequest> PrepareSearchRequest("))
        self.assertIn(
            "MTP_flags((topicRootId ? Flag::f_top_msg_id : Flag(0))",
            request)
        self.assertIn("MTP_int(topicRootId)", request)
        self.assertIn("MTP_int(topicRootId), filter, MTP_int(0)", request)
        self.assertIn(
            "!SharedMediaThreadFilterSupported(peer, topicRootId, type)",
            request)

        parse = compact_source(function_definition(
            search,
            "SearchResult ParseSearchResult("))
        self.assertIn("item->sharedMediaTypes().test(type)", parse)

        media_types = (SOURCE / "data/data_media_types.cpp").read_text()
        classification = compact_source(function_definition(
            media_types,
            "Storage::SharedMediaTypesMask MediaFile::sharedMediaTypes() const"))
        self.assertIn("_document->isVideoMessage()", classification)
        self.assertIn(".added(Type::RoundFile)", classification)
        self.assertIn(".added(Type::RoundVoiceFile)", classification)

        shared = (SOURCE / "data/data_shared_media.cpp").read_text()
        supported = compact_source(function_definition(
            shared,
            "bool SharedMediaThreadFilterSupported("))
        self.assertNotIn("Type::RoundVoiceFile", supported)
        self.assertIn("case Type::ChatPhoto: return false;", supported)
        loadable = compact_source(function_definition(
            shared,
            "Storage::SharedMediaKey SharedMediaLoadableKey("))
        self.assertNotIn("key.type = Type::VoiceFile;", loadable)
        self.assertIn("key.topicRootId = MsgId();", loadable)
        viewer = compact_source(function_definition(
            shared,
            "rpl::producer<SparseIdsSlice> SharedMediaViewer("))
        self.assertIn("topicRootId = key.topicRootId", viewer)
        self.assertIn("monoforumPeerId = key.monoforumPeerId", viewer)
        self.assertIn("type = key.type", viewer)
        self.assertIn(
            "requestSharedMedia( peer, topicRootId, monoforumPeerId, type,",
            viewer)
        overview = compact_source(function_definition(
            shared,
            "std::optional<Storage::SharedMediaType> SharedMediaOverviewType("))
        self.assertIn("case Type::RoundVoiceFile:", overview)

        provider = (SOURCE / "info/media/info_media_provider.cpp").read_text()
        jump = compact_source(function_definition(
            provider,
            "void Provider::jumpToMessage("))
        self.assertIn("key.topicRootId,", jump)
        self.assertIn("key.type,", jump)
        self.assertIn("key.topicRootId, key.monoforumPeerId, key.type,", jump)
        self.assertIn("key.type, messageId, Data::LoadDirection::Around", jump)
        self.assertIn(
            "key.topicRootId, key.monoforumPeerId, key.type, std::move(parsed.messageIds)",
            jump)

        apiwrap = (SOURCE / "apiwrap.cpp").read_text()
        api_request = compact_source(function_definition(
            apiwrap,
            "void ApiWrap::requestSharedMedia("))
        self.assertIn(
            "SharedMediaRequest{ peer, topicRootId, monoforumPeerId, type, messageId, slice,",
            api_request)
        self.assertIn(
            "Api::PrepareSearchRequest( peer, topicRootId, monoforumPeerId, type,",
            api_request)
        self.assertIn(
            "Api::ParseSearchResult( peer, type, messageId, slice, result)",
            api_request)
        self.assertIn(
            "sharedMediaDone( peer, topicRootId, monoforumPeerId, type, std::move(parsed))",
            api_request)

        persist = compact_source(function_definition(
            apiwrap,
            "void ApiWrap::sharedMediaDone("))
        self.assertIn(
            "Storage::SharedMediaAddSlice( peer->id, topicRootId, monoforumPeerId, type,",
            persist)

    def test_sent_message_updates_ayu_no_forwards_flag(self):
        source = (SOURCE / "history/history_item.cpp").read_text()
        sent = function_definition(
            source,
            "void HistoryItem::applySentMessage(const MTPDmessage &data)")
        self.assertIn("if (data.is_noforwards())", sent)
        start = sent.index("if (data.is_noforwards())")
        opening = sent.index("{", start)
        depth = 1
        cursor = opening + 1
        while depth:
            if sent[cursor] == "{":
                depth += 1
            elif sent[cursor] == "}":
                depth -= 1
            cursor += 1
        block = sent[start:cursor]
        else_start = sent.index("else {", cursor)
        else_opening = sent.index("{", else_start)
        depth = 1
        else_cursor = else_opening + 1
        while depth:
            if sent[else_cursor] == "{":
                depth += 1
            elif sent[else_cursor] == "}":
                depth -= 1
            else_cursor += 1
        block += " " + sent[else_start:else_cursor]

        cpp = r'''#include <cstdint>
using MessageFlags = std::uint32_t;
enum class MessageFlag : MessageFlags { AyuNoForwards = 4 };
constexpr MessageFlags operator~(MessageFlag flag) {
    return ~static_cast<MessageFlags>(flag);
}
MessageFlags &operator|=(MessageFlags &flags, MessageFlag flag) {
    flags |= static_cast<MessageFlags>(flag);
    return flags;
}
struct MessageData {
    bool noforwards = false;
    bool is_noforwards() const { return noforwards; }
};
void update(MessageFlags &_flags, const MessageData &data) {
''' + block + r'''
}
int main() {
    auto flags = MessageFlags(0);
    update(flags, MessageData{true});
    if (!(flags & static_cast<MessageFlags>(MessageFlag::AyuNoForwards))) {
        return 1;
    }
    update(flags, MessageData{false});
    if (flags & static_cast<MessageFlags>(MessageFlag::AyuNoForwards)) {
        return 2;
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="ayu-sent-noforwards-") as directory:
            directory = pathlib.Path(directory)
            test_source = directory / "test.cpp"
            binary = directory / "test"
            test_source.write_text(cpp)
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run(compiler + [
                "-std=c++20", "-O0", "-Wall", "-Wextra", "-pedantic",
                str(test_source), "-o", str(binary),
            ], check=True, timeout=60)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_one_removed_matches_the_viewer_scope(self):
        source = (SOURCE / "data/data_shared_media.cpp").read_text()
        viewer = function_definition(source, "rpl::producer<SparseIdsSlice> SharedMediaViewer(")
        start = viewer.index("rpl::filter([=](const OneRemoved &update)")
        end = viewer.index("}) | rpl::filter", start)
        predicate = viewer[start:end]
        expression = re.sub(
            r"\s+",
            "",
            predicate.split("return", 1)[1].split(";", 1)[0])
        expected = re.sub(
            r"\s+",
            "",
            """(update.peerId == key.peerId)
                && (!key.topicRootId || key.topicRootId == update.topicRootId)
                && (!key.monoforumPeerId
                    || key.monoforumPeerId == update.monoforumPeerId)
                && update.types.test(key.type)
                && (!update.onlyMatched || matchedTypes.test(key.type))""")
        self.assertEqual(expression, expected)

        def matches(key_topic, key_monoforum, update_topic, update_monoforum):
            return (
                (not key_topic or key_topic == update_topic)
                and (not key_monoforum or key_monoforum == update_monoforum)
            )

        self.assertTrue(matches(0, 0, 11, 0))
        self.assertFalse(matches(22, 0, 11, 0))
        self.assertFalse(matches(0, 55, 0, 44))
        self.assertTrue(matches(22, 0, 22, 0))

    def test_remove_event_follows_both_index_updates(self):
        source = (SOURCE / "storage/storage_shared_media.cpp").read_text()
        remove = function_definition(
            source,
            "void SharedMedia::remove(SharedMediaRemoveOne &&query)")
        self.assertIn(
            "removeByIt(peerIt, query.matchedPeerWideTypes);",
            remove)
        self.assertIn("if (topicIt != end(_lists))", remove)
        self.assertIn(
            "removeByIt(topicIt, query.matchedTopicTypes);",
            remove)
        self.assertIn(
            "removeByIt(monoforumPeerIt, query.matchedMonoforumTypes);",
            remove)
        self.assertIn("_oneRemoved.fire(std::move(query));", remove)
        peer_remove = remove.index(
            "removeByIt(peerIt, query.matchedPeerWideTypes);")
        topic_remove = remove.index(
            "removeByIt(topicIt, query.matchedTopicTypes);")
        monoforum_remove = remove.index(
            "removeByIt(monoforumPeerIt, query.matchedMonoforumTypes);")
        publish = remove.index("_oneRemoved.fire(std::move(query));")
        self.assertLess(peer_remove, topic_remove)
        self.assertLess(topic_remove, monoforum_remove)
        self.assertLess(monoforum_remove, publish)

    def test_forum_teardown_unloads_orphaned_topic_keys(self):
        source = (SOURCE / "data/data_forum.cpp").read_text()
        destructor = function_definition(source, "Forum::~Forum()")
        per_topic = destructor.index("SharedMediaUnloadThread(")
        per_topic_guard = destructor.rindex("if (rootId)", 0, per_topic)
        self.assertIn(
            "storage.unload(Storage::SharedMediaUnloadAllTopics(peerId));",
            destructor)
        self.assertLess(per_topic_guard, per_topic)

    def test_topic_delete_unloads_even_when_topic_object_is_missing(self):
        source = (SOURCE / "data/data_forum.cpp").read_text()
        deleted = function_definition(
            source,
            "void Forum::applyTopicDeleted(MsgId rootId)")
        missing = deleted.index("if (i == end(_topics))")
        cleanup = deleted.index("SharedMediaUnloadThread(", missing)
        root_guard = deleted.index("if (rootId)", missing)
        early_return = deleted.index("return;", missing)
        self.assertLess(root_guard, cleanup)
        self.assertLess(cleanup, early_return)

    def test_existing_topic_delete_guards_the_peer_wide_root(self):
        source = (SOURCE / "data/data_forum.cpp").read_text()
        deleted = function_definition(
            source,
            "void Forum::applyTopicDeleted(MsgId rootId)")
        destroy_messages = deleted.index("_history->destroyMessagesByTopic(rootId);")
        root_guard = deleted.index("if (rootId)", destroy_messages)
        cleanup = deleted.index("SharedMediaUnloadThread(", destroy_messages)
        self.assertLess(destroy_messages, root_guard)
        self.assertLess(root_guard, cleanup)

    def test_unload_all_topics_preserves_peer_wide_index(self):
        source = (SOURCE / "storage/storage_shared_media.cpp").read_text()
        unload = function_definition(
            source,
            "void SharedMedia::unload(SharedMediaUnloadAllTopics &&query)")
        self.assertIn("peerIt->first.peerId == query.peerId", unload)
        guard = unload.index("if (peerIt->first.topicRootId)")
        erase = unload.index("_lists.erase(peerIt)", guard)
        otherwise = unload.index("} else {", guard)
        preserve = unload.index("++peerIt;", otherwise)
        self.assertLess(guard, erase)
        self.assertLess(erase, otherwise)
        self.assertLess(otherwise, preserve)

    def test_pin_transitions_increment_known_existing_counts_once(self):
        history = (SOURCE / "history/history_item.cpp").read_text()
        pin = function_definition(history, "void HistoryItem::setIsPinned(")
        self.assertEqual(pin.count("changed));"), 3)

        storage = (SOURCE / "storage/storage_shared_media.cpp").read_text()
        add = function_definition(
            storage,
            "void SharedMedia::add(SharedMediaAddExisting &&query)")
        self.assertIn("query.incrementCount", add)

        sparse = (SOURCE / "storage/storage_sparse_ids_list.cpp").read_text()
        add_existing = function_definition(
            sparse,
            "void SparseIdsList::addExisting(")
        add_range = function_definition(sparse, "void SparseIdsList::addRange(")
        self.assertIn("std::nullopt, incrementCount", add_existing)
        self.assertIn("incrementCount && _count && result.added > 0", add_range)

    def test_sent_message_removes_only_lost_types_or_moved_keys(self):
        history = (SOURCE / "history/history_item.cpp").read_text()
        apply_sent = function_definition(
            history,
            "void HistoryItem::applySentMessage(const MTPDmessage &data)")
        self.assertIn("const auto wasTypes = sharedMediaTypes();", apply_sent)
        self.assertIn("const auto keyMoved =", apply_sent)
        self.assertIn(
            "wasTypes.test(type) && (keyMoved || !nowTypes.test(type))",
            apply_sent)
        self.assertIn(
            "nowTypes.test(type) && (keyMoved || !wasTypes.test(type))",
            apply_sent)
        self.assertIn("if (addedTypes)", apply_sent)
        self.assertNotIn("addToSharedMediaIndex();", apply_sent)
        self.assertNotIn("removeFromSharedMediaIndex();", apply_sent)

        storage = (SOURCE / "storage/storage_shared_media.cpp").read_text()
        remove = function_definition(
            storage,
            "void SharedMedia::remove(SharedMediaRemoveOne &&query)")
        self.assertIn(
            "removeOne(query.messageId,query.onlyMatched)",
            re.sub(r"\s+", "", remove))

        sparse = (SOURCE / "storage/storage_sparse_ids_list.cpp").read_text()
        add_new = function_definition(
            sparse,
            "void SparseIdsList::addNew(MsgId messageId)")
        add_existing = function_definition(
            sparse,
            "void SparseIdsList::addExisting(")
        remove_one = function_definition(
            sparse,
            "bool SparseIdsList::removeOne(MsgId messageId, bool onlyMatched)")

        cpp = r'''#include <algorithm>
#include <functional>
#include <optional>
#include <set>
#include <vector>
using MsgId = int;
constexpr MsgId ServerMaxMsgId = 1000000;
struct MsgRange {
    MsgId from = 0;
    MsgId till = 0;
};
struct Messages : std::set<MsgId> {
    bool remove(MsgId messageId) {
        return erase(messageId) != 0;
    }
};
struct Slice {
    MsgRange range;
    Messages messages;
};
struct SliceSet : std::vector<Slice> {
    template <typename Callback>
    void modify(iterator slice, Callback callback) {
        callback(*slice);
    }
};
namespace ranges {
template <typename Collection, typename Value, typename Compare, typename Projection>
auto lower_bound(
        Collection &collection,
        const Value &value,
        Compare compare,
        Projection projection) {
    return std::lower_bound(
        collection.begin(),
        collection.end(),
        value,
        [&](const auto &item, const Value &target) {
            return compare(projection(item), target);
        });
}
}
class SparseIdsList {
public:
    void addExisting(MsgId messageId, MsgRange noSkipRange, bool incrementCount);
    bool removeOne(MsgId messageId, bool onlyMatched = false);
    void addNew(MsgId messageId);
    template <typename Range>
    void addRange(
            const Range &messageIds,
            MsgRange,
            std::optional<int>,
            bool incrementCount = false) {
        auto added = 0;
        for (const auto messageId : messageIds) {
            added += _indexed.insert(messageId).second;
        }
        if (incrementCount && _count && added > 0) {
            *_count += added;
        }
    }
    SliceSet _slices;
    std::optional<int> _count;
private:
    std::set<MsgId> _indexed;
};
''' + add_new + "\n" + add_existing + "\n" + remove_one + r'''
int main() {
    auto pinned = SparseIdsList();
    pinned._count = 10;
    pinned.addExisting(42, { 42, 42 }, true);
    if (*pinned._count != 11) return 1;
    pinned.addExisting(42, { 42, 42 }, true);
    if (*pinned._count != 11) return 2;

    auto sparse = SparseIdsList();
    sparse._count = 12;
    auto slice = Slice();
    slice.range = { 100, 200 };
    slice.messages.insert(110);
    slice.messages.insert(120);
    sparse._slices.push_back(std::move(slice));
    sparse.removeOne(105);
    if (*sparse._count != 11) return 3;
    sparse.removeOne(110);
    if (*sparse._count != 10) return 4;

    auto rollback = SparseIdsList();
    rollback._count = 12;
    auto rollbackSlice = Slice();
    rollbackSlice.range = { 100, 200 };
    rollbackSlice.messages.insert(110);
    rollback._slices.push_back(std::move(rollbackSlice));
    rollback.removeOne(105, true);
    if (*rollback._count != 12) return 7;
    rollback.removeOne(110, true);
    if (*rollback._count != 11) return 8;

    auto retainedType = SparseIdsList();
    retainedType._count = 12;
    auto retainedSlice = Slice();
    retainedSlice.range = { 100, 200 };
    retainedSlice.messages.insert(110);
    retainedSlice.messages.insert(120);
    retainedType._slices.push_back(std::move(retainedSlice));
    const auto wasType = true;
    const auto nowType = true;
    const auto keyMoved = false;
    if (nowType && (keyMoved || !wasType)) {
        retainedType.addNew(105);
    }
    if (*retainedType._count != 12) return 5;

    auto gainedType = SparseIdsList();
    gainedType._count = 12;
    const auto hadType = false;
    if (nowType && (keyMoved || !hadType)) {
        gainedType.addNew(105);
    }
    if (*gainedType._count != 13) return 6;
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="ayu-sparse-media-count-") as directory:
            directory = pathlib.Path(directory)
            test_source = directory / "test.cpp"
            binary = directory / "test"
            test_source.write_text(cpp)
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run(compiler + [
                "-std=c++20", "-O0", "-Wall", "-Wextra", "-pedantic",
                str(test_source), "-o", str(binary),
            ], check=True, timeout=60)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
