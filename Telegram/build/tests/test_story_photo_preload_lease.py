"""Guard story-sibling photo preload ownership and load delegation."""
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


def compile_and_run(cpp, prefix):
    root = os.environ.get("AYU_TEST_PERSIST_TEMP_ROOT")
    if root:
        root = pathlib.Path(root)
        root.mkdir(parents=True, exist_ok=True)
        directory = pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=root))
        _compile_and_run_in(cpp, directory)
    else:
        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            _compile_and_run_in(cpp, pathlib.Path(directory))


def _compile_and_run_in(cpp, directory):
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


class StoryPhotoPreloadLease(unittest.TestCase):
    def test_same_id_completion_does_not_clear_a_newer_preload(self):
        stories_source = (SOURCE / "data/data_stories.cpp").read_text()
        start = block_body(
            stories_source,
            "void Stories::startPreloading(not_null<Story*> story)",
        )
        self.assertLess(
            start.index("_preloading = std::make_unique<StoryPreload>"),
            start.index("_preloading->start("),
        )
        self.assertIn("_preloading->token() != token", start)
        cpp = r'''
#include <cassert>
#include <functional>
#include <iostream>
#include <memory>
#include <set>
#include <utility>
#include <vector>
template <typename T> using not_null = T;
#define Expects(...) assert(__VA_ARGS__)
struct FullStoryId {
    int value = 0;
    friend bool operator==(FullStoryId, FullStoryId) = default;
    friend bool operator<(FullStoryId a, FullStoryId b) {
        return a.value < b.value;
    }
};
struct Story {
    FullStoryId id;
    FullStoryId fullId() const { return id; }
};
struct StoryPreload {
    StoryPreload(Story *story, std::shared_ptr<int> token)
    : _id(story->fullId()), _token(std::move(token)) {}
    template <typename Done> void start(Done done) {
        done(true);
    }
    FullStoryId id() const { return _id; }
    const std::shared_ptr<int> &token() const { return _token; }
    FullStoryId _id;
    std::shared_ptr<int> _token;
};
namespace crl {
std::vector<std::function<void()>> pending;
template <typename Guard, typename Callable>
void on_main(Guard*, Callable &&callable) {
    pending.emplace_back(std::forward<Callable>(callable));
}
void runOne() {
    if (pending.empty()) return;
    auto callback = std::move(pending.front());
    pending.erase(pending.begin());
    callback();
}
void runAll() {
    while (!pending.empty()) runOne();
}
}
struct Stories {
    void startPreloading(not_null<Story*> story) {''' + start + r'''}
    void photoLoadSettingsChanged() { ++_preloadSettingsGeneration; }
    void preloadFinished(FullStoryId id, bool success) {
        _finished.emplace_back(id.value, success);
        if (success) _preloaded.emplace(id);
    }
    std::unique_ptr<StoryPreload> _preloading;
    std::set<FullStoryId> _preloaded;
    std::vector<std::pair<int, bool>> _finished;
    std::uint64_t _preloadSettingsGeneration = 0;
};
int main() {
    auto stories = Stories();
    auto first = Story{ FullStoryId{ 1 } };
    stories.startPreloading(&first);
    if (!stories._preloading || stories._preloading->id().value != 1) {
        std::cerr << "first preload was not installed\n";
        return 1;
    }

    const auto firstToken = stories._preloading->token();
    stories._preloading = nullptr;
    auto second = Story{ FullStoryId{ 1 } };
    stories.startPreloading(&second);
    if (!stories._preloading || stories._preloading->id().value != 1) {
        std::cerr << "replacement preload was not installed\n";
        return 2;
    }
    const auto secondToken = stories._preloading->token();
    if (firstToken == secondToken) {
        std::cerr << "replacement reused the prior preload token\n";
        return 3;
    }

    crl::runOne();
    if (!stories._preloading
        || stories._preloading->token() != secondToken
        || stories._preloaded.contains(FullStoryId{ 1 })) {
        std::cerr << "older completion affected the replacement preload\n";
        return 4;
    }

    crl::runAll();
    if (stories._preloading
        || stories._finished.size() != 1
        || !stories._preloaded.contains(FullStoryId{ 1 })) {
        std::cerr << "replacement preload did not finish exactly once\n";
        return 5;
    }
    return 0;
}
'''
        compile_and_run(cpp, "ayu-story-sync-preload-")

    def test_deleted_story_is_removed_from_preloaded_registry(self):
        stories_source = (SOURCE / "data/data_stories.cpp").read_text()
        deleted = block_body(
            stories_source,
            "void Stories::applyDeleted(not_null<PeerData*> peer, StoryId id)",
        )
        self.assertIn("_preloaded.remove(fullId);", deleted)
        self.assertIn("_preloadViewerIds.erase(", deleted)
        self.assertIn("const auto wasPreloading =", deleted)
        self.assertIn("auto wasQueued = ranges::contains(_toPreloadViewer, fullId);", deleted)
        self.assertIn("if (wasPreloading || wasQueued)", deleted)
        self.assertIn("preloadFinished(fullId);", deleted)

        finished = block_body(
            stories_source,
            "void Stories::preloadFinished(FullStoryId id, bool markAsPreloaded)",
        )
        self.assertIn("_toPreloadViewer.erase(", finished)
        self.assertIn("for (auto &sources : _toPreloadSources)", finished)

    def test_session_clear_releases_story_preload_before_photo_owners(self):
        stories_source = (SOURCE / "data/data_stories.cpp").read_text()
        stories_clear = block_body(stories_source, "void Stories::clear()")
        self.assertLess(
            stories_clear.index("_preloading = nullptr;"),
            stories_clear.index("_stories.clear();"),
        )

        session_source = (SOURCE / "data/data_session.cpp").read_text()
        session_clear = block_body(session_source, "void Session::clear()")
        self.assertLess(
            session_clear.index("_stories->clear();"),
            session_clear.index("_photos.clear();"),
        )

    def test_story_preload_retries_only_preload_cancelled_photo(self):
        story_source = (SOURCE / "data/data_story.cpp").read_text()
        story_preload = block_body(
            story_source,
            "void StoryPreload::start(Fn<void(bool)> done)",
        )
        self.assertIn("PhotoPreload::Should(photo, _story->peer(), true)", story_preload)
        normalized = "\n".join(line.strip() for line in story_preload.splitlines())
        self.assertIn("std::move(done),\ntrue);", normalized)

        preload_source = (SOURCE / "data/data_media_preload.cpp").read_text()
        should = block_body(preload_source, "bool PhotoPreload::Should(")
        self.assertIn(
            "retryCancelled && photo->_cancelledByPreloadLease",
            should,
        )
        start = block_body(preload_source, "void PhotoPreload::start(")
        normalized = "\n".join(line.strip() for line in start.splitlines())
        self.assertIn(
            "(!retryCancelled || !photo->_cancelledByPreloadLease)))",
            normalized,
        )

        photo_source = (SOURCE / "data/data_photo.cpp").read_text()
        cancel = block_body(photo_source, "void PhotoData::cancel()")
        self.assertIn("cancel(false);", cancel)
        cancel_by_reason = block_body(
            photo_source,
            "void PhotoData::cancel(bool retryableForPreload)",
        )
        self.assertIn(
            "_cancelledByPreloadLease = retryableForPreload;",
            cancel_by_reason,
        )
        release = block_body(
            photo_source,
            "void PhotoData::releasePreload(bool requireFull)",
        )
        self.assertIn("cancel(true);", release)
        load_internal = block_body(
            photo_source,
            "void PhotoData::loadInternal(",
        )
        self.assertIn("_cancelledByPreloadLease = false;", load_internal)
        self.assertLess(
            load_internal.index("Data::LoadCloudFile("),
            load_internal.index("_cancelledByPreloadLease = false;"),
        )

        cpp = r'''
#include <cassert>
template <typename T> using not_null = T;
struct PeerData {};
struct AutoDownloadSettings {
    bool autoDownload() const { return true; }
};
struct MainSession {
    const AutoDownloadSettings &settings() const { return _settings; }
    AutoDownloadSettings _settings;
};
struct PhotoData {
    bool cancelled() const { return _cancelled; }
    const MainSession &session() const { return _session; }
    bool _cancelled = false;
    bool _cancelledByPreloadLease = false;
    MainSession _session;
};
namespace Data {
namespace AutoDownload {
bool Should(bool enabled, PeerData*, PhotoData*) { return enabled; }
}
bool Should(not_null<PhotoData*> photo,
        not_null<PeerData*> context,
        bool retryCancelled) {''' + should + r'''}
}
int main() {
    auto peer = PeerData();
    auto manuallyCancelled = PhotoData();
    manuallyCancelled._cancelled = true;
    if (Data::Should(&manuallyCancelled, &peer, true)) return 1;

    auto preloadCancelled = PhotoData();
    preloadCancelled._cancelled = true;
    preloadCancelled._cancelledByPreloadLease = true;
    if (!Data::Should(&preloadCancelled, &peer, true)) return 2;
    if (Data::Should(&preloadCancelled, &peer, false)) return 3;

    auto active = PhotoData();
    if (!Data::Should(&active, &peer, false)) return 4;
    return 0;
}
'''
        compile_and_run(cpp, "ayu-story-cancel-reason-")

    def test_media_change_requeues_preloaded_viewer_story(self):
        stories_source = (SOURCE / "data/data_stories.cpp").read_text()
        media_changed = block_body(stories_source, "if (mediaChanged) {")

        self.assertIn("_preloaded.remove(fullId);", media_changed)
        self.assertIn("StorySourcesList::NotHidden", media_changed)
        self.assertIn("StorySourcesList::Hidden", media_changed)
        self.assertIn("auto viewer = _preloadViewerIds;", media_changed)
        self.assertIn("_preloaded.contains(id)", media_changed)
        self.assertIn("_toPreloadViewer = std::move(viewer);", media_changed)
        self.assertIn("continuePreloading();", media_changed)
        self.assertLess(
            media_changed.index("_preloaded.remove(fullId);"),
            media_changed.index("auto viewer = _preloadViewerIds;"),
        )
        self.assertLess(
            media_changed.index("_toPreloadViewer = std::move(viewer);"),
            media_changed.index("continuePreloading();"),
        )

    def test_photo_settings_change_requeues_policy_skipped_story_preload(self):
        story_source = (SOURCE / "data/data_story.cpp").read_text()
        story_preload = block_body(
            story_source,
            "void StoryPreload::start(Fn<void(bool)> done)",
        )
        self.assertIn("if (PhotoPreload::Should(", story_preload)
        self.assertIn("done(false);", story_preload)

        session_source = (SOURCE / "data/data_session.cpp").read_text()
        settings_changed = block_body(
            session_source,
            "void Session::photoLoadSettingsChanged()",
        )
        self.assertLess(
            settings_changed.index("photo->automaticLoadSettingsChanged();"),
            settings_changed.index("_stories->photoLoadSettingsChanged();"),
        )

        stories_source = (SOURCE / "data/data_stories.cpp").read_text()
        viewer_setup = block_body(
            stories_source,
            "void Stories::setPreloadingInViewer(std::vector<FullStoryId> ids)",
        )
        self.assertIn("_preloadViewerIds = ids;", viewer_setup)

        rebuild = block_body(
            stories_source,
            "void Stories::photoLoadSettingsChanged()",
        )
        self.assertIn("++_preloadSettingsGeneration;", rebuild)
        self.assertIn("StorySourcesList::Hidden", rebuild)
        self.assertIn("StorySourcesList::NotHidden", rebuild)
        self.assertIn("auto viewer = _preloadViewerIds;", rebuild)
        self.assertIn("continuePreloading();", rebuild)

        start_preloading = block_body(
            stories_source,
            "void Stories::startPreloading(not_null<Story*> story)",
        )
        self.assertIn("settingsGeneration != _preloadSettingsGeneration", start_preloading)
        self.assertIn("photoLoadSettingsChanged();", start_preloading)

    def test_auto_loading_does_not_retry_but_save_click_does(self):
        media_source = (SOURCE / "data/data_photo_media.cpp").read_text()
        automatic_load = block_body(
            media_source,
            "void PhotoMedia::automaticLoad(\n\t\tFileOrigin origin,\n\t\tnot_null<PeerData*> peer)",
        )
        self.assertIn("_owner->load(", automatic_load)
        self.assertIn("\t\ttrue);", automatic_load)

        click_source = (SOURCE / "data/data_file_click_handler.cpp").read_text()
        save_click = block_body(
            click_source,
            "void PhotoSaveClickHandler::onClickImpl() const",
        )
        self.assertLess(
            save_click.index("clearFailed(Data::PhotoSize::Large)"),
            save_click.index("data->load(context());"),
        )

        photo_source = (SOURCE / "data/data_photo.cpp").read_text()
        load = block_body(
            photo_source,
            "void PhotoData::load(\n\t\tData::FileOrigin origin,",
        )
        self.assertNotIn("clearFailed", load)
        cpp = r'''
enum class PhotoSize { Large };
using FileOrigin = int;
using LoadFromCloudSetting = int;
constexpr auto LoadFromCloudOrLocal = 0;
struct PhotoData {
    void clearFailed(PhotoSize) { failed = false; }
    void loadInternal(
            PhotoSize,
            FileOrigin,
            LoadFromCloudSetting,
            bool,
            bool) {
        requestStarted = !failed;
    }
    void load(
            FileOrigin origin,
            LoadFromCloudSetting fromCloud,
            bool autoLoading) {''' + load + r'''}
    bool failed = true;
    bool requestStarted = false;
};
int main() {
    auto automaticDemand = PhotoData();
    automaticDemand.load(1, LoadFromCloudOrLocal, true);
    automaticDemand.load(1, LoadFromCloudOrLocal, true);
    if (!automaticDemand.failed || automaticDemand.requestStarted) return 1;
    return 0;
}
'''
        compile_and_run(cpp, "ayu-story-photo-retry-")

    def test_preload_delegates_cancel_retry_and_releases_last_lease(self):
        preload_header = (SOURCE / "data/data_media_preload.h").read_text()
        self.assertEqual(
            2,
            preload_header.count("bool retryCancelled = true"),
        )
        preload_source = (SOURCE / "data/data_media_preload.cpp").read_text()
        photo_source = (SOURCE / "data/data_photo.cpp").read_text()
        cloud_source = (SOURCE / "data/data_cloud_file.cpp").read_text()
        start = block_body(
            preload_source,
            "void PhotoPreload::start(",
        )
        load_for_preload = block_body(
            photo_source,
            "bool PhotoData::loadForPreload(",
        )
        load_internal = block_body(
            photo_source,
            "void PhotoData::loadInternal(",
        )
        load_cloud_file = block_body(cloud_source, "void LoadCloudFile(")
        release_preload = block_body(
            photo_source,
            "void PhotoData::releasePreload(",
        )
        self.assertIn(
            "const auto requireFull = (completion == Completion::Full);",
            start,
        )
        self.assertIn(
            "class PhotoPreload final : public MediaPreload, public base::has_weak_ptr",
            preload_header,
        )
        self.assertIn("_requireFull = requireFull;", start)
        self.assertIn(
            "if (photo->loadForPreload(origin, requireFull)) {",
            start,
        )
        self.assertIn("crl::on_main(this, [=, this]", start)
        self.assertIn("photo->continueFullPreload(origin);", start)
        self.assertIn("++_preloadRequests;", load_for_preload)
        self.assertIn("LoadFromCloudOrLocal,\n\t\ttrue,\n\t\ttrue", load_for_preload)
        self.assertIn("++_fullPreloadRequests;", load_for_preload)
        self.assertIn("\t\trequireFull);", load_for_preload)
        self.assertIn("if (loader && loader->finished())", load_for_preload)
        self.assertIn("--_fullPreloadRequests;", release_preload)
        self.assertIn(
            "const auto fullPhotoRequired = requireFull\n"
            "\t\t|| (validSize == PhotoSize::Large && _fullPreloadRequests > 0);\n"
            "\tconst auto frontPartSize = fullPhotoRequired\n"
            "\t\t? 0\n"
            "\t\t: _images[existing].progressivePartSize;",
            load_internal,
        )
        self.assertIn(
            "Data::LoadCloudFile(\n"
            "\t\t&session(),\n"
            "\t\t_images[valid],\n"
            "\t\torigin,\n"
            "\t\tfromCloud,\n"
            "\t\tautoLoading,",
            load_internal,
        )
        existing_loader = load_cloud_file.index("if (file.loader) {")
        reset_cancelled = load_cloud_file.index(
            "file.flags &= ~CloudFile::Flag::Cancelled;"
        )
        self.assertLess(existing_loader, reset_cancelled)
        self.assertIn(
            "file.loader->increaseLoadSize(loadSize, autoLoading);",
            load_cloud_file,
        )
        existing_loader_body = block_body(
            load_cloud_file[existing_loader:], "if (file.loader) {"
        )
        self.assertTrue(existing_loader_body.rstrip().endswith("return;"))
        self.assertLess(
            reset_cancelled,
            load_cloud_file.index("file.loader = CreateFileLoader("),
        )
        self.assertLess(
            load_cloud_file.index("file.loader = CreateFileLoader("),
            load_cloud_file.index("file.loader->start();"),
        )
        cpp = r'''
#include <array>
#include <cassert>
#include <functional>
#include <memory>
#include <utility>
#include <vector>
using FileOrigin = int;
enum class PhotoSize { Large };
inline int PhotoSizeIndex(PhotoSize) { return 0; }
constexpr auto LoadFromCloudOrLocal = 0;
template <typename T> using not_null = T;
#define Expects(...) assert(__VA_ARGS__)
struct Lifetime {};
struct Event {};
struct Pipe {};
namespace rpl {
struct Operation {};
template <typename F> Operation filter(F &&) { return {}; }
Operation take(int) { return {}; }
template <typename F> Operation on_next(F &&, Lifetime &) { return {}; }
}
namespace crl {
std::vector<std::function<void()>> pending;
template <typename Guard, typename Callable>
void on_main(Guard*, Callable &&callable) {
    pending.emplace_back(std::forward<Callable>(callable));
}
void runAll() {
    while (!pending.empty()) {
        auto callback = std::move(pending.front());
        pending.erase(pending.begin());
        callback();
    }
}
}
template <typename L, typename R>
Pipe operator|(L &&, R &&) { return {}; }
struct Loader {
    bool done = false;
    bool finished() const { return done; }
    FileOrigin fileOrigin() const { return 42; }
    void cancel() { done = true; }
};
struct LoaderSlot {
    Loader *value = nullptr;
    Loader *get() const { return value; }
    explicit operator bool() const { return value != nullptr; }
    Loader *operator->() const { return value; }
};
namespace base {
Loader *take(LoaderSlot &slot) { return std::exchange(slot.value, nullptr); }
}
struct CloudImage { LoaderSlot loader; };
struct PhotoMedia;
struct PhotoData {
    bool isLoaded = false;
    bool isFailed = false;
    bool isCancelled = false;
    bool _cancelledByPreloadLease = false;
    bool isNullPhoto = false;
    bool _largeProgressivePartReady = false;
    bool loadingLarge = true;
    bool _regularLargeLoad = false;
    int loads = 0;
    int _preloadRequests = 0;
    int _fullPreloadRequests = 0;
    int cancels = 0;
    bool fullLoadRequested = false;
    std::weak_ptr<PhotoMedia> _media;
    Loader loader;
    std::array<CloudImage, 1> _images;
    PhotoData &owner() { return *this; }
    Event photoLoadProgress() { return {}; }
    bool failed(PhotoSize) const { return isFailed; }
    bool cancelled() const { return isCancelled; }
    bool isNull() const { return isNullPhoto; }
    bool loading(PhotoSize) const { return loadingLarge; }
    Event sessionDataAboutToBeCleared() { return {}; }
    std::shared_ptr<PhotoMedia> createMediaView();
    bool loadForPreload(FileOrigin, bool requireFull) {
        ++_preloadRequests;
        if (requireFull) {
            ++_fullPreloadRequests;
            const auto loader = _images[0].loader.get();
            if (loader && loader->finished()) return true;
        }
        ++loads;
        isCancelled = false;
        _cancelledByPreloadLease = false;
        fullLoadRequested = requireFull;
        return false;
    }
    void continueFullPreload(FileOrigin) {
        ++loads;
        fullLoadRequested = true;
    }
    void loadInternal(
            PhotoSize,
            FileOrigin,
            int,
            bool,
            bool,
            bool requireFull) {
        ++loads;
        fullLoadRequested = requireFull;
    }
    void cancel(bool retryableForPreload) {
        ++cancels;
        isCancelled = true;
        _cancelledByPreloadLease = retryableForPreload;
        loadingLarge = false;
    }
    void releasePreload(bool requireFull = false);
};
struct PhotoMedia {
    explicit PhotoMedia(PhotoData *owner) : data(owner) {}
    PhotoData *owner() const { return data; }
    bool loaded() const { return data->isLoaded; }
    PhotoData *data;
};
std::shared_ptr<PhotoMedia> PhotoData::createMediaView() {
    if (auto result = _media.lock()) return result;
    _largeProgressivePartReady = false;
    auto result = std::make_shared<PhotoMedia>(this);
    _media = result;
    return result;
}
struct MediaPreload {
    void callDone(bool success) { ++doneCalls; doneSuccess = success; }
    int doneCalls = 0;
    bool doneSuccess = false;
};
class PhotoPreload : public MediaPreload {
public:
    enum class Completion { Progressive, Full };
    explicit PhotoPreload(PhotoData *photo)
    : _photo(photo->createMediaView()) {
    }
    void startForTest(FileOrigin origin, bool retryCancelled = true,
            Completion completion = Completion::Progressive) {
        start(origin, retryCancelled, completion);
    }
    bool registered() const { return _registered; }
private:
    void start(FileOrigin origin, bool retryCancelled, Completion completion);
    void sessionCleared();
    std::shared_ptr<PhotoMedia> _photo;
    Lifetime _lifetime;
    Lifetime _sessionLifetime;
    bool _registered = false;
    bool _requireFull = false;
};
void PhotoPreload::sessionCleared() {}
void PhotoPreload::start(FileOrigin origin, bool retryCancelled,
        Completion completion) {''' + start + r'''
}
void PhotoData::releasePreload(bool requireFull) {''' + release_preload + r'''
}
int main() {
    auto cancelled = PhotoData();
    cancelled.isCancelled = true;
    auto skipped = PhotoPreload(&cancelled);
    skipped.startForTest(0);
    if (skipped.doneCalls != 1 || skipped.doneSuccess
        || skipped.registered() || cancelled.loads != 0) {
        return 1;
    }

    auto retriedPhoto = PhotoData();
    retriedPhoto.isCancelled = true;
    retriedPhoto._cancelledByPreloadLease = true;
    auto retried = PhotoPreload(&retriedPhoto);
    retried.startForTest(0);
    if (retried.doneCalls != 0 || !retried.registered()
        || retriedPhoto._preloadRequests != 1 || retriedPhoto.loads != 1
        || retriedPhoto.isCancelled
        || retriedPhoto._cancelledByPreloadLease) {
        return 2;
    }

    auto retryDisabledPhoto = PhotoData();
    retryDisabledPhoto.isCancelled = true;
    retryDisabledPhoto._cancelledByPreloadLease = true;
    auto retryDisabled = PhotoPreload(&retryDisabledPhoto);
    retryDisabled.startForTest(0, false);
    if (retryDisabled.doneCalls != 1 || retryDisabled.doneSuccess
        || retryDisabledPhoto.loads != 0) return 8;

    auto failedPhoto = PhotoData();
    failedPhoto.isFailed = true;
    auto failed = PhotoPreload(&failedPhoto);
    failed.startForTest(0, true);
    if (failed.doneCalls != 1 || failed.doneSuccess
        || failed.registered() || failedPhoto.loads != 0) {
        return 3;
    }

    auto shared = PhotoData();
    shared._preloadRequests = 2;
    shared._images[0].loader.value = &shared.loader;
    shared.releasePreload();
    if (shared._preloadRequests != 1 || shared.cancels != 0) {
        return 4;
    }
    shared.releasePreload();
    if (shared._preloadRequests != 0 || shared.cancels != 1
        || !shared._cancelledByPreloadLease) {
        return 5;
    }

    auto regular = PhotoData();
    regular._preloadRequests = 1;
    regular._regularLargeLoad = true;
    regular._images[0].loader.value = &regular.loader;
    regular.releasePreload();
    if (regular._preloadRequests != 0 || regular.cancels != 0) {
        return 6;
    }

    auto finished = PhotoData();
    finished._preloadRequests = 1;
    finished._fullPreloadRequests = 1;
    finished.loader.done = true;
    finished._images[0].loader.value = &finished.loader;
    finished.releasePreload(true);
    return (finished.cancels == 0 && !finished._fullPreloadRequests) ? 0 : 7;
}
'''
        compile_and_run(cpp, "ayu-story-photo-preload-")

    def test_progressive_photo_completion_releases_story_preload(self):
        photo_header = (SOURCE / "data/data_photo.h").read_text()
        photo_source = (SOURCE / "data/data_photo.cpp").read_text()
        preload_source = (SOURCE / "data/data_media_preload.cpp").read_text()
        self.assertIn("bool _largeProgressivePartReady = false;", photo_header)
        create_media_view = block_body(
            photo_source,
            "std::shared_ptr<PhotoMedia> PhotoData::createMediaView()",
        )
        clear_cache = block_body(
            photo_source,
            "void PhotoData::clearLocalCache()",
        )
        update_images = block_body(
            photo_source,
            "void PhotoData::updateImages(",
        )
        self.assertIn("_largeProgressivePartReady = false;", create_media_view)
        self.assertIn("_largeProgressivePartReady = false;", clear_cache)
        self.assertIn(
            "if (_images[largeIndex].location != large.location)",
            update_images,
        )
        load_internal = block_body(
            photo_source,
            "void PhotoData::loadInternal(",
        )
        normal_load = block_body(
            photo_source,
            "void PhotoData::load(\n\t\tPhotoSize size,",
        )
        self.assertIn("load(size, origin);", update_images)
        self.assertIn(
            "loadInternal(size, origin, fromCloud, autoLoading, false);",
            normal_load,
        )
        done = block_body(
            load_internal,
            "const auto done = [=](QImage result, QByteArray bytes) {",
        )
        self.assertIn("&& !result.isNull()", done)
        self.assertIn("&& goodFor < validSize", done)
        self.assertLess(
            done.index("const auto progressivePartReady"),
            done.index("active->set("),
        )
        self.assertIn("_largeProgressivePartReady = true;", done)
        self.assertIn("_owner->photoLoadProgress(this);", done)
        start = block_body(
            preload_source,
            "void PhotoPreload::start(",
        )
        self.assertEqual(1, start.count("photo->_largeProgressivePartReady"))
        self.assertIn("Completion::Progressive", start)
        self.assertEqual(3, start.count("progressive()"))
        self.assertIn("return media->loaded() || progressive();", start)
        self.assertEqual(2, start.count("callDone(successful());"))

        cpp = r'''
#include <array>
#include <cassert>
#include <functional>
#include <memory>
#include <type_traits>
#include <utility>
#include <vector>
using FileOrigin = int;
enum class PhotoSize { Large };
inline int PhotoSizeIndex(PhotoSize) { return 0; }
constexpr auto LoadFromCloudOrLocal = 0;
template <typename T> using not_null = T;
#define Expects(...) assert(__VA_ARGS__)
struct Lifetime {};
struct PhotoData;
struct Event { PhotoData *source = nullptr; };
template <typename F> struct Filter { F callback; };
struct Take { int count; };
template <typename F> struct OnNext { F callback; Lifetime *lifetime; };
struct Pipeline {
    PhotoData *source = nullptr;
    std::function<bool(PhotoData*)> filter = [](PhotoData*) { return true; };
    int remaining = 1;
};
namespace rpl {
template <typename F> auto filter(F &&callback) {
    return Filter<std::decay_t<F>>{std::forward<F>(callback)};
}
Take take(int count) { return {count}; }
template <typename F> auto on_next(F &&callback, Lifetime &lifetime) {
    return OnNext<std::decay_t<F>>{
        std::forward<F>(callback),
        &lifetime
    };
}
}
namespace crl {
std::vector<std::function<void()>> pending;
template <typename Guard, typename Callable>
void on_main(Guard*, Callable &&callable) {
    pending.emplace_back(std::forward<Callable>(callable));
}
void runAll() {
    while (!pending.empty()) {
        auto callback = std::move(pending.front());
        pending.erase(pending.begin());
        callback();
    }
}
}
template <typename F> Pipeline operator|(Event event, Filter<F> operation) {
    return Pipeline{
        event.source,
        [callback = std::move(operation.callback)](PhotoData *updated) {
            return callback(updated);
        }
    };
}
Pipeline operator|(Event event, Take operation) {
    return Pipeline{event.source, {}, operation.count};
}
template <typename F> Pipeline operator|(Pipeline pipeline, Filter<F> operation) {
    auto previous = std::move(pipeline.filter);
    pipeline.filter = [
        previous = std::move(previous),
        callback = std::move(operation.callback)
    ](PhotoData *updated) {
        return previous(updated) && callback(updated);
    };
    return pipeline;
}
Pipeline operator|(Pipeline pipeline, Take operation) {
    pipeline.remaining = operation.count;
    return pipeline;
}
void subscribePhotoLoad(
    PhotoData *photo,
    std::function<bool(PhotoData*)> filter,
    std::function<void()> callback,
    int remaining);
template <typename F> void operator|(Pipeline pipeline, OnNext<F> operation) {
    if (pipeline.source) {
        subscribePhotoLoad(
            pipeline.source,
            std::move(pipeline.filter),
            std::move(operation.callback),
            pipeline.remaining);
    }
}
struct Loader {
    bool done = false;
    bool finished() const { return done; }
    FileOrigin fileOrigin() const { return 42; }
    void cancel() { done = true; }
};
struct LoaderSlot {
    Loader *value = nullptr;
    Loader *get() const { return value; }
    explicit operator bool() const { return value != nullptr; }
    Loader *operator->() const { return value; }
};
namespace base {
Loader *take(LoaderSlot &slot) { return std::exchange(slot.value, nullptr); }
}
struct CloudImage { LoaderSlot loader; };
struct PhotoMedia;
struct PhotoData {
    bool isLoaded = false;
    bool isFailed = false;
    bool isCancelled = false;
    bool _cancelledByPreloadLease = false;
    bool isNullPhoto = false;
    bool loadingLarge = true;
    bool signalProgressiveOnLoad = false;
    bool _largeProgressivePartReady = false;
    bool _regularLargeLoad = false;
    int loads = 0;
    int _preloadRequests = 0;
    int _fullPreloadRequests = 0;
    int cancels = 0;
    bool fullLoadRequested = false;
    std::weak_ptr<PhotoMedia> _media;
    Loader loader;
    std::array<CloudImage, 1> _images;
    std::function<void(PhotoData*)> photoSubscriber;
    PhotoData &owner() { return *this; }
    Event photoLoadProgress() { return {this}; }
    bool failed(PhotoSize) const { return isFailed; }
    bool cancelled() const { return isCancelled; }
    bool isNull() const { return isNullPhoto; }
    bool loading(PhotoSize) const { return loadingLarge; }
    Event sessionDataAboutToBeCleared() { return {}; }
    std::shared_ptr<PhotoMedia> createMediaView();
    bool loadForPreload(FileOrigin, bool requireFull) {
        ++_preloadRequests;
        if (requireFull) {
            ++_fullPreloadRequests;
            const auto loader = _images[0].loader.get();
            if (loader && loader->finished()) return true;
        }
        ++loads;
        isCancelled = false;
        _cancelledByPreloadLease = false;
        fullLoadRequested = requireFull;
        if (signalProgressiveOnLoad) {
            _largeProgressivePartReady = true;
            if (photoSubscriber) photoSubscriber(this);
        }
        return false;
    }
    void continueFullPreload(FileOrigin) {
        ++loads;
        fullLoadRequested = true;
    }
    void loadInternal(
            PhotoSize,
            FileOrigin,
            int,
            bool,
            bool,
            bool requireFull) {
        ++loads;
        fullLoadRequested = requireFull;
    }
    void cancel(bool retryableForPreload) {
        ++cancels;
        isCancelled = true;
        _cancelledByPreloadLease = retryableForPreload;
        loadingLarge = false;
    }
    void releasePreload(bool requireFull = false);
};
void subscribePhotoLoad(
        PhotoData *photo,
        std::function<bool(PhotoData*)> filter,
        std::function<void()> callback,
        int remaining) {
    photo->photoSubscriber = [
        filter = std::move(filter),
        callback = std::move(callback),
        remaining
    ](PhotoData *updated) mutable {
        if (remaining > 0 && filter(updated)) {
            --remaining;
            callback();
        }
    };
}
struct PhotoMedia {
    explicit PhotoMedia(PhotoData *owner) : data(owner) {}
    PhotoData *owner() const { return data; }
    bool loaded() const { return data->isLoaded; }
    PhotoData *data;
};
std::shared_ptr<PhotoMedia> PhotoData::createMediaView() {
    if (auto result = _media.lock()) return result;
    _largeProgressivePartReady = false;
    auto result = std::make_shared<PhotoMedia>(this);
    _media = result;
    return result;
}
struct MediaPreload {
    void callDone(bool success) { ++doneCalls; doneSuccess = success; }
    int doneCalls = 0;
    bool doneSuccess = false;
};
class PhotoPreload : public MediaPreload {
public:
    enum class Completion { Progressive, Full };
    explicit PhotoPreload(PhotoData *photo)
    : _photo(photo->createMediaView()) {
    }
    void startForTest(FileOrigin origin, bool retryCancelled = true,
            Completion completion = Completion::Progressive) {
        start(origin, retryCancelled, completion);
    }
    bool registered() const { return _registered; }
private:
    void start(FileOrigin origin, bool retryCancelled, Completion completion);
    void sessionCleared();
    std::shared_ptr<PhotoMedia> _photo;
    Lifetime _lifetime;
    Lifetime _sessionLifetime;
    bool _registered = false;
    bool _requireFull = false;
};
void PhotoPreload::sessionCleared() {}
void PhotoPreload::start(FileOrigin origin, bool retryCancelled,
        Completion completion) {''' + block_body(
            preload_source,
            "void PhotoPreload::start(",
        ) + r'''
}
void PhotoData::releasePreload(bool requireFull) {''' + block_body(
            photo_source,
            "void PhotoData::releasePreload(",
        ) + r'''
}
int main() {
    auto progressivePhoto = PhotoData();
    progressivePhoto.signalProgressiveOnLoad = true;
    auto progressive = PhotoPreload(&progressivePhoto);
    progressive.startForTest(0);
    if (progressive.doneCalls != 1 || !progressive.doneSuccess
        || !progressive.registered() || progressivePhoto.loads != 1) {
        return 1;
    }
    if (progressivePhoto.fullLoadRequested) return 11;

    auto progressiveFullPhoto = PhotoData();
    progressiveFullPhoto.signalProgressiveOnLoad = true;
    auto progressiveFull = PhotoPreload(&progressiveFullPhoto);
    progressiveFull.startForTest(0, true, PhotoPreload::Completion::Full);
    if (progressiveFull.doneCalls != 0 || !progressiveFull.registered()
        || progressiveFullPhoto.loads != 1
        || !progressiveFullPhoto.fullLoadRequested
        || !progressiveFullPhoto._largeProgressivePartReady) return 4;
    progressiveFullPhoto.isLoaded = true;
    if (!progressiveFullPhoto.photoSubscriber) return 5;
    progressiveFullPhoto.photoSubscriber(&progressiveFullPhoto);
    if (progressiveFull.doneCalls != 1 || !progressiveFull.doneSuccess) {
        return 6;
    }

    auto cachedProgressivePhoto = PhotoData();
    auto cachedProgressiveView = cachedProgressivePhoto.createMediaView();
    if (!cachedProgressiveView) return 10;
    cachedProgressivePhoto._largeProgressivePartReady = true;
    auto cachedProgressive = PhotoPreload(&cachedProgressivePhoto);
    cachedProgressive.startForTest(0);
    if (cachedProgressive.doneCalls != 1 || !cachedProgressive.doneSuccess
        || cachedProgressive.registered() || cachedProgressivePhoto.loads != 0) {
        return 2;
    }
    auto cachedProgressiveFullPhoto = PhotoData();
    auto cachedProgressiveFullView =
        cachedProgressiveFullPhoto.createMediaView();
    if (!cachedProgressiveFullView) return 11;
    cachedProgressiveFullPhoto._largeProgressivePartReady = true;
    cachedProgressiveFullPhoto.signalProgressiveOnLoad = true;
    auto cachedProgressiveFull = PhotoPreload(&cachedProgressiveFullPhoto);
    cachedProgressiveFull.startForTest(
        0, true, PhotoPreload::Completion::Full);
    if (cachedProgressiveFull.doneCalls != 0
        || !cachedProgressiveFull.registered()
        || cachedProgressiveFullPhoto.loads != 1
        || !cachedProgressiveFullPhoto.fullLoadRequested) return 7;
    cachedProgressiveFullPhoto.isLoaded = true;
    if (!cachedProgressiveFullPhoto.photoSubscriber) return 8;
    cachedProgressiveFullPhoto.photoSubscriber(&cachedProgressiveFullPhoto);
    if (cachedProgressiveFull.doneCalls != 1
        || !cachedProgressiveFull.doneSuccess) return 9;

    auto finishedPrefixPhoto = PhotoData();
    finishedPrefixPhoto.loader.done = true;
    finishedPrefixPhoto._images[0].loader.value =
        &finishedPrefixPhoto.loader;
    auto deferredFull = PhotoPreload(&finishedPrefixPhoto);
    deferredFull.startForTest(0, true, PhotoPreload::Completion::Full);
    if (deferredFull.doneCalls != 0 || !deferredFull.registered()
        || finishedPrefixPhoto.loads != 0
        || finishedPrefixPhoto._preloadRequests != 1
        || finishedPrefixPhoto._fullPreloadRequests != 1
        || crl::pending.empty()) return 12;
    finishedPrefixPhoto._images[0].loader.value = nullptr;
    crl::runAll();
    if (finishedPrefixPhoto.loads != 1
        || !finishedPrefixPhoto.fullLoadRequested
        || finishedPrefixPhoto._preloadRequests != 1
        || finishedPrefixPhoto._fullPreloadRequests != 1) return 13;
    finishedPrefixPhoto.releasePreload(true);
    if (finishedPrefixPhoto._preloadRequests != 0
        || finishedPrefixPhoto._fullPreloadRequests != 0) return 14;

    auto fullyLoadedPhoto = PhotoData();
    fullyLoadedPhoto.isLoaded = true;
    auto fullyLoaded = PhotoPreload(&fullyLoadedPhoto);
    fullyLoaded.startForTest(0);
    if (fullyLoaded.doneCalls != 1 || !fullyLoaded.doneSuccess
        || fullyLoadedPhoto.loads != 0) {
        return 3;
    }
    return 0;
}
'''
        compile_and_run(cpp, "ayu-story-progressive-photo-preload-")

    def test_session_clear_disarms_photo_preload_before_owner_destruction(self):
        preload_source = (SOURCE / "data/data_media_preload.cpp").read_text()
        preload_header = (SOURCE / "data/data_media_preload.h").read_text()
        start = block_body(
            preload_source,
            "void PhotoPreload::start(",
        )
        clear = block_body(
            preload_source,
            "void PhotoPreload::sessionCleared()",
        )
        destructor = block_body(preload_source, "PhotoPreload::~PhotoPreload()")
        self.assertIn("rpl::lifetime _sessionLifetime;", preload_header)
        session_signal = start.index("sessionDataAboutToBeCleared()")
        session_handler = start.index("rpl::on_next([this]")
        session_callback = start.index("sessionCleared();")
        load_request = start.index(
            "if (photo->loadForPreload(origin, requireFull))")
        self.assertLess(session_signal, session_handler)
        self.assertLess(session_handler, session_callback)
        self.assertLess(
            session_callback,
            load_request,
        )
        self.assertLess(
            clear.index("_lifetime.destroy();"),
            clear.index("_registered = false;"),
        )
        self.assertLess(
            clear.index("_registered = false;"),
            clear.index("_photo->owner()->releasePreload(_requireFull);"),
        )
        self.assertLess(
            clear.index("_photo->owner()->releasePreload(_requireFull);"),
            clear.index("callDone(false);"),
        )
        self.assertIn("_sessionLifetime.destroy();", destructor)
        self.assertIn("if (_registered)", destructor)

        session_source = (SOURCE / "data/data_session.cpp").read_text()
        session_clear = block_body(session_source, "void Session::clear()")
        self.assertLess(
            session_clear.index("_sessionDataAboutToBeCleared.fire({});"),
            session_clear.index("_photos.clear();"),
        )

        cpp = r'''
#include <cstdlib>
#include <functional>
#include <memory>
#include <utility>
struct Lifetime {
    void destroy() { destroyed = true; }
    bool destroyed = false;
};
struct PhotoData {
    void releasePreload(bool requireFull) {
        if (!alive) std::abort();
        ++releaseCalls;
        --preloadRequests;
        if (requireFull) --fullPreloadRequests;
    }
    bool alive = true;
    int preloadRequests = 1;
    int fullPreloadRequests = 1;
    int releaseCalls = 0;
};
struct PhotoMedia {
    PhotoData *owner() const {
        if (!data->alive) std::abort();
        return data;
    }
    PhotoData *data;
};
struct PhotoPreload {
    void callDone(bool success) {
        ++doneCalls;
        doneSuccess = success;
        auto callback = std::exchange(onDone, {});
        if (callback) callback(success);
    }
    void sessionCleared();
    ~PhotoPreload();
    std::shared_ptr<PhotoMedia> _photo;
    Lifetime _lifetime;
    Lifetime _sessionLifetime;
    bool _registered = true;
    bool _requireFull = true;
    int doneCalls = 0;
    bool doneSuccess = true;
    std::function<void(bool)> onDone;
};
void PhotoPreload::sessionCleared() {''' + clear + r'''
}
PhotoPreload::~PhotoPreload() {''' + destructor + r'''
}
int main() {
    auto owner = PhotoData();
    auto preload = std::make_unique<PhotoPreload>();
    preload->_photo = std::make_shared<PhotoMedia>(PhotoMedia{ &owner });
    preload->sessionCleared();
    if (owner.preloadRequests != 0 || owner.releaseCalls != 1
        || preload->doneCalls != 1 || preload->doneSuccess
        || preload->_registered || !preload->_lifetime.destroyed) {
        return 1;
    }
    owner.alive = false;
    preload.reset();
    if (owner.releaseCalls != 1) return 2;

    auto reentrantOwner = PhotoData();
    auto reentrant = std::make_unique<PhotoPreload>();
    reentrant->_photo = std::make_shared<PhotoMedia>(
        PhotoMedia{ &reentrantOwner });
    reentrant->onDone = [&](bool success) {
        if (success) std::abort();
        reentrant.reset();
        reentrantOwner.alive = false;
    };
    reentrant->sessionCleared();
    return (!reentrant
        && reentrantOwner.preloadRequests == 0
        && reentrantOwner.releaseCalls == 1) ? 0 : 3;
}
'''
        compile_and_run(cpp, "ayu-photo-preload-session-clear-")

    def test_sibling_owns_lease_and_destroys_waiter_first(self):
        source = (SOURCE / "media/stories/media_stories_sibling.cpp").read_text()
        loader = block_body(source, "class Sibling::LoaderPhoto final")
        fields = loader.index("std::unique_ptr<Data::PhotoPreload> _preload;")
        waiter = loader.index("rpl::lifetime _waitingLoading;")
        constructor_start = source.index("Sibling::LoaderPhoto::LoaderPhoto(")
        constructor_end = source.index("\n}\n", constructor_start) + 2
        constructor = source[constructor_start:constructor_end]
        self.assertLess(fields, waiter)
        self.assertIn("std::make_unique<Data::PhotoPreload>(", constructor)
        self.assertIn("Fn<void()>(),\n\t\ttrue", constructor)
        self.assertNotIn("_photo->load(origin", constructor)


