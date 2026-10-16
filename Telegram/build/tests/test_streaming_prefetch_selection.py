"""Compile the production prefetch-selection block with controlled reader state.

This checks selection logic, not the full asynchronous Reader/Loader integration.
"""
import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "SourceFiles"


class PrefetchSelection(unittest.TestCase):
    def test_consumer_position_not_loading_queue_controls_seeking(self):
        reader = (SOURCE / "media/streaming/media_streaming_reader.cpp").read_text()
        method = reader.split(
            "Reader::FillState Reader::fillFromSlices(uint32 offset, bytes::span buffer) {", 1
        )[1]
        block = method.split("\n\tauto result = _slices.fill(", 1)[0]
        block = "\tconst auto " + block.split("\n\tconst auto ", 1)[1]
        cpp = r'''
#include "media/streaming/media_streaming_prefetch.h"
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <optional>
using uint32 = std::uint32_t;
namespace crl {
using time = std::int64_t;
time now() { return 10000; }
}
constexpr auto kPrefetchStatsStaleAfter = crl::time(3000);
using namespace Media::Streaming;
struct Queue {
    std::optional<std::int64_t> value;
    auto front() const { return value; }
};
struct Probe {
    Queue _loadingOffsets;
    std::optional<uint32> _lastFillEnd;
    std::atomic<crl::time> _lastDownloadAt = 10000;
    std::atomic<std::int64_t> _downloadBytesPerSecond = 4 * 1024 * 1024;
    std::atomic<std::int64_t> _consumptionBytesPerSecond = 0;
    std::atomic<int> _requestLatencyMs = 0;
    std::atomic<bool> _downloadUnreliable = false;
    int parts(uint32 offset) {
''' + block + r'''
        return prefetch.partsAhead;
    }
};
int main() {
    auto failures = 0;
    auto checks = 0;
    const auto check = [&](const char *name, int actual, int expected) {
        ++checks;
        if (actual != expected) {
            ++failures;
            std::fprintf(stderr, "%s: got %d, expected %d\n", name, actual, expected);
        }
    };
    auto p = Probe();
    p._lastFillEnd = 32768;
    p._loadingOffsets.value = 131072;
    check("sequential subpart with read-ahead", p.parts(32768), 48);
    check("waiting retry", p.parts(32768), 48);
    p._loadingOffsets.value = 262144;
    check("arrival advances queue not consumer", p.parts(32768), 48);
    p._loadingOffsets.value.reset();
    check("empty queue", p.parts(32768), 48);
    p._loadingOffsets.value = 524288;
    check("forward seek", p.parts(524288), 8);
    check("seek retry", p.parts(524288), 8);
    p._lastFillEnd = 557056;
    p._loadingOffsets.value = 655360;
    check("sequential after seek", p.parts(557056), 48);
    check("backward reread", p.parts(524288), 8);
    p._lastFillEnd = 0;
    check("engaged zero end", p.parts(0), 48);
    check("discontinuity from zero", p.parts(1), 8);
    p._lastFillEnd.reset();
    check("no successful read", p.parts(0), 48);
    p._lastDownloadAt = 0;
    check("startup fallback", p.parts(0), 8);
    p._lastDownloadAt = 6999;
    check("stale fallback", p.parts(0), 8);
    p._lastDownloadAt = 10000;
    p._downloadUnreliable = true;
    check("unreliable fallback", p.parts(0), 8);
    p._downloadUnreliable = false;
    p._downloadBytesPerSecond = 0;
    check("unknown rate fallback", p.parts(0), 8);
    std::printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="ayu-prefetch-") as directory:
            directory = pathlib.Path(directory)
            source = directory / "test.cpp"
            binary = directory / "test"
            source.write_text(cpp)
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run(compiler + [
                "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-pedantic",
                "-I", str(SOURCE), str(source), "-o", str(binary),
            ], check=True, timeout=60)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
