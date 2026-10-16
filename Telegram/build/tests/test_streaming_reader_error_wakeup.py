"""Guard streaming-reader waiter ownership across wake sources."""
import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest
from contextlib import nullcontext

SOURCE = pathlib.Path(os.environ.get(
    "AYU_SOURCE_ROOT",
    pathlib.Path(__file__).resolve().parents[2] / "SourceFiles",
))


def test_temporary_directory(prefix):
    root = os.environ.get("AYU_TEST_PERSIST_TEMP_ROOT")
    if root:
        root = pathlib.Path(root)
        root.mkdir(parents=True, exist_ok=True)
        return nullcontext(tempfile.mkdtemp(prefix=prefix, dir=root))
    return tempfile.TemporaryDirectory(prefix=prefix)


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


class StreamingReaderErrorWakeup(unittest.TestCase):
    def test_fill_streaming_errors_use_the_waiter_releasing_path(self):
        source = (SOURCE / "media/streaming/media_streaming_reader.cpp").read_text()
        fill = block_body(source, "Reader::FillState Reader::fill(")
        normalized = " ".join(fill.split())

        self.assertIn("if (_streamingError) { return failed(); }", normalized)
        self.assertIn("return _streamingError ? failed() : lastResult;", normalized)

    def test_all_wake_sources_claim_one_waiter(self):
        source = (SOURCE / "media/streaming/media_streaming_reader.cpp").read_text()
        fill = block_body(source, "Reader::FillState Reader::fill(")
        start_waiting = block_body(fill, "const auto startWaiting = [&]")
        clear_waiting = block_body(fill, "const auto clearWaiting = [&]")
        failed = block_body(fill, "const auto failed = [&]")
        wake_from_sleep = block_body(source, "void Reader::wakeFromSleep()")
        part_start = source.index(
            "\n\t\tif ",
            source.index("_loadedParts.emplace(std::move(part));"),
        ) + 1
        part_end = source.index("\n\t}, _lifetime);", part_start)
        part_wakeup = source[part_start:part_end]
        cache_start = source.index("strong->sizes = std::move(sizes);")
        cache_end = source.index("\n\t\t\t\t} else {", cache_start) + len(
            "\n\t\t\t\t}"
        )
        cache_wakeup = source[cache_start:cache_end]
        cache_waiting = block_body(source, "struct Reader::CacheHelper {")

        self.assertIn("crl::semaphore *waiting = nullptr;", cache_waiting)
        self.assertIn("_cacheHelper->waiting = notify.get();", start_waiting)
        self.assertIn("_waiting.store(notify.get(), std::memory_order_release);", start_waiting)
        self.assertIn("_cacheHelper->waiting = nullptr;", clear_waiting)
        self.assertIn("_waiting.store(nullptr, std::memory_order_release);", clear_waiting)
        self.assertIn("std::exchange(", failed)
        self.assertIn("_cacheHelper->waiting", failed)
        self.assertIn("_waiting.exchange(", failed)
        self.assertIn("waiting || !waitingWasPublished", failed)
        self.assertIn("std::exchange(", part_wakeup)
        self.assertIn("_cacheHelper->waiting", part_wakeup)
        self.assertIn("_waiting.exchange(", part_wakeup)
        self.assertIn("std::exchange(", cache_wakeup)
        self.assertIn("strong->waiting", cache_wakeup)
        self.assertIn("crl::on_main(weak, [=] {", source[cache_end:])
        self.assertIn("_cacheHelper->waiting", wake_from_sleep)
        self.assertIn("_waiting.exchange(", wake_from_sleep)

        cpp = r'''
#include <atomic>
#include <barrier>
#include <mutex>
#include <thread>
#include <utility>
#include <vector>

enum class FillState { Failed, Waiting };
struct MockSemaphore {
    void release() { ++releases; }
    std::atomic<int> releases = 0;
};
struct NotNullSemaphore {
    MockSemaphore *value = nullptr;
    MockSemaphore *get() const { return value; }
    MockSemaphore *operator->() const { return value; }
};
using QMutex = std::mutex;
struct QMutexLocker {
    explicit QMutexLocker(QMutex *mutex) : lock(*mutex) {}
    std::lock_guard<QMutex> lock;
};
struct CacheHelper {
    QMutex mutex;
    std::vector<int> sizes;
    MockSemaphore *waiting = nullptr;
};
struct Reader {
    Reader(CacheHelper *cache, MockSemaphore *semaphore)
    : _cacheHelper(cache), notify{ semaphore } {}
    void publish() {
        auto &waitingWasPublished = _waitingWasPublished;
        const auto startWaiting = [&] {
''' + start_waiting + r'''
        };
        startWaiting();
    }
    void clear() {
        const auto clearWaiting = [&] {
''' + clear_waiting + r'''
        };
        clearWaiting();
    }
    FillState fail() {
        auto &waitingWasPublished = _waitingWasPublished;
        const auto failed = [&] {
''' + failed + r'''
        };
        return failed();
    }
    void partReady() {
''' + part_wakeup + r'''
    }
    void cacheReady() {
        QMutexLocker lock(&_cacheHelper->mutex);
        auto strong = _cacheHelper;
        std::vector<int> sizes;
''' + cache_wakeup + r'''
    }
    void wakeFromSleep() {
''' + wake_from_sleep + r'''
    }
    CacheHelper *_cacheHelper = nullptr;
    std::atomic<MockSemaphore*> _waiting = nullptr;
    std::atomic<MockSemaphore*> _sleeping = nullptr;
    std::atomic<bool> _downloaderCancellationPending = false;
    NotNullSemaphore notify;
    bool _waitingWasPublished = false;
};
bool cacheLoaderAndFailureShareOneWaiter() {
    auto semaphore = MockSemaphore();
    auto cache = CacheHelper();
    auto reader = Reader(&cache, &semaphore);
    reader.publish();
    reader.partReady();
    reader.cacheReady();
    const auto result = reader.fail();
    return result == FillState::Failed
        && reader._waiting.load() == nullptr
        && cache.waiting == nullptr
        && semaphore.releases == 1;
}
bool cancellationAndFailureShareOneWaiter() {
    auto semaphore = MockSemaphore();
    auto cache = CacheHelper();
    auto reader = Reader(&cache, &semaphore);
    reader.publish();
    reader._downloaderCancellationPending = true;
    reader.wakeFromSleep();
    const auto result = reader.fail();
    return result == FillState::Failed
        && cache.waiting == nullptr
        && semaphore.releases == 1;
}
bool uncachedLoaderAndFailureShareOneWaiter() {
    auto semaphore = MockSemaphore();
    auto reader = Reader(nullptr, &semaphore);
    reader.publish();
    reader.partReady();
    const auto result = reader.fail();
    return result == FillState::Failed
        && reader._waiting.load() == nullptr
        && semaphore.releases == 1;
}
bool failureBeforeWaitPublicationStillReleases() {
    auto semaphore = MockSemaphore();
    auto cache = CacheHelper();
    auto reader = Reader(&cache, &semaphore);
    const auto result = reader.fail();
    return result == FillState::Failed
        && cache.waiting == nullptr
        && semaphore.releases == 1;
}
bool clearPreventsLateWake() {
    auto semaphore = MockSemaphore();
    auto cache = CacheHelper();
    auto reader = Reader(&cache, &semaphore);
    reader.publish();
    reader.clear();
    reader.partReady();
    return cache.waiting == nullptr && semaphore.releases == 0;
}
bool cacheWakersRaceForOneWaiter() {
    for (auto iteration = 0; iteration != 256; ++iteration) {
        auto semaphore = MockSemaphore();
        auto cache = CacheHelper();
        auto reader = Reader(&cache, &semaphore);
        reader.publish();
        auto start = std::barrier(4);
        auto part = std::thread([&] {
            start.arrive_and_wait();
            reader.partReady();
        });
        auto cached = std::thread([&] {
            start.arrive_and_wait();
            reader.cacheReady();
        });
        auto failed = std::thread([&] {
            start.arrive_and_wait();
            reader.fail();
        });
        start.arrive_and_wait();
        part.join();
        cached.join();
        failed.join();
        if (semaphore.releases != 1 || cache.waiting != nullptr) {
            return false;
        }
    }
    return true;
}
bool uncachedWakersRaceForOneWaiter() {
    for (auto iteration = 0; iteration != 256; ++iteration) {
        auto semaphore = MockSemaphore();
        auto reader = Reader(nullptr, &semaphore);
        reader.publish();
        reader._downloaderCancellationPending = true;
        auto start = std::barrier(4);
        auto part = std::thread([&] {
            start.arrive_and_wait();
            reader.partReady();
        });
        auto cancelled = std::thread([&] {
            start.arrive_and_wait();
            reader.wakeFromSleep();
        });
        auto failed = std::thread([&] {
            start.arrive_and_wait();
            reader.fail();
        });
        start.arrive_and_wait();
        part.join();
        cancelled.join();
        failed.join();
        if (semaphore.releases != 1 || reader._waiting.load() != nullptr) {
            return false;
        }
    }
    return true;
}
int main() {
    return (cacheLoaderAndFailureShareOneWaiter()
        && cancellationAndFailureShareOneWaiter()
        && uncachedLoaderAndFailureShareOneWaiter()
        && failureBeforeWaitPublicationStillReleases()
        && clearPreventsLateWake()
        && cacheWakersRaceForOneWaiter()
        && uncachedWakersRaceForOneWaiter()) ? 0 : 1;
}
'''
        with test_temporary_directory(
            prefix="ayu-reader-error-wakeup-",
        ) as directory:
            directory = pathlib.Path(directory)
            test_source = directory / "test.cpp"
            binary = directory / "test"
            test_source.write_text(cpp)
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run(compiler + [
                "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-pedantic",
                str(test_source), "-o", str(binary),
            ], check=True, timeout=60)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
