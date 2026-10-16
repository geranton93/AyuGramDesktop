"""Verify streaming request cancellation does not poison rate samples."""
import os
import pathlib
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


def compile_and_run(cpp):
    with tempfile.TemporaryDirectory(prefix="ayu-streaming-request-stats-") as directory:
        directory = pathlib.Path(directory)
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
            raise AssertionError(result.stdout + result.stderr)


class StreamingRequestStats(unittest.TestCase):
    def test_cancelled_offset_does_not_match_or_accumulate_across_retries(self):
        source = (SOURCE / "media/streaming/media_streaming_loader_mtproto.cpp").read_text()
        stop = function_definition(source, "void LoaderMtproto::stop()")
        cancel = function_definition(source, "void LoaderMtproto::cancelForOffset(")
        cancel_on_fail = function_definition(source, "void LoaderMtproto::cancelOnFail()")
        feed = function_definition(source, "bool LoaderMtproto::feedPart(")
        self.assertIn("removePendingStats()", stop)
        self.assertIn("removePendingStatsForOffset(offset)", cancel)
        self.assertIn("removePendingStats()", cancel_on_fail)
        self.assertIn("IsPendingStatsEntry(entry, offset)", feed)

        matches_pending = function_definition(source, "bool IsPendingStatsEntry(")
        template_start = source.rfind(
            "template <typename Entry>",
            0,
            source.index("bool IsPendingStatsEntry("),
        )
        signature_start = source.index("bool IsPendingStatsEntry(")
        matches_pending = source[
            template_start:
            signature_start + len(matches_pending)
        ]
        remove_for_offset = function_definition(
            source,
            "void LoaderMtproto::removePendingStatsForOffset(int64 offset)",
        )
        remove_all = function_definition(
            source,
            "void LoaderMtproto::removePendingStats()",
        )
        cpp = r'''
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <vector>
using int64 = std::int64_t;
namespace ranges {
template <typename Container, typename Predicate>
auto remove_if(Container &container, Predicate predicate) {
    return std::remove_if(container.begin(), container.end(), predicate);
}
}
struct StatsEntry {
    int64 start = 0;
    int64 end = 0;
    int64 offset = 0;
};
class LoaderMtproto {
public:
    std::vector<StatsEntry> _stats;
    void removePendingStatsForOffset(int64 offset);
    void removePendingStats();
};
''' + matches_pending + remove_for_offset + remove_all + r'''
int main() {
    auto loader = LoaderMtproto();
    loader._stats = {{10, 20, 12}, {11, 0, 12}, {12, 0, 24}};
    loader.removePendingStatsForOffset(12);
    if (loader._stats.size() != 2
        || loader._stats[0].end != 20
        || loader._stats[0].offset != 12
        || loader._stats[1].offset != 24) {
        return 1;
    }

    loader._stats.push_back({30, 0, 12});
    auto retry = std::find_if(loader._stats.begin(), loader._stats.end(), [](const auto &entry) {
        return IsPendingStatsEntry(entry, 12);
    });
    if (retry == loader._stats.end() || retry->start != 30) {
        return 2;
    }
    retry->end = 40;
    if (loader._stats[0].end != 20 || retry->end != 40) {
        return 3;
    }

    for (auto i = 0; i != 100; ++i) {
        loader._stats.push_back({50 + i, 0, 12});
        loader.removePendingStatsForOffset(12);
        if (loader._stats.size() != 3) {
            return 4;
        }
    }
    loader._stats.push_back({200, 0, 36});
    loader.removePendingStats();
    return (loader._stats.size() == 2
        && loader._stats[0].end == 20
        && loader._stats[1].end == 40) ? 0 : 5;
}
'''
        compile_and_run(cpp)

    def test_short_final_range_uses_actual_response_bytes(self):
        source = (SOURCE / "media/streaming/media_streaming_loader_mtproto.cpp").read_text()
        feed = function_definition(source, "bool LoaderMtproto::feedPart(")
        check = function_definition(source, "void LoaderMtproto::checkStats()")
        pending_start = source.rfind(
            "template <typename Entry>",
            0,
            source.index("bool IsPendingStatsEntry("),
        )
        pending_end = source.index("\n\n} // namespace", pending_start)
        matches_pending = source[pending_start:pending_end]

        self.assertIn("entry.receivedBytes = bytes.size();", feed)
        self.assertIn("received += entry.receivedBytes;", check)

        cpp = r'''
#include <algorithm>
#include <cstdint>
#include <utility>
#include <vector>

using int64 = std::int64_t;
using std::begin;
using std::end;

namespace crl {
using time = std::int64_t;
inline time current = 0;
inline time now() {
    return current;
}
}

namespace Storage {
constexpr int64 kDownloadPartSize = 128 * 1024;
}

class QByteArray {
public:
    QByteArray(int size = 0) : _size(size) {
    }

    int size() const {
        return _size;
    }

private:
    int _size = 0;
};

struct LoadedPart {
    int64 offset = 0;
    QByteArray bytes;
    static constexpr int64 kFailedOffset = -1;
};

struct SpeedEstimate {
    int bytesPerSecond = 0;
    bool unreliable = false;
    int latencyMs = 0;
};

template <typename T>
struct EventStream {
    T last{};
    bool fired = false;

    void fire(T value) {
        last = std::move(value);
        fired = true;
    }
};

struct Timer {
    bool isActive() const {
        return false;
    }

    void callOnce(crl::time) {
    }
};

struct StatsEntry {
    crl::time start = 0;
    crl::time end = 0;
    int64 offset = 0;
    int64 receivedBytes = 0;
};

class LoaderMtproto {
public:
    std::vector<StatsEntry> _stats;
    EventStream<LoadedPart> _parts;
    EventStream<SpeedEstimate> _speedEstimate;
    crl::time _firstRequestStart = 0;
    Timer _statsTimer;

    bool feedPart(int64 offset, const QByteArray &bytes);
    void checkStats();
};

constexpr auto kCheckStatsInterval = crl::time(1000);
constexpr auto kInitialStatsWait = 5 * crl::time(1000);
''' + matches_pending + feed + check + r'''
int main() {
    auto loader = LoaderMtproto();
    loader._firstRequestStart = 6000;
    loader._stats = {
        { .start = 0, .end = 100, .offset = 256 * 1024, .receivedBytes = 777 },
        { .start = 6000, .offset = 0 },
        { .start = 7000, .offset = 128 * 1024 },
        { .start = 8000, .offset = 256 * 1024 },
    };

    crl::current = 8000;
    if (!loader.feedPart(0, QByteArray(Storage::kDownloadPartSize))) {
        return 1;
    }
    crl::current = 9000;
    if (!loader.feedPart(128 * 1024, QByteArray(Storage::kDownloadPartSize))) {
        return 2;
    }
    crl::current = 10000;
    if (!loader.feedPart(256 * 1024, QByteArray(32 * 1024))) {
        return 3;
    }
    if (loader._stats[0].end != 100
        || loader._stats[0].receivedBytes != 777
        || loader._stats[3].receivedBytes != 32 * 1024) {
        return 4;
    }

    loader.checkStats();
    if (!loader._speedEstimate.fired
        || loader._speedEstimate.last.bytesPerSecond != 72 * 1024
        || !loader._speedEstimate.last.unreliable) {
        return 5;
    }
    return 0;
}
'''
        compile_and_run(cpp)


if __name__ == "__main__":
    unittest.main()
