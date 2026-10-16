from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]


def read_source(path):
    return (ROOT / path).read_text()


def function_body(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for position in range(opening, len(source)):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:position]
    raise AssertionError(f"Unclosed function body: {signature}")


class PinnedMessageRecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api_header = read_source("Telegram/SourceFiles/apiwrap.h")
        cls.api_source = read_source("Telegram/SourceFiles/apiwrap.cpp")
        cls.updates_source = read_source(
            "Telegram/SourceFiles/api/api_updates.cpp"
        )
        cls.peer_header = read_source("Telegram/SourceFiles/data/data_peer.h")
        cls.peer_source = read_source("Telegram/SourceFiles/data/data_peer.cpp")
        cls.history_source = read_source("Telegram/SourceFiles/history/history.cpp")

    def test_request_helper_reports_when_a_known_sparse_list_can_recover_pin(self):
        self.assertIn(
            "bool requestPinnedMessagesIfNeeded(",
            self.api_header,
        )
        self.assertIn("MsgId topicRootId = 0", self.api_header)
        self.assertIn("PeerId monoforumPeerId = 0", self.api_header)

    def test_request_is_bounded_to_known_missing_server_pins(self):
        body = function_body(
            self.api_source,
            "bool ApiWrap::requestPinnedMessagesIfNeeded(",
        )
        self.assertIn(
            "if (!IsServerMsgId(messageId) || !peer->isLoaded())",
            body,
        )
        self.assertIn("snapshot.messageIds.contains(messageId)", body)
        self.assertIn("snapshot.count", body)
        self.assertIn("SharedMediaType::Pinned", body)
        self.assertIn("SliceType::Around", body)
        self.assertIn("requestOne(MsgId(0), PeerId(0))", body)
        self.assertIn("peer->forum()", body)
        self.assertIn("forum->topicDeleted(topicRootId)", body)
        self.assertIn("peer->monoforum()", body)
        self.assertIn("peer->monoforumSublistFor(monoforumPeerId)", body)

    def test_recovery_reuses_shared_media_request_deduplication(self):
        body = function_body(
            self.api_source,
            "void ApiWrap::requestSharedMedia(",
        )
        self.assertIn("if (_sharedMediaRequests.contains(key))", body)
        self.assertIn("_sharedMediaRequests.emplace(key)", body)

    def test_saved_messages_sublist_resolves_for_player_panel(self):
        body = function_body(
            self.peer_source,
            "Data::SavedSublist *PeerData::monoforumSublistFor(",
        )
        self.assertIn("else if (isSelf())", body)
        self.assertIn("owner().savedMessages().sublistLoaded(", body)

        panel = read_source(
            "Telegram/SourceFiles/media/player/media_player_panel.cpp")
        sublist = function_body(panel, "Data::SavedSublist *Panel::listSublist(")
        self.assertIn(
            "_listPeer->monoforumSublistFor(_listSublistPeerId)",
            sublist,
        )
        self.assertNotIn("->monoforum()", sublist)

    def test_missing_pin_keeps_authoritative_id_and_falls_back_to_exact_message(self):
        self.assertIn("void ApplyPinnedMessageId(", self.peer_header)
        body = function_body(
            self.peer_source,
            "void ApplyPinnedMessageId(",
        )
        self.assertIn("peer->owner().message(peer->id, messageId)", body)
        self.assertIn("requestPinnedMessagesIfNeeded(", body)
        self.assertLess(
            body.index("requestPinnedMessagesIfNeeded("),
            body.index("SharedMediaAddSlice("),
        )
        self.assertIn("SharedMediaAddSlice(", body)
        self.assertIn("{ messageId }", body)
        self.assertIn("setHasPinnedMessages(true)", body)
        self.assertIn("item->setIsPinned(true)", body)
        self.assertIn("!peer->isLoaded()", body)
        self.assertIn("requestMessageData(peer, messageId, {})", body)

    def test_top_pin_uses_shared_loaded_or_missing_path(self):
        body = function_body(
            self.peer_source,
            "void SetTopPinnedMessageId(",
        )
        self.assertIn("ApplyPinnedMessageId(", body)
        self.assertIn("setHasPinnedMessages(true)", function_body(
            self.peer_source,
            "void ApplyPinnedMessageId(",
        ))

    def test_both_pin_update_kinds_recover_only_missing_pins(self):
        channel_start = self.updates_source.index(
            "case mtpc_updatePinnedChannelMessages:"
        )
        channel_end = self.updates_source.index(
            "case mtpc_updateEditMessage:",
            channel_start,
        )
        channel = self.updates_source[channel_start:channel_end]

        peer_start = self.updates_source.index(
            "case mtpc_updatePinnedMessages:"
        )
        peer_end = self.updates_source.index(
            "default: Unexpected(\"Type in applyUpdateNoPtsCheck()\")",
            peer_start,
        )
        peer = self.updates_source[peer_start:peer_end]

        for update in (channel, peer):
            self.assertIn("if (d.is_pinned())", update)
            self.assertIn("ApplyPinnedMessageId(", update)
            self.assertIn("session().data().peer(peerId)", update)
            self.assertIn("item->setIsPinned(false)", update)

    def test_service_pin_recovers_missing_item_and_keeps_visible_state(self):
        body = function_body(
            self.history_source,
            "void History::applyServiceChanges(",
        )
        self.assertIn("Data::ApplyPinnedMessageId(", body)
        self.assertIn("peer->forum()", body)
        self.assertIn("item->topicRootId()", body)
        self.assertNotIn("topic ? topic->rootId()", body)

    def test_recovery_sets_peer_and_loaded_scope_flags(self):
        body = function_body(
            self.peer_source,
            "void ApplyPinnedMessageId(",
        )
        self.assertIn("setHasPinnedMessages(true)", body)
        self.assertIn("topic->setHasPinnedMessages(true)", body)
        self.assertIn("sublist->setHasPinnedMessages(true)", body)
        self.assertIn("addPinnedId(topicRootId, PeerId(0))", body)
        self.assertIn("forum->topicDeleted(topicRootId)", body)
        self.assertIn("forum->topicFor(topicRootId)", body)
        self.assertNotIn("enforceTopicFor(", body)

    def test_scoped_search_accepts_forum_key_before_topic_model_is_loaded(self):
        body = function_body(
            self.api_source,
            "void ApiWrap::sharedMediaDone(",
        )
        self.assertIn("forum->topicDeleted(topicRootId)", body)
        self.assertIn("monoforumPeerId && !sublist", body)
        self.assertNotIn("topicRootId && !topic", body)


if __name__ == "__main__":
    unittest.main()