class StoryViewReadiness(unittest.TestCase):
    def test_increment_views_requires_a_started_view(self):
        stories_source = (SOURCE / "data/data_stories.cpp").read_text()
        mark_as_read = block_body(
            stories_source,
            "void Stories::markAsRead(FullStoryId id, bool viewed)",
        )
        cpp = r'''
#include <map>
#include <optional>
#include <set>
using PeerId = int;
using StoryId = int;
constexpr auto kIncrementViewsDelay = 1;
constexpr auto kMarkAsReadDelay = 1;
struct FullStoryId { PeerId peer; StoryId story; };
struct Session { PeerId userPeerId() const { return 1; } };
struct Owner {
    Session &session() { return _session; }
    Session _session;
};
struct GhostSettings { bool sendReadStories() const { return true; } };
namespace AyuSettings {
GhostSettings &ghost(Session *) {
    static auto result = GhostSettings();
    return result;
}
}
struct Story {
    bool expired() const { return true; }
    bool inProfile() const { return true; }
};
struct Timer {
    bool isActive() const { return active; }
    void callOnce(int) { active = true; }
    bool active = false;
};
struct Stories {
    explicit Stories(Story *story) : _story(story) {}
    void markAsRead(FullStoryId id, bool viewed) {''' + mark_as_read + r'''
    }
    std::optional<Story*> lookup(FullStoryId) const {
        return _story ? std::optional<Story*>(_story) : std::nullopt;
    }
    bool bumpReadTill(PeerId, StoryId) { ++readTillUpdates; return true; }
    void sendMarkAsReadRequests() { ++readRequests; }
    Owner _ownedOwner;
    Owner *_owner = &_ownedOwner;
    Story *_story = nullptr;
    std::map<PeerId, std::set<StoryId>> _incrementViewsPending;
    Timer _incrementViewsTimer;
    std::set<PeerId> _markReadPending;
    Timer _markReadTimer;
    int readTillUpdates = 0;
    int readRequests = 0;
};
int main() {
    auto story = Story();
    auto skipped = Stories(&story);
    skipped.markAsRead({2, 7}, false);
    if (skipped.readTillUpdates != 1
        || skipped._incrementViewsPending.contains(2)
        || skipped._incrementViewsTimer.isActive()) return 1;

    auto viewed = Stories(&story);
    viewed.markAsRead({2, 7}, true);
    viewed.markAsRead({2, 7}, true);
    if (viewed.readTillUpdates != 2
        || viewed._incrementViewsPending[2].size() != 1
        || !viewed._incrementViewsTimer.isActive()) return 2;
    return 0;
}
'''
        compile_and_run(cpp, "ayu-story-view-readiness-")


