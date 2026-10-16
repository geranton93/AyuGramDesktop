import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SOURCE = ROOT / "Telegram/SourceFiles"


def block_body(source, signature):
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


class AnimatedVideoCompositionPrefetch(unittest.TestCase):
    def test_unique_video_sources_are_loaded_before_ui_composition(self):
        send = block_body(
            (SOURCE / "boxes/send_files_box.cpp").read_text(),
            "void SendFilesBox::send(",
        )
        animated = block_body(send, "if (animated) {")
        worker = block_body(animated, "crl::async(")
        main = block_body(worker, "crl::on_main(")
        completion = block_body(animated, "const auto done = crl::guard(")

        self.assertLess(
            send.index("ReadAnimatedSourceContents("),
            send.index("Storage::ApplyModifications(_list, animated)"),
        )
        self.assertIn("ReadAnimatedSourceContents(", worker)
        self.assertIn("done(std::move(contents));", main)
        self.assertIn("entry.source->content = std::move(entry.content);",
                      completion)
        self.assertIn("entry.source->contentResolved = true;", completion)
        self.assertIn("send(options, ctrlShiftEnter);", completion)

    def test_shared_video_source_is_deduplicated_and_reads_are_bounded(self):
        send = (SOURCE / "boxes/send_files_box.cpp").read_text()
        sources = block_body(send, "AnimatedSources(")
        reader = block_body(send, "ReadAnimatedSourceContents(")

        self.assertIn("ranges::find(result, source)", sources)
        self.assertIn("source->contentResolved", sources)
        self.assertIn("size <= Images::kReadBytesLimit", reader)
        self.assertIn("file.read(Images::kReadBytesLimit + 1)", reader)
        self.assertIn("loaded.size() <= Images::kReadBytesLimit", reader)
        self.assertIn("result.push_back({ source, std::move(content) });",
                      reader)

    def test_video_content_uses_prefetched_bytes_and_keeps_failed_fallback(self):
        video = (SOURCE / "editor/scene/scene_item_video.cpp").read_text()
        content = block_body(video, "QByteArray ItemVideo::content() const")
        player = block_body(video, "void ItemVideo::createPlayer()")
        duplicate = block_body(
            video,
            "std::shared_ptr<ItemBase> ItemVideo::duplicate(",
        )

        self.assertIn("_source->contentResolved", content)
        self.assertIn("return _source->content;", content)
        self.assertIn("file.size() > Images::kReadBytesLimit", content)
        self.assertIn("MakeReader(_source->path, callback)", player)
        self.assertIn("std::make_shared<ItemVideo>(_source", duplicate)

    def test_closing_box_cancels_prefetch_continuation(self):
        send_files = (SOURCE / "boxes/send_files_box.cpp").read_text()
        prepare = block_body(send_files, "void SendFilesBox::prepare()")
        closing = block_body(prepare, "boxClosing() | rpl::on_next([=]")
        send = block_body(send_files, "void SendFilesBox::send(")
        animated = block_body(send, "if (animated) {")
        completion = block_body(animated, "const auto done = crl::guard(")
        close_guard = block_body(completion, "if (_closing) {")

        self.assertIn("_closing = true;", closing)
        self.assertLess(
            closing.index("_closing = true;"),
            closing.index("_cancelledCallback();"),
        )
        self.assertIn("return;", close_guard)
        self.assertLess(
            completion.index("if (_closing)"),
            completion.index("entry.source->content ="),
        )
        self.assertLess(
            completion.index("if (_closing)"),
            completion.index("send(options, ctrlShiftEnter);"),
        )


if __name__ == "__main__":
    unittest.main()
