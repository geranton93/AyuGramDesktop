"""Keep explicit attached-video saves retryable after a failed download."""
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


def compile_and_run(cpp):
    with test_temporary_directory(
        prefix="ayu-photo-video-save-retry-",
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
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)


class PhotoVideoSaveRetry(unittest.TestCase):
    def test_video_cover_tracks_message_and_retries_failed_image(self):
        overlay = (SOURCE / "media/view/media_view_overlay_widget.cpp").read_text()
        assign_document = block_body(
            overlay,
            "void OverlayWidget::assignMediaPointer(DocumentData *document)",
        )
        document_change = block_body(assign_document, "if (documentChanged)")
        self.assertNotIn("LookupVideoCover", document_change)
        self.assertIn(
            "const auto cover = LookupVideoCover(_document, _message);",
            assign_document,
        )
        self.assertIn(
            "const auto documentChanged = (_document != document);",
            assign_document,
        )
        self.assertIn("if (_videoCover != cover)", assign_document)

        changed_cover = block_body(assign_document, "if (_videoCover != cover)")
        self.assertIn("cover->createMediaView()", changed_cover)
        self.assertIn("ClearPhotoFailuresForViewing(_videoCover)", changed_cover)
        self.assertIn("RetryFailedPhotoLoadsForViewing(", changed_cover)
        self.assertIn(
            "_videoCoverMedia->wanted(\n"
            "\t\t\t\tData::PhotoSize::Large,",
            changed_cover,
        )

        same_cover = block_body(
            assign_document,
            "else if (_videoCover && _videoCoverMedia)",
        )
        self.assertIn("ClearPhotoFailuresForViewing(_videoCover)", same_cover)
        self.assertIn("if (failedLoads.large)", same_cover)
        self.assertIn("_videoCoverMedia->wanted(", same_cover)

        no_cover_on_document_change = block_body(
            assign_document,
            "else if (documentChanged)",
        )
        self.assertIn("_documentMedia->goodThumbnailWanted();", no_cover_on_document_change)
        self.assertIn("_documentMedia->thumbnailWanted(fileOrigin());", no_cover_on_document_change)

        open_request = block_body(
            overlay,
            "void OverlayWidget::show(OpenRequest request)",
        )
        self.assertLess(
            open_request.index("setContext(ItemContext{"),
            open_request.index("displayDocument(\n"),
        )

    def test_only_explicit_save_paths_request_retry(self):
        header = (SOURCE / "data/data_photo.h").read_text()
        self.assertIn(
            "void loadVideo(\n"
            "\t\tData::PhotoSize size,\n"
            "\t\tData::FileOrigin origin,\n"
            "\t\tbool retryFailed = false);",
            header,
        )

        overlay = (SOURCE / "media/view/media_view_overlay_widget.cpp").read_text()
        save_as = block_body(overlay, "void OverlayWidget::saveAs()")
        quick_save = block_body(overlay, "void OverlayWidget::downloadMedia()")
        self.assertIn("loadVideo(large, fileOrigin(), true)", save_as)
        self.assertIn(
            "loadVideo(Data::PhotoSize::Large, fileOrigin(), true)",
            quick_save,
        )

        photo_source = (SOURCE / "data/data_photo.cpp").read_text()
        update_images = block_body(
            photo_source,
            "void PhotoData::updateImages(",
        )
        self.assertEqual(update_images.count("loadVideo("), 2)
        self.assertNotRegex(
            update_images,
            r"loadVideo\([^;]*,\s*true\s*\)",
        )

        media = (SOURCE / "data/data_photo_media.cpp").read_text()
        video_wanted = block_body(media, "void PhotoMedia::videoWanted(")
        self.assertIn("_owner->loadVideo(size, origin);", video_wanted)
        self.assertNotIn("retryFailed", video_wanted)
        self.assertNotRegex(video_wanted, r"loadVideo\([^;]*,\s*true\s*\)")

    def test_quick_save_sanitizes_document_filename(self):
        overlay = (SOURCE / "media/view/media_view_overlay_widget.cpp").read_text()
        quick_save = block_body(overlay, "void OverlayWidget::downloadMedia()")
        call_start = quick_save.index("filedialogNextFilename(")
        call_end = quick_save.index(");", call_start)
        call = quick_save[call_start:call_end]
        first_argument = call[len("filedialogNextFilename("):].split(",", 1)[0].strip()
        self.assertEqual(
            first_argument,
            "base::FileNameFromUserString(_document->filename())",
        )

    def test_failed_flag_is_cleared_only_for_explicit_retry_before_load(self):
        photo_source = (SOURCE / "data/data_photo.cpp").read_text()
        load_video = block_body(photo_source, "void PhotoData::loadVideo(")
        self.assertIn("if (retryFailed)", load_video)
        self.assertIn(
            "file.flags &= ~Data::CloudFile::Flag::Failed;",
            load_video,
        )
        self.assertLess(
            load_video.index("file.flags &= ~Data::CloudFile::Flag::Failed;"),
            load_video.index("Data::LoadCloudFile("),
        )
        self.assertIn("auto &file = videoFile(size);", load_video)
        self.assertIn("\n\t\tfile,", load_video)

        cpp = r'''
#include <cstdint>
#include <memory>
#include <utility>
struct QByteArray {
    bool empty = true;
    bool isEmpty() const { return empty; }
};
namespace Main {
struct Session {};
}
namespace Data {
enum class PhotoSize { Small, Large };
struct FileOrigin {};
enum class LoadFromCloudSetting { LocalOrCloud };
inline constexpr auto LoadFromCloudOrLocal = LoadFromCloudSetting::LocalOrCloud;
inline constexpr auto kAnimationCacheTag = std::uint8_t(1);
struct CloudFile {
    enum class Flag : unsigned {
        Cancelled = 0x01,
        Failed = 0x02,
        Loaded = 0x04,
    };
    struct Flags {
        unsigned value = 0;
        Flags &operator&=(unsigned mask) {
            value &= mask;
            return *this;
        }
    } flags;
};
constexpr unsigned operator~(CloudFile::Flag flag) {
    return ~static_cast<unsigned>(flag);
}
inline int startedLoads = 0;
inline int rejectedLoads = 0;
inline unsigned flagsAtLoad = 0;
template <typename Check, typename Done>
void LoadCloudFile(
        Main::Session *,
        CloudFile &file,
        FileOrigin,
        LoadFromCloudSetting,
        bool,
        std::uint8_t,
        Check &&,
        Done &&) {
    flagsAtLoad = file.flags.value;
    if (flagsAtLoad & static_cast<unsigned>(CloudFile::Flag::Failed)) {
        ++rejectedLoads;
    } else {
        ++startedLoads;
    }
}
}
using Data::LoadFromCloudOrLocal;
struct MediaView {
    QByteArray videoContent(Data::PhotoSize) const { return {}; }
    void setVideo(Data::PhotoSize, QByteArray) { }
};
class PhotoData {
public:
    struct VideoSizes {
        Data::CloudFile small;
        Data::CloudFile large;
    };
    void loadVideo(
        Data::PhotoSize size,
        Data::FileOrigin origin,
        bool retryFailed = false);
    Data::CloudFile &videoFile(Data::PhotoSize size) {
        return (size == Data::PhotoSize::Large)
            ? _videoSizes->large
            : _videoSizes->small;
    }
    Main::Session &session() const {
        static auto value = Main::Session();
        return value;
    }
    MediaView *activeMediaView() const { return nullptr; }
    std::unique_ptr<VideoSizes> _videoSizes;
};
void PhotoData::loadVideo(
        Data::PhotoSize size,
        Data::FileOrigin origin,
        bool retryFailed) {''' + load_video + r'''
}

int main() {
    constexpr auto failed = static_cast<unsigned>(Data::CloudFile::Flag::Failed);
    constexpr auto cancelled = static_cast<unsigned>(Data::CloudFile::Flag::Cancelled);
    constexpr auto loaded = static_cast<unsigned>(Data::CloudFile::Flag::Loaded);

    auto automaticLoad = PhotoData();
    automaticLoad._videoSizes = std::make_unique<PhotoData::VideoSizes>();
    automaticLoad._videoSizes->large.flags.value = failed | cancelled | loaded;
    automaticLoad.loadVideo(Data::PhotoSize::Large, {});
    if (Data::startedLoads != 0
        || Data::rejectedLoads != 1
        || Data::flagsAtLoad != (failed | cancelled | loaded)) {
        return 1;
    }

    auto explicitSave = PhotoData();
    explicitSave._videoSizes = std::make_unique<PhotoData::VideoSizes>();
    explicitSave._videoSizes->large.flags.value = failed | cancelled | loaded;
    explicitSave.loadVideo(Data::PhotoSize::Large, {}, true);
    if (Data::startedLoads != 1
        || Data::rejectedLoads != 1
        || Data::flagsAtLoad != (cancelled | loaded)) {
        return 2;
    }

    auto absentVideo = PhotoData();
    absentVideo.loadVideo(Data::PhotoSize::Large, {}, true);
    if (Data::startedLoads != 1 || Data::rejectedLoads != 1) {
        return 3;
    }
    return 0;
}
'''
        compile_and_run(cpp)


    def test_explicit_photo_view_retries_failed_images(self):
        overlay = (SOURCE / "media/view/media_view_overlay_widget.cpp").read_text()
        clear_failures = block_body(
            overlay,
            "FailedPhotoLoads ClearPhotoFailuresForViewing(",
        )
        self.assertIn("photo->failed(Data::PhotoSize::Small)", clear_failures)
        self.assertIn("photo->failed(Data::PhotoSize::Large)", clear_failures)
        self.assertIn(
            "photo->clearFailed(Data::PhotoSize::Small);",
            clear_failures,
        )
        self.assertIn(
            "photo->clearFailed(Data::PhotoSize::Large);",
            clear_failures,
        )

        retry_failures = block_body(
            overlay,
            "void RetryFailedPhotoLoadsForViewing(",
        )
        self.assertIn(
            "media->wanted(Data::PhotoSize::Small, origin);",
            retry_failures,
        )
        self.assertIn(
            "photo->load(origin, LoadFromCloudOrLocal, true);",
            retry_failures,
        )
        self.assertIn(
            "(!photo->hasVideo() || photo->videoPlaybackFailed())",
            retry_failures,
        )

        open_request = block_body(
            overlay,
            "void OverlayWidget::show(OpenRequest request)",
        )
        self.assertIn(
            "const auto failedLoads = ClearPhotoFailuresForViewing(photo);",
            open_request,
        )
        self.assertIn("if (!photoChanged)", open_request)
        self.assertIn("RetryFailedPhotoLoadsForViewing(", open_request)
        self.assertLess(
            open_request.index("ClearPhotoFailuresForViewing(photo);"),
            open_request.index("assignMediaPointer(photo);"),
        )

        story_jump = block_body(overlay, "void OverlayWidget::storiesJumpTo(")
        self.assertIn(
            "const auto failedLoads = ClearPhotoFailuresForViewing(photo);",
            story_jump,
        )
        self.assertIn("if (_photo == photo)", story_jump)
        self.assertIn("RetryFailedPhotoLoadsForViewing(", story_jump)
        self.assertLess(
            story_jump.index("RetryFailedPhotoLoadsForViewing("),
            story_jump.index("displayPhoto(photo"),
        )

        gallery_jump = block_body(overlay, "bool OverlayWidget::moveToEntity(")
        self.assertIn(
            "const auto failedLoads = ClearPhotoFailuresForViewing(*photo);",
            gallery_jump,
        )
        self.assertIn("if (_photo == *photo)", gallery_jump)
        self.assertIn("RetryFailedPhotoLoadsForViewing(", gallery_jump)
        self.assertLess(
            gallery_jump.index("RetryFailedPhotoLoadsForViewing("),
            gallery_jump.index("displayPhoto(*photo);"),
        )

        for passive_path in (
            "void OverlayWidget::displayPhoto(",
            "void OverlayWidget::storiesRedisplay(",
            "void OverlayWidget::preloadData(",
        ):
            body = block_body(overlay, passive_path)
            self.assertNotIn("ClearPhotoFailuresForViewing", body)
            self.assertNotIn("RetryFailedPhotoLoadsForViewing", body)

        preload = (SOURCE / "data/data_media_preload.cpp").read_text()
        photo_preload = block_body(preload, "void PhotoPreload::start(")
        self.assertIn("photo->failed(PhotoSize::Large)", photo_preload)

        cpp = r'''
#include <cstdint>
namespace Data {
enum class PhotoSize { Small, Large };
struct FileOrigin {};
enum class LoadFromCloudSetting { LocalOrCloud };
inline constexpr auto LoadFromCloudOrLocal = LoadFromCloudSetting::LocalOrCloud;
struct PhotoMedia {
    int smallRequests = 0;
    void wanted(PhotoSize size, FileOrigin) {
        if (size == PhotoSize::Small) {
            ++smallRequests;
        }
    }
};
}
using Data::LoadFromCloudOrLocal;
template <typename T>
using not_null = T;
struct FailedPhotoLoads {
    bool small = false;
    bool large = false;
};
struct PhotoData {
    enum : unsigned { Failed = 0x01, Loaded = 0x02 };
    unsigned small = Failed | Loaded;
    unsigned large = Failed | Loaded;
    bool video = false;
    bool playbackFailed = false;
    int clearCalls = 0;
    int largeRequests = 0;
    bool autoLoading = false;
    bool failed(Data::PhotoSize size) const {
        const auto flags = (size == Data::PhotoSize::Small) ? small : large;
        return (flags & Failed) != 0;
    }
    void clearFailed(Data::PhotoSize size) {
        ++clearCalls;
        auto &flags = (size == Data::PhotoSize::Small) ? small : large;
        flags &= ~Failed;
    }
    bool hasVideo() const { return video; }
    bool videoPlaybackFailed() const { return playbackFailed; }
    void load(
            Data::FileOrigin,
            Data::LoadFromCloudSetting,
            bool automatic) {
        ++largeRequests;
        autoLoading = automatic;
    }
};
'''
        cpp += (
            "\nFailedPhotoLoads ClearPhotoFailuresForViewing("
            "not_null<PhotoData*> photo) {\n"
            + clear_failures
            + "\n}\n"
        )
        cpp += (
            "\nvoid RetryFailedPhotoLoadsForViewing("
            "not_null<PhotoData*> photo, "
            "not_null<Data::PhotoMedia*> media, "
            "Data::FileOrigin origin, FailedPhotoLoads failed) {\n"
            + retry_failures
            + "\n}\n"
        )
        cpp += r'''
int main() {
    auto photo = PhotoData();
    auto media = Data::PhotoMedia();
    const auto failed = ClearPhotoFailuresForViewing(&photo);
    if (!failed.small || !failed.large
        || photo.small != PhotoData::Loaded
        || photo.large != PhotoData::Loaded) {
        return 1;
    }
    RetryFailedPhotoLoadsForViewing(&photo, &media, {}, failed);
    if (media.smallRequests != 1
        || photo.largeRequests != 1
        || !photo.autoLoading) {
        return 2;
    }

    auto videoPhoto = PhotoData();
    auto videoMedia = Data::PhotoMedia();
    videoPhoto.video = true;
    const auto videoFailed = ClearPhotoFailuresForViewing(&videoPhoto);
    RetryFailedPhotoLoadsForViewing(
        &videoPhoto,
        &videoMedia,
        {},
        videoFailed);
    if (videoMedia.smallRequests != 1 || videoPhoto.largeRequests != 0) {
        return 3;
    }
    return 0;
}
'''
        compile_and_run(cpp)