class PhotoPreloadLeaseDowngrade(unittest.TestCase):
    def test_last_full_lease_demotes_shared_loader(self):
        photo_source = (SOURCE / "data/data_photo.cpp").read_text()
        release = block_body(
            photo_source,
            "void PhotoData::releasePreload(bool requireFull)",
        )
        cpp = r'''
#include <array>
#include <cassert>
#include <cstdlib>
#include <functional>
#include <memory>
#include <utility>
namespace Data { using FileOrigin = int; }
enum class PhotoSize { Large };
enum LoadFromCloudSetting { LoadFromCloudOrLocal };
constexpr auto PhotoSizeIndex(PhotoSize) { return 0; }
namespace base {
template <typename T>
T take(T &value) { return std::exchange(value, T()); }
}
#define Expects(...) do { if (!(__VA_ARGS__)) std::abort(); } while (false)
struct Loader {
    explicit Loader(Data::FileOrigin origin, bool full = true)
    : origin(origin), full(full) {}
    Data::FileOrigin fileOrigin() const { return origin; }
    bool finished() const { return isFinished; }
    void cancel() {
        isCancelled = true;
        if (onCancel) onCancel();
    }
    Data::FileOrigin origin;
    bool full;
    bool isFinished = false;
    bool isCancelled = false;
    std::function<void()> onCancel;
};
struct Image {
    std::unique_ptr<Loader> loader;
};
struct PhotoData {
    void releasePreload(bool requireFull) {''' + release + r'''
    }
    bool cancelled() const { return isCancelled; }
    bool loading(PhotoSize) const { return isLoading; }
    void cancel(bool retryableForPreload) {
        ++cancelCalls;
        _cancelledByPreloadLease = retryableForPreload;
        isLoading = false;
        _images[0].loader.reset();
    }
    void loadInternal(
            PhotoSize,
            Data::FileOrigin origin,
            LoadFromCloudSetting,
            bool,
            bool preload,
            bool requireFull) {
        ++restartCalls;
        lastOrigin = origin;
        lastPreload = preload;
        lastRequireFull = requireFull;
        auto loader = std::make_unique<Loader>(origin, requireFull);
        loader->onCancel = [this] {
            if (_images[0].loader) _images[0].loader.reset();
        };
        _images[0].loader = std::move(loader);
        isLoading = true;
        isCancelled = false;
    }
    std::array<Image, 1> _images;
    int _preloadRequests = 0;
    int _fullPreloadRequests = 0;
    bool _regularLargeLoad = false;
    bool _cancelledByPreloadLease = false;
    bool isLoading = true;
    bool isCancelled = false;
    int restartCalls = 0;
    int cancelCalls = 0;
    Data::FileOrigin lastOrigin = 0;
    bool lastPreload = false;
    bool lastRequireFull = true;
};
void installLoader(PhotoData &photo, bool full = true, bool finished = false) {
    auto loader = std::make_unique<Loader>(42, full);
    loader->isFinished = finished;
    loader->onCancel = [&photo] {
        if (photo._images[0].loader) photo._images[0].loader.reset();
    };
    photo._images[0].loader = std::move(loader);
}
int main() {
    auto photo = PhotoData();
    photo._preloadRequests = 3;
    photo._fullPreloadRequests = 2;
    installLoader(photo);
    photo.releasePreload(true);
    if (!photo._images[0].loader
        || photo.restartCalls != 0
        || photo._preloadRequests != 2
        || photo._fullPreloadRequests != 1) return 1;

    photo.releasePreload(true);
    if (!photo._images[0].loader
        || photo._images[0].loader->full
        || photo.restartCalls != 1
        || photo.cancelCalls != 0
        || photo._preloadRequests != 1
        || photo._fullPreloadRequests != 0
        || photo.lastOrigin != 42
        || !photo.lastPreload
        || photo.lastRequireFull) return 2;

    photo.releasePreload(false);
    if (photo._images[0].loader
        || photo.cancelCalls != 1
        || photo.restartCalls != 1
        || photo._preloadRequests != 0
        || !photo._cancelledByPreloadLease) return 3;

    auto uiOwned = PhotoData();
    uiOwned._preloadRequests = 2;
    uiOwned._fullPreloadRequests = 1;
    uiOwned._regularLargeLoad = true;
    installLoader(uiOwned);
    uiOwned.releasePreload(true);
    if (!uiOwned._images[0].loader
        || !uiOwned._images[0].loader->full
        || uiOwned.restartCalls != 0
        || uiOwned.cancelCalls != 0) return 4;

    auto finished = PhotoData();
    finished._preloadRequests = 2;
    finished._fullPreloadRequests = 1;
    installLoader(finished, true, true);
    finished.releasePreload(true);
    if (!finished._images[0].loader
        || finished.restartCalls != 0
        || finished.cancelCalls != 0) return 5;
    return 0;
}
'''
        compile_and_run(cpp, "ayu-photo-preload-demotion-")


if __name__ == "__main__":
    unittest.main()
