"""Compile downloader detach handling from Reader with controlled state."""
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import unittest
from contextlib import nullcontext

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "SourceFiles"


def test_temporary_directory(prefix):
    root = os.environ.get("AYU_TEST_PERSIST_TEMP_ROOT")
    if root:
        root = pathlib.Path(root)
        root.mkdir(parents=True, exist_ok=True)
        return nullcontext(tempfile.mkdtemp(prefix=prefix, dir=root))
    return tempfile.TemporaryDirectory(prefix=prefix)


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


class StreamingDownloaderDetach(unittest.TestCase):
    def test_malformed_part_fails_downloader_and_reaches_streaming_reader(self):
        reader_path = SOURCE / "media/streaming/media_streaming_reader.cpp"
        source = reader_path.read_text()
        callback_start = source.index("_loader->parts(")
        callback_end = source.index("_loader->speedEstimate()", callback_start)
        callback = source[callback_start:callback_end]

        self.assertRegex(
            callback,
            re.compile(
                r"if \(part\.valid\(size\(\)\)\)\s*\{\s*"
                r"_partsForDownloader\.fire_copy\(part\);\s*\}\s*"
                r"else\s*\{\s*_partsForDownloader\.fire\(\{\s*"
                r"LoadedPart::kFailedOffset\s*\}\);\s*\}"
            ),
        )
        self.assertEqual(callback.count("_partsForDownloader.fire_copy(part)"), 1)
        self.assertEqual(
            callback.count(
                "_partsForDownloader.fire({ LoadedPart::kFailedOffset });"
            ),
            1,
        )
        self.assertLess(
            callback.index("if (part.valid(size()))"),
            callback.index("_partsForDownloader.fire_copy(part)"),
        )
        self.assertLess(
            callback.index("_partsForDownloader.fire({ LoadedPart::kFailedOffset });"),
            callback.index("_loadedParts.emplace(std::move(part))"),
        )

    def test_ordinary_load_wakes_waiting_reader_and_drains_its_offset(self):
        reader_path = SOURCE / "media/streaming/media_streaming_reader.cpp"
        load_for_downloader = method_body(
            reader_path,
            "void Reader::loadForDownloader(\n\t\tnot_null<Storage::StreamedFileDownloader*> downloader,\n\t\tint64 offset)",
        )
        queue_at = load_for_downloader.index(
            "_downloaderOffsetRequests.emplace("
        )
        signal_at = load_for_downloader.index(
            "_downloaderCancellationPending.store("
        )
        self.assertLess(queue_at, signal_at)
        continue_downloader = method_body(
            reader_path, "void Reader::continueDownloaderFromMainThread()"
        )
        process_pending = method_body(
            reader_path,
            "void Reader::processPendingDownloaderCancellationFromStreamingThread()",
        )
        enqueue_offsets = method_body(
            reader_path, "void Reader::enqueueDownloaderOffsets()"
        )
        process_offsets = method_body(
            reader_path, "void Reader::processDownloaderOffsets()"
        )
        process_requests = method_body(
            reader_path, "void Reader::processDownloaderRequests()"
        )
        send_requests = method_body(
            reader_path, "void Reader::sendDownloaderRequests()"
        )
        wake_from_sleep = method_body(reader_path, "void Reader::wakeFromSleep()")
        fill = method_body(
            reader_path,
            "Reader::FillState Reader::fill(\n\t\tint64 offset,\n\t\tbytes::span buffer,\n\t\tnot_null<crl::semaphore*> notify)",
        )
        waiting_at = fill.index("startWaiting();")
        drain_at = fill.index(
            "processPendingDownloaderCancellationFromStreamingThread()",
            waiting_at,
        )
        self.assertLess(waiting_at, drain_at)

        cpp = r'''
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <iterator>
#include <limits>
#include <memory>
#include <mutex>
#include <set>
#include <utility>
#include <vector>

using uint32 = std::uint32_t;
using uint64 = std::uint64_t;
using int64 = std::int64_t;
using std::begin;
using std::empty;
using std::end;
template <typename Type>
using not_null = Type;
#define Expects(value) assert(value)

namespace ranges {
template <typename Range, typename Value>
auto find(const Range &range, const Value &value) {
    return std::find(begin(range), end(range), value);
}
}

namespace crl {
struct semaphore {
    void release() { ++releases; }
    int releases = 0;
};
}

struct QMutex {
    void lock() { mutex.lock(); }
    void unlock() { mutex.unlock(); }
    std::mutex mutex;
};
struct QMutexLocker {
    explicit QMutexLocker(QMutex *mutex) : lock(*mutex) {}
    std::lock_guard<QMutex> lock;
};
struct CacheHelper {
    QMutex mutex;
    crl::semaphore *waiting = nullptr;
};

namespace Storage {
struct StreamedFileDownloader {};
}

struct Loader {
    void attachDownloader(Storage::StreamedFileDownloader*) {}
    void load(uint32 offset) { loaded.push_back(offset); }
    void cancel(uint32) {}
    void clearAttachedDownloader() {}
    void tryRemoveFromQueue() {}
    std::vector<uint32> loaded;
};

struct DownloaderOffsetRequest {
    enum class Type { Load, Acknowledge, Cancel };
    Type type = Type::Load;
    uint64 generation = 0;
    uint32 offset = 0;
};

struct DownloaderRequests {
    void emplace(DownloaderOffsetRequest request) {
        values.push_back(std::move(request));
    }
    auto take() {
        auto result = std::move(values);
        values.clear();
        return result;
    }
    auto begin() const { return values.begin(); }
    auto end() const { return values.end(); }
    bool empty() const { return values.empty(); }
    void clear() { values.clear(); }
    std::vector<DownloaderOffsetRequest> values;
};

struct FlatSet : std::set<uint32> {
    bool remove(uint32 value) { return erase(value) != 0; }
};

class Reader {
public:
    static constexpr int kDownloaderRequestsLimit = 4;

    void loadForDownloader(
        not_null<Storage::StreamedFileDownloader*> downloader,
        int64 offset);
    void continueDownloaderFromMainThread();
    void processPendingDownloaderCancellationFromStreamingThread();
    void wakeFromSleep();
    void cancelForDownloader(Storage::StreamedFileDownloader*);
    void enqueueDownloaderOffsets();
    void processDownloaderRequests();
    void processDownloaderOffsets();
    void sendDownloaderRequests();

    void processCacheResults() {}
    void checkForDownloaderReadyOffsets() {}
    void pruneDownloaderCache(uint32) {}
    void checkForDownloaderChange(int) {}
    bool downloaderWaitForCachedSlice(uint32) { return false; }

    Storage::StreamedFileDownloader *_attachedDownloader = nullptr;
    Loader *_loader = nullptr;
    uint64 _downloaderGeneration = 7;
    DownloaderRequests _downloaderOffsetRequests;
    std::atomic<bool> _downloaderCancellationPending = false;
    std::atomic<crl::semaphore*> _waiting = nullptr;
    std::atomic<crl::semaphore*> _sleeping = nullptr;
    std::vector<uint32> _offsetsForDownloader;
    FlatSet _downloaderOffsetsRequested;
    std::set<uint32> _loadingOffsets;
    std::set<uint32> _downloaderReadCache;
    uint64 _activeDownloaderGeneration = 7;
    std::shared_ptr<CacheHelper> _cacheHelper;
    std::mutex _downloaderDispatchMutex;
    bool _streamingActive = true;
};

void Reader::cancelForDownloader(Storage::StreamedFileDownloader*) {}

void Reader::loadForDownloader(
        not_null<Storage::StreamedFileDownloader*> downloader,
        int64 offset) {
''' + load_for_downloader + r'''
}

void Reader::continueDownloaderFromMainThread() {
''' + continue_downloader + r'''
}

void Reader::wakeFromSleep() {
''' + wake_from_sleep + r'''
}

void Reader::processPendingDownloaderCancellationFromStreamingThread() {
''' + process_pending + r'''
}

void Reader::enqueueDownloaderOffsets() {
''' + enqueue_offsets + r'''
}

void Reader::processDownloaderRequests() {
''' + process_requests + r'''
}

void Reader::processDownloaderOffsets() {
''' + process_offsets + r'''
}

void Reader::sendDownloaderRequests() {
''' + send_requests + r'''
}

int main() {
    auto reader = Reader();
    auto loader = Loader();
    auto downloader = Storage::StreamedFileDownloader();
    auto waiting = crl::semaphore();
    reader._loader = &loader;
    reader._attachedDownloader = &downloader;
    reader._waiting.store(&waiting, std::memory_order_release);

    reader.loadForDownloader(&downloader, 4096);
    reader.continueDownloaderFromMainThread();
    reader.processPendingDownloaderCancellationFromStreamingThread();

    const auto woke = waiting.releases == 1;
    reader.processPendingDownloaderCancellationFromStreamingThread();
    const auto dispatched = loader.loaded == std::vector<uint32>{ 4096 }
        && reader._downloaderOffsetsRequested.size() == 1
        && reader._downloaderOffsetsRequested.contains(4096)
        && reader._downloaderOffsetRequests.empty();
    auto cachedReader = Reader();
    auto cacheHelper = std::make_shared<CacheHelper>();
    auto cachedWaiting = crl::semaphore();
    auto ordinaryWaiting = crl::semaphore();
    cacheHelper->waiting = &cachedWaiting;
    cachedReader._cacheHelper = cacheHelper;
    cachedReader._waiting.store(&ordinaryWaiting, std::memory_order_release);
    cachedReader._downloaderCancellationPending.store(
        true,
        std::memory_order_release);
    cachedReader.wakeFromSleep();
    const auto wokeCachedReader = cachedWaiting.releases == 1
        && ordinaryWaiting.releases == 0;
    std::printf("waiting reader woke: %s\n", woke ? "yes" : "no");
    std::printf("queued offset reaches loader: %s\n",
        dispatched ? "yes" : "no");
    std::printf("cached reader woke: %s\n",
        wokeCachedReader ? "yes" : "no");
    return woke && dispatched && wokeCachedReader ? 0 : 1;
}
'''
        with test_temporary_directory(
            prefix="ayu-downloader-wakeup-",
        ) as directory:
            directory = pathlib.Path(directory)
            source = directory / "test.cpp"
            binary = directory / "test"
            source.write_text(cpp)
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run(compiler + [
                "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-pedantic",
                str(source), "-o", str(binary),
            ], check=True, timeout=60)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())

    def test_detach_clears_old_requests_and_allows_same_offset_retry(self):
        reader_path = SOURCE / "media/streaming/media_streaming_reader.cpp"
        cancel_downloader = method_body(
            reader_path,
            "void Reader::cancelForDownloader(\n\t\tnot_null<Storage::StreamedFileDownloader*> downloader)",
        )
        done_downloader = method_body(
            reader_path,
            "void Reader::doneForDownloader(\n\t\tnot_null<Storage::StreamedFileDownloader*> downloader,\n\t\tint64 offset)",
        )
        continue_downloader = method_body(
            reader_path, "void Reader::continueDownloaderFromMainThread()"
        )
        process_from_streaming_thread = method_body(
            reader_path,
            "void Reader::processPendingDownloaderCancellationFromStreamingThread()",
        )
        process_offsets = method_body(
            reader_path, "void Reader::processDownloaderOffsets()"
        )
        wake_from_sleep = method_body(reader_path, "void Reader::wakeFromSleep()")
        fill = method_body(
            reader_path,
            "Reader::FillState Reader::fill(\n\t\tint64 offset,\n\t\tbytes::span buffer,\n\t\tnot_null<crl::semaphore*> notify)",
        )
        enqueue_offsets = method_body(
            reader_path, "void Reader::enqueueDownloaderOffsets()"
        )
        process_requests = method_body(
            reader_path, "void Reader::processDownloaderRequests()"
        )
        send_requests = method_body(
            reader_path, "void Reader::sendDownloaderRequests()"
        )
        self.assertLess(
            send_requests.index("processPendingDownloaderCancellationFromStreamingThread()"),
            send_requests.index("_loader->load(offset)"),
        )
        contains = method_body(
            SOURCE / "media/streaming/media_streaming_loader.cpp",
            "bool PriorityQueue::contains(int64 value) const",
        )
        file_source = (SOURCE / "media/streaming/media_streaming_file.cpp").read_text()
        packet_reader = method_body(
            SOURCE / "media/streaming/media_streaming_file.cpp",
            "void File::Context::readNextPacket()",
        )
        self.assertLess(
            packet_reader.index("processPendingDownloaderCancellationFromStreamingThread()"),
            packet_reader.index("readPacket()"),
        )
        self.assertIn(
            "processPendingDownloaderCancellationFromStreamingThread()",
            file_source,
        )
        waiting_at = fill.index("startWaiting();")
        drain_at = fill.index(
            "processPendingDownloaderCancellationFromStreamingThread()",
            waiting_at,
        )
        retry_at = fill.index("} while (checkForSomethingMoreReceived());", drain_at)
        self.assertLess(waiting_at, drain_at)
        self.assertLess(drain_at, retry_at)
        cpp = r'''
#include <algorithm>
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <functional>
#include <limits>
#include <iterator>
#include <memory>
#include <mutex>
#include <set>
#include <thread>
#include <utility>
#include <vector>

using uint32 = std::uint32_t;
using int64 = std::int64_t;
using std::begin;
using std::end;
using std::empty;
#define Expects(value) assert(value)

thread_local bool callingDownloaderCancellation = false;
std::mutex cancellationAttemptMutex;
std::condition_variable cancellationAttemptCondition;
bool cancellationLockAttempted = false;

struct DispatchMutex {
    std::mutex mutex;

    void lock() {
        if (!callingDownloaderCancellation || mutex.try_lock()) {
            if (callingDownloaderCancellation) {
                return;
            }
            mutex.lock();
            return;
        }
        {
            auto lock = std::lock_guard(cancellationAttemptMutex);
            cancellationLockAttempted = true;
            cancellationAttemptCondition.notify_one();
        }
        mutex.lock();
    }

    void unlock() {
        mutex.unlock();
    }
};

namespace ranges {
template <typename Range, typename Value>
auto find(const Range &range, const Value &value) {
    return std::find(begin(range), end(range), value);
}

template <typename Range, typename Value, typename Projection>
auto find(const Range &range, const Value &value, Projection projection) {
    return std::find_if(begin(range), end(range), [&](const auto &entry) {
        return std::invoke(projection, entry) == value;
    });
}
}

namespace Storage {
struct StreamedFileDownloader {};
}

struct DownloaderOffsetRequest {
    enum class Type { Load, Acknowledge, Cancel };
    Type type = Type::Load;
    std::uint64_t generation = 0;
    uint32 offset = 0;
    operator uint32() const { return offset; }
};

struct PriorityQueue {
    struct Entry { int64 value = 0; };
    bool contains(int64 value) const;
    std::vector<Entry> _data;
};

struct MockSemaphore {
    int releases = 0;
    void release() { ++releases; }
};

struct QMutex {
    void lock() { mutex.lock(); }
    void unlock() { mutex.unlock(); }
    std::mutex mutex;
};
struct QMutexLocker {
    explicit QMutexLocker(QMutex *mutex) : lock(*mutex) {}
    std::lock_guard<QMutex> lock;
};
struct CacheHelper {
    QMutex mutex;
    MockSemaphore *waiting = nullptr;
};

bool PriorityQueue::contains(int64 value) const {
''' + contains + r'''
}

template <typename T>
struct ThreadSafeQueue {
    std::vector<T> values;
    std::mutex mutex;
    void emplace(T value) {
        std::lock_guard lock(mutex);
        values.push_back(std::move(value));
    }
    std::vector<T> take() {
        std::lock_guard lock(mutex);
        auto result = std::move(values);
        values.clear();
        return result;
    }
};

struct OffsetList : std::deque<uint32> {
    using std::deque<uint32>::operator=;

    template <typename Request>
    OffsetList &operator=(std::vector<Request> &&requests) {
        clear();
        for (const auto &request : requests) {
            push_back(uint32(request));
        }
        return *this;
    }
};

struct FlatSet : std::set<uint32> {
    using std::set<uint32>::operator=;

    bool remove(uint32 value) { return erase(value) != 0; }
};

struct MockLoader {
    std::vector<uint32> loaded;
    std::vector<uint32> cancelled;
    int cleared = 0;
    int removed = 0;
    std::function<void(uint32)> onLoad;
    void load(uint32 offset) {
        loaded.push_back(offset);
        if (onLoad) {
            onLoad(offset);
        }
    }
    void cancel(uint32 offset) { cancelled.push_back(offset); }
    void clearAttachedDownloader() { ++cleared; }
    void tryRemoveFromQueue() { ++removed; }
};

struct Reader {
    static constexpr int kDownloaderRequestsLimit = 4;

    void cancelForDownloader(Storage::StreamedFileDownloader *downloader);
    void doneForDownloader(Storage::StreamedFileDownloader *downloader, int64 offset);
    void continueDownloaderFromMainThread();
    void processPendingDownloaderCancellationFromStreamingThread();
    void enqueueDownloaderOffsets();
    void processDownloaderRequests();
    void processDownloaderOffsets();
    void sendDownloaderRequests();
    void wakeFromSleep();

    void processCacheResults() {}
    void checkForDownloaderReadyOffsets() {
        if (readyOffsetsHook) {
            readyOffsetsHook();
        }
    }
    void pruneDoneDownloaderRequests() {}
    void pruneDownloaderCache(uint32) {}
    void checkForDownloaderChange(int) {}
    bool downloaderWaitForCachedSlice(uint32) { return false; }
    void requestFromDownloader(uint32 offset) {
        _downloaderOffsetRequests.emplace(DownloaderOffsetRequest{
            .type = DownloaderOffsetRequest::Type::Load,
            .generation = _downloaderGeneration,
            .offset = offset,
        });
    }

    std::function<void()> readyOffsetsHook;

    MockLoader *_loader = nullptr;
    Storage::StreamedFileDownloader *_attachedDownloader = nullptr;
    std::uint64_t _downloaderGeneration = 0;
    bool _streamingActive = true;
    std::atomic<MockSemaphore*> _sleeping = nullptr;
    std::atomic<MockSemaphore*> _waiting = nullptr;
    OffsetList _offsetsForDownloader;
    FlatSet _downloaderOffsetsRequested;
    std::set<uint32> _loadingOffsets;
    std::set<uint32> _downloaderReadCache;
    std::uint64_t _activeDownloaderGeneration = 0;
    ThreadSafeQueue<DownloaderOffsetRequest> _downloaderOffsetRequests;
    std::atomic<bool> _downloaderCancellationPending = false;
    DispatchMutex _downloaderDispatchMutex;
    std::shared_ptr<CacheHelper> _cacheHelper;
};

void Reader::cancelForDownloader(
        Storage::StreamedFileDownloader *downloader) {
''' + cancel_downloader + r'''
}

void Reader::doneForDownloader(
        Storage::StreamedFileDownloader *downloader, int64 offset) {
''' + done_downloader + r'''
}

void Reader::continueDownloaderFromMainThread() {
''' + continue_downloader + r'''
}

void Reader::wakeFromSleep() {
''' + wake_from_sleep + r'''
}

void Reader::processPendingDownloaderCancellationFromStreamingThread() {
''' + process_from_streaming_thread + r'''
}

void Reader::enqueueDownloaderOffsets() {
''' + enqueue_offsets + r'''
}

void Reader::processDownloaderRequests() {
''' + process_requests + r'''
}

void Reader::processDownloaderOffsets() {
''' + process_offsets + r'''
}

void Reader::sendDownloaderRequests() {
''' + send_requests + r'''
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

    auto priorityQueue = PriorityQueue();
    priorityQueue._data = { { 128 }, { 256 } };
    check("priority queue finds a requested player offset",
        priorityQueue.contains(128));
    check("priority queue rejects an absent offset",
        !priorityQueue.contains(512));

    auto loader = MockLoader();
    auto reader = Reader();
    reader._loader = &loader;
    auto first = Storage::StreamedFileDownloader();
    auto second = Storage::StreamedFileDownloader();
    reader._attachedDownloader = &first;
    reader._offsetsForDownloader = { 0, 128, 256 };
    reader._downloaderOffsetsRequested = { 0, 128, 256 };
    loader.loaded = { 0, 128, 256 };
    reader._downloaderReadCache.insert(1);
    reader._loadingOffsets.insert(128);
    reader.requestFromDownloader(768);
    reader.processDownloaderRequests();
    reader.requestFromDownloader(512);
    reader.doneForDownloader(&first, 128);
    reader.cancelForDownloader(&first);
    reader.doneForDownloader(&first, 0);
    check("cancel stays queued while the streaming worker is awake",
        reader._offsetsForDownloader == std::deque<uint32>{ 0, 128, 256, 768 }
            && reader._downloaderOffsetsRequested.size() == 4
            && reader._downloaderOffsetsRequested.contains(0)
            && reader._downloaderOffsetsRequested.contains(128)
            && reader._downloaderOffsetsRequested.contains(256)
            && reader._downloaderOffsetsRequested.contains(768)
            && reader._downloaderOffsetRequests.values.size() == 3
            && reader._downloaderCancellationPending.load(std::memory_order_acquire));
    reader._attachedDownloader = &second;
    reader.requestFromDownloader(0);
    reader.processPendingDownloaderCancellationFromStreamingThread();
    check("detach clears old offsets and retains only the new request",
        reader._offsetsForDownloader.size() == 1
            && reader._offsetsForDownloader.front() == 0);
    check("detach clears old in-flight dedup markers",
        reader._downloaderOffsetsRequested.size() == 1
            && reader._downloaderOffsetsRequested.contains(0));
    check("detach advances the active downloader generation",
        reader._activeDownloaderGeneration == 1);
    check("detach cancels downloader-only loader offsets",
        loader.cancelled == std::vector<uint32>{ 0, 256, 768 });
    check("detach preserves player-shared loader offset",
        std::find(begin(loader.cancelled), end(loader.cancelled), 128)
            == end(loader.cancelled));
    check("detach clears downloader cache and acknowledgements",
        reader._downloaderReadCache.empty()
            && reader._downloaderOffsetRequests.values.empty());
    check("detach asks the loader to remove an idle queue entry", loader.removed == 1);
    check("old downloader load already sent before cancellation",
        loader.loaded == std::vector<uint32>{ 0, 128, 256, 768, 0 });
    check("cancel without acknowledgement frees same offset for retry",
        std::count(begin(loader.loaded), end(loader.loaded), uint32(0)) == 2);
    check("new downloader owns the retried offset",
        reader._downloaderOffsetsRequested.size() == 1
            && reader._downloaderOffsetsRequested.contains(0));

    auto third = Storage::StreamedFileDownloader();
    reader.cancelForDownloader(&second);
    auto drainMutex = std::mutex();
    auto drainCondition = std::condition_variable();
    auto drainPaused = false;
    auto resumeDrain = false;
    reader.readyOffsetsHook = [&] {
        auto lock = std::unique_lock(drainMutex);
        drainPaused = true;
        drainCondition.notify_one();
        drainCondition.wait(lock, [&] { return resumeDrain; });
    };
    auto drain = std::thread([&] {
        reader.processPendingDownloaderCancellationFromStreamingThread();
    });
    {
        auto lock = std::unique_lock(drainMutex);
        drainCondition.wait(lock, [&] { return drainPaused; });
    }
    reader._attachedDownloader = &third;
    reader.cancelForDownloader(&third);
    {
        auto lock = std::lock_guard(drainMutex);
        resumeDrain = true;
        drainCondition.notify_one();
    }
    drain.join();
    check("cancel queued after take is drained before the current dispatch",
        !reader._downloaderCancellationPending.load(std::memory_order_acquire)
            && reader._downloaderOffsetRequests.values.empty()
            && reader._activeDownloaderGeneration == 3);
    reader.readyOffsetsHook = {};

    auto fourth = Storage::StreamedFileDownloader();
    reader._attachedDownloader = &fourth;
    reader.requestFromDownloader(1024);
    auto dispatchMutex = std::mutex();
    auto dispatchCondition = std::condition_variable();
    auto dispatchPaused = false;
    auto resumeDispatch = false;
    reader.readyOffsetsHook = [&] {
        auto lock = std::unique_lock(dispatchMutex);
        dispatchPaused = true;
        dispatchCondition.notify_one();
        dispatchCondition.wait(lock, [&] { return resumeDispatch; });
    };
    auto dispatch = std::thread([&] {
        reader.processDownloaderRequests();
    });
    {
        auto lock = std::unique_lock(dispatchMutex);
        dispatchCondition.wait(lock, [&] { return dispatchPaused; });
    }
    reader.cancelForDownloader(&fourth);
    {
        auto lock = std::lock_guard(dispatchMutex);
        resumeDispatch = true;
        dispatchCondition.notify_one();
    }
    dispatch.join();
    check("cancel after queue snapshot prevents stale offset dispatch",
        std::find(begin(loader.loaded), end(loader.loaded), uint32(1024))
            == end(loader.loaded)
            && reader._activeDownloaderGeneration == 4
            && !reader._downloaderCancellationPending.load(
                std::memory_order_acquire));
    reader.readyOffsetsHook = {};

    auto waitingSemaphore = MockSemaphore();
    auto fifth = Storage::StreamedFileDownloader();
    reader._attachedDownloader = &fifth;
    reader._waiting.store(&waitingSemaphore, std::memory_order_release);
    reader.cancelForDownloader(&fifth);
    check("cancel wakes a Reader fill waiting for remote bytes",
        waitingSemaphore.releases == 1);
    reader._waiting.store(nullptr, std::memory_order_release);
    reader.processPendingDownloaderCancellationFromStreamingThread();

    auto sleepingSemaphore = MockSemaphore();
    auto sixth = Storage::StreamedFileDownloader();
    reader._attachedDownloader = &sixth;
    reader._sleeping.store(&sleepingSemaphore, std::memory_order_release);
    reader._waiting.store(&waitingSemaphore, std::memory_order_release);
    reader.cancelForDownloader(&sixth);
    check("cancel releases only the active sleep semaphore",
        sleepingSemaphore.releases == 1
            && waitingSemaphore.releases == 1
            && reader._sleeping.load(std::memory_order_acquire) == nullptr);
    reader._waiting.store(nullptr, std::memory_order_release);
    reader.processPendingDownloaderCancellationFromStreamingThread();

    auto seventh = Storage::StreamedFileDownloader();
    reader._attachedDownloader = &seventh;
    reader._offsetsForDownloader = { 1536 };
    reader._downloaderOffsetsRequested.clear();
    auto loadMutex = std::mutex();
    auto loadCondition = std::condition_variable();
    auto loadPaused = false;
    auto resumeLoad = false;
    auto cancellationContended = false;
    loader.onLoad = [&](uint32 offset) {
        assert(offset == 1536);
        auto lock = std::unique_lock(loadMutex);
        loadPaused = true;
        loadCondition.notify_one();
        loadCondition.wait(lock, [&] { return resumeLoad; });
    };
    const auto loadedBeforeRace = loader.loaded.size();
    auto dispatchRace = std::thread([&] {
        reader.sendDownloaderRequests();
    });
    {
        auto lock = std::unique_lock(loadMutex);
        loadCondition.wait(lock, [&] { return loadPaused; });
    }
    auto releaseLoad = std::thread([&] {
        auto contended = false;
        {
            auto lock = std::unique_lock(cancellationAttemptMutex);
            contended = cancellationAttemptCondition.wait_for(
                lock,
                std::chrono::seconds(5),
                [&] { return cancellationLockAttempted; });
        }
        cancellationContended = contended;
        {
            auto lock = std::lock_guard(loadMutex);
            resumeLoad = true;
            loadCondition.notify_one();
        }
    });
    callingDownloaderCancellation = true;
    reader.cancelForDownloader(&seventh);
    callingDownloaderCancellation = false;
    releaseLoad.join();
    dispatchRace.join();
    check("cancel waits for an in-progress loader dispatch",
        cancellationContended);
    check("dispatch completes before cancellation takes effect",
        loader.loaded.size() == loadedBeforeRace + 1
            && loader.loaded.back() == 1536
            && reader._downloaderCancellationPending.load(
                std::memory_order_acquire));
    auto eighth = Storage::StreamedFileDownloader();
    reader._attachedDownloader = &eighth;
    reader.requestFromDownloader(1536);
    reader.processPendingDownloaderCancellationFromStreamingThread();
    loader.onLoad = {};
    check("same offset retries after the concurrent cancellation barrier",
        reader._offsetsForDownloader.size() == 1
            && reader._offsetsForDownloader.front() == 1536
            && reader._downloaderOffsetsRequested.size() == 1
            && reader._downloaderOffsetsRequested.contains(1536)
            && reader._activeDownloaderGeneration == reader._downloaderGeneration
            && loader.loaded.size() == loadedBeforeRace + 2
            && loader.loaded.back() == 1536);

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
'''
        with test_temporary_directory(
            prefix="ayu-downloader-detach-",
        ) as directory:
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
