"""Guard DownloadManager against late resolve callbacks after Delete All."""
import pathlib
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "SourceFiles"


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


class DownloadManagerClearAllResolve(unittest.TestCase):
    def test_delete_all_resets_resolve_indexes_before_clearing_downloads(self):
        source = (SOURCE / "data/data_download_manager.cpp").read_text()
        delete_all = block_body(source, "void DownloadManager::deleteAll()")
        finish = block_body(
            source,
            "void DownloadManager::resolveRequestsFinished(",
        )

        reset_needed = delete_all.index("data.resolveNeeded = 0;")
        reset_sent_total = delete_all.index("data.resolveSentTotal = 0;")
        clear_downloads = delete_all.index(
            "for (auto &id : base::take(data.downloaded))"
        )
        resolve_loop = finish.index(
            "for (; data.resolveSentTotal > 0; --data.resolveSentTotal)"
        )
        resolve_index = finish.index(
            "begin(data.downloaded) + (--data.resolveNeeded)"
        )

        self.assertLess(reset_needed, clear_downloads)
        self.assertLess(reset_sent_total, clear_downloads)
        self.assertLess(resolve_loop, resolve_index)


if __name__ == "__main__":
    unittest.main()
