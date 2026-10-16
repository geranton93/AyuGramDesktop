"""Guard shared-media rollback matching and viewer notification."""

import os
import pathlib
import re
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


def compact(source):
    return re.sub(r"\s+", "", source)


class SharedMediaRollbackContract(unittest.TestCase):
    def test_uploaded_media_edit_restores_shared_media_index_before_updates(self):
        source = (SOURCE / "api/api_editing.cpp").read_text()
        edit = function_definition(
            source,
            "void EditMessageWithUploadedMedia(")
        remove = edit.index("item->removeFromSharedMediaIndex();")
        clear = edit.index("item->clearSavedMedia();")
        add = edit.index("item->addToSharedMediaIndex();")
        updates = edit.index("applyUpdates();")
        self.assertLess(remove, clear)
        self.assertLess(clear, add)
        self.assertLess(add, updates)

    def test_sparse_removal_decrements_only_for_matched_rollback(self):
        header = (SOURCE / "storage/storage_sparse_ids_list.h").read_text()
        implementation = (
            SOURCE / "storage/storage_sparse_ids_list.cpp"
        ).read_text()
        self.assertIn(
            "bool removeOne(MsgId messageId, bool onlyMatched = false);",
            header)
        remove = function_definition(
            implementation,
            "bool SparseIdsList::removeOne(MsgId messageId, bool onlyMatched)")
        guard = remove.index("if (onlyMatched && !removed)")
        decrement = remove.index("--*_count;")
        self.assertLess(guard, decrement)
        self.assertIn("return removed;", remove)

    def test_ack_marks_only_same_key_rollback_as_matched_only(self):
        source = (SOURCE / "history/history_item.cpp").read_text()
        ack = function_definition(
            source,
            "void HistoryItem::applySentMessage(const MTPDmessage &data)")
        remove = ack[ack.index("SharedMediaRemoveOne("):]
        self.assertIn("goneTypes,id,!keyMoved", compact(remove))

    def test_storage_tracks_each_bucket_and_passes_rollback_mode(self):
        header = (SOURCE / "storage/storage_shared_media.h").read_text()
        implementation = (
            SOURCE / "storage/storage_shared_media.cpp"
        ).read_text()
        remove_query = header[
            header.index("struct SharedMediaRemoveOne {"):
            header.index("struct SharedMediaRemoveAll {")]
        add_query = header[
            header.index("struct SharedMediaAddNew {"):
            header.index("struct SharedMediaAddExisting {")]
        for field in (
            "bool onlyMatched = false;",
            "matchedPeerWideTypes",
            "matchedTopicTypes",
            "matchedMonoforumTypes",
        ):
            self.assertIn(field, remove_query)
        self.assertNotIn("onlyMatched", add_query)
        remove = function_definition(
            implementation,
            "void SharedMedia::remove(SharedMediaRemoveOne &&query)")
        normalized = compact(remove)
        self.assertIn(
            "removeOne(query.messageId,query.onlyMatched)",
            normalized)
        for field in (
            "query.matchedPeerWideTypes",
            "query.matchedTopicTypes",
            "query.matchedMonoforumTypes",
        ):
            self.assertIn(compact(field), normalized)

    def test_open_viewer_ignores_unmatched_rollback_bucket(self):
        source = (SOURCE / "data/data_shared_media.cpp").read_text()
        viewer = function_definition(
            source,
            "rpl::producer<SparseIdsSlice> SharedMediaViewer(")
        start = viewer.index("rpl::filter([=](const OneRemoved &update)")
        end = viewer.index("}) | rpl::filter", start)
        predicate = compact(viewer[start:end])
        self.assertIn(
            "key.topicRootId?update.matchedTopicTypes:key.monoforumPeerId"
            "?update.matchedMonoforumTypes:update.matchedPeerWideTypes;",
            predicate)
        self.assertIn(
            "&&(!update.onlyMatched||matchedTypes.test(key.type))",
            predicate)


if __name__ == "__main__":
    unittest.main()
