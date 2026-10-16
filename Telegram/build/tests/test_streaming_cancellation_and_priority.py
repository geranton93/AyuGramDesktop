"""Compile streaming cancellation and priority methods with controlled state."""
import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "SourceFiles"


def method_body(path, signature):
    source = path.read_text()
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


class StreamingCancellationAndPriority(unittest.TestCase):
    def test_stop_cancels_only_streaming_offsets_and_requeues_waiting_priority(self):
        reader_path = SOURCE / "media/streaming/media_streaming_reader.cpp"
        loader_path = SOURCE / "media/streaming/media_streaming_loader_mtproto.cpp"
        stop_streaming = method_body(
            reader_path, "void Reader::stopStreaming(bool stillActive)"
        )
        cancel_range = method_body(
            reader_path, "void Reader::cancelLoadInRange(uint32 from, uint32 till)"
        )
        ready_to_request = method_body(
            loader_path, "bool LoaderMtproto::readyToRequest() const"
        )
        set_priority = method_body(
            loader_path, "void LoaderMtproto::setPriority(int priority)"
        )
        cpp = r'''
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <set>
#include <vector>

using uint32 = std::uint32_t;
using int64 = std::int64_t;
#define Expects(value) assert(value)

struct OffsetQueue {
    std::set<int64> offsets;
    bool empty() const { return offsets.empty(); }
    void add(int64 offset) { offsets.insert(offset); }
    void clear() { offsets.clear(); }
    std::set<int64> takeInRange(int64 from, int64 till) {
        auto result = std::set<int64>();
        for (auto i = offsets.begin(); i != offsets.end();) {
            if (*i >= from && *i < till) {
                result.insert(*i);
                i = offsets.erase(i);
            } else {
                ++i;
            }
        }
        return result;
    }
};

struct Mutex {
    void lock() {}
    void unlock() {}
};
struct QMutexLocker {
    explicit QMutexLocker(Mutex*) {}
};
struct CacheHelper {
    std::atomic<void*> waiting = nullptr;
    Mutex mutex;
};
struct MockLoader {
    std::vector<int64> cancelled;
    std::vector<int> *events = nullptr;
    int removed = 0;
    void cancel(int64 offset) {
        cancelled.push_back(offset);
        events->push_back(10);
    }
    void tryRemoveFromQueue() {
        ++removed;
        events->push_back(30);
    }
};

struct Reader {
    explicit Reader(MockLoader *loader) : _loader(loader) {}
    void stopStreaming(bool stillActive);
    void cancelLoadInRange(uint32 from, uint32 till);
    uint32 size() const { return _size; }
    void refreshLoaderPriority() {
        ++priorityRefreshes;
        events.push_back(25);
    }
    void processDownloaderRequests() {
        ++processedDownloaderRequests;
        events.push_back(20);
    }

    MockLoader *_loader = nullptr;
    uint32 _size = 0;
    bool _streamingActive = true;
    void *_sleeping = nullptr;
    std::atomic<void*> _waiting = nullptr;
    std::atomic<bool> _stopStreamingAsync = false;
    CacheHelper *_cacheHelper = nullptr;
    OffsetQueue _loadingOffsets;
    std::set<uint32> _downloaderOffsetsRequested;
    std::vector<int> events;
    int priorityRefreshes = 0;
    int processedDownloaderRequests = 0;
};

void Reader::stopStreaming(bool stillActive) {
''' + stop_streaming + r'''
}

void Reader::cancelLoadInRange(uint32 from, uint32 till) {
''' + cancel_range + r'''
}

struct LoaderMtproto {
    bool readyToRequest() const;
    void setPriority(int priority);
    bool haveSentRequests() const { return sentRequests; }
    void addToQueueWithPriority() {
        ++queueUpdates;
        lastQueuedPriority = _priority;
    }

    OffsetQueue _requested;
    int _priority = 0;
    bool sentRequests = false;
    int queueUpdates = 0;
    int lastQueuedPriority = -1;
};

bool LoaderMtproto::readyToRequest() const {
''' + ready_to_request + r'''
}

void LoaderMtproto::setPriority(int priority) {
''' + set_priority + r'''
}

int main() {
    auto checks = 0;
    auto failures = 0;
    const auto check = [&](const char *name, bool passed) {
        ++checks;
        if (!passed) {
            ++failures;
            std::fprintf(stderr, "FAIL: %s\n", name);
        }
    };

    auto loaderForReader = MockLoader();
    auto reader = Reader(&loaderForReader);
    loaderForReader.events = &reader.events;
    reader._size = 256 * 1024;
    reader._loadingOffsets.add(0);
    reader._loadingOffsets.add(128 * 1024);
    reader._loadingOffsets.add(256 * 1024);
    reader._downloaderOffsetsRequested.insert(128 * 1024);
    reader.stopStreaming(false);
    check("stop cancels unshared streamed parts", loaderForReader.cancelled.size() == 1
        && loaderForReader.cancelled.front() == 0);
    check("stop preserves downloader-owned part", reader._downloaderOffsetsRequested.contains(128 * 1024));
    check("cancel range is half open", reader._loadingOffsets.offsets == std::set<int64>{ 256 * 1024 });
    check("stop processes downloader handoff before queue cleanup", reader.events
        == std::vector<int>{ 10, 20, 25, 30 });
    check("stop asks loader to discard an empty queue entry", loaderForReader.removed == 1);
    check("stop resets streaming state", !reader._streamingActive && reader.priorityRefreshes == 1);

    auto emptyLoader = MockLoader();
    auto emptyReader = Reader(&emptyLoader);
    emptyLoader.events = &emptyReader.events;
    emptyReader._loadingOffsets.add(0);
    emptyReader.stopStreaming(false);
    check("zero-size cleanup cancels queued offsets safely",
        emptyLoader.cancelled == std::vector<int64>{ 0 });

    auto priority = LoaderMtproto();
    priority._requested.add(64 * 1024);
    priority.setPriority(5);
    check("priority change requeues unsent pending parts", priority.queueUpdates == 1
        && priority.lastQueuedPriority == 5 && priority.readyToRequest());
    priority.setPriority(5);
    check("unchanged priority does not requeue", priority.queueUpdates == 1);
    priority._requested = OffsetQueue();
    priority.setPriority(6);
    check("idle task does not enter download queue", priority.queueUpdates == 1);
    priority.sentRequests = true;
    priority.setPriority(7);
    check("priority change updates a task with active requests", priority.queueUpdates == 2
        && priority.lastQueuedPriority == 7);

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="ayu-streaming-state-") as directory:
            directory = pathlib.Path(directory)
            source = directory / "test.cpp"
            binary = directory / "test"
            source.write_text(cpp)
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run(compiler + [
                "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-pedantic",
                str(source), "-o", str(binary),
            ], check=True, timeout=60)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
