"""Compile production frame timing paths and guard their speed handoffs."""
import os
import pathlib
import re
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


def compile_and_run(cpp, prefix, preserve=False):
    def run(directory):
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
            raise AssertionError(
                f"exit {result.returncode}\n{result.stdout}{result.stderr}",
            )

    if preserve:
        root = pathlib.Path(os.environ["AYU_TEST_PERSIST_TEMP_ROOT"])
        root.mkdir(parents=True, exist_ok=True)
        directory = pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=root))
        run(directory)
    else:
        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            run(directory)


class StreamingVideoSpeedTiming(unittest.TestCase):
    def test_speed_change_invalidates_old_frame_timer_until_rebase(self):
        path = SOURCE / "media/streaming/media_streaming_player.cpp"
        source = path.read_text()
        set_speed = block_body(source, "void Player::setSpeed(float64 speed)")
        check_next = block_body(source, "void Player::checkNextFrameRender()")
        check_available = block_body(
            source,
            "void Player::checkNextFrameAvailability()",
        )
        render = block_body(source, "void Player::renderFrame(crl::time now)")
        cpp = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#include <functional>
#include <limits>
using float64 = double;
namespace crl {
using time = std::int64_t;
inline time CurrentTime = 0;
time now() { return CurrentTime; }
}
namespace Media::Audio {
bool SupportsSpeedControl() { return true; }
}
inline constexpr auto kSpeedMin = 0.25;
inline constexpr auto kSpeedMax = 4.0;
inline constexpr auto kTimeUnknown = std::numeric_limits<crl::time>::min();
inline constexpr auto kFrameDisplayTimeAlreadyDone
    = std::numeric_limits<crl::time>::max();
#define Expects(...) assert(__VA_ARGS__)
#define Assert(...) assert(__VA_ARGS__)
bool EqualSpeeds(float64 a, float64 b) { return std::abs(a - b) < 0.001; }
struct Timer {
    bool active = false;
    crl::time delay = 0;
    std::function<void()> callback;
    bool isActive() const { return active; }
    void cancel() { active = false; }
    void callOnce(crl::time value) { active = true; delay = value; }
    void fire() {
        if (active) {
            active = false;
            callback();
        }
    }
};
struct VideoTrack {
    crl::time displayTime = kTimeUnknown;
    float64 queuedSpeed = 1.0;
    int displayedFrames = 0;
    crl::time nextFrameDisplayTime() const { return displayTime; }
    crl::time markFrameDisplayed(crl::time now) {
        if (now < displayTime) return kTimeUnknown;
        ++displayedFrames;
        return 42;
    }
    bool markFrameShown() { return true; }
    void setSpeed(float64 speed) { queuedSpeed = speed; }
    void addTimelineDelay(crl::time) { }
};
struct Options {
    float64 speed = 1.0;
    bool waitForMarkAsShown = false;
};
class Player {
public:
    void setSpeed(float64 speed);
    void checkNextFrameAvailability();
    void checkNextFrameRender();
    void renderFrame(crl::time now);
    bool active() const { return true; }
    void videoPlayedTill(crl::time) { ++playedFrames; }

    Options _options;
    VideoTrack *_audio = nullptr;
    VideoTrack *_video = nullptr;
    bool _paused = false;
    crl::time _nextFrameTime = kTimeUnknown;
    crl::time _currentFrameTime = kTimeUnknown;
    Timer _renderFrameTimer;
    int playedFrames = 0;
};
void Player::setSpeed(float64 speed) {''' + set_speed + r'''
}
void Player::checkNextFrameRender() {''' + check_next + r'''
}
void Player::checkNextFrameAvailability() {''' + check_available + r'''
}
void Player::renderFrame(crl::time now) {''' + render + r'''
}
int main() {
    auto track = VideoTrack();
    track.displayTime = 500;
    auto player = Player();
    player._video = &track;
    player._renderFrameTimer.callback = [&] {
        player.checkNextFrameRender();
    };
    player.checkNextFrameAvailability();
    if (!player._renderFrameTimer.active
        || player._renderFrameTimer.delay != 500) return 1;

    player.setSpeed(0.5);
    if (player._renderFrameTimer.active || track.queuedSpeed != 0.5) {
        return 2;
    }
    crl::CurrentTime = 500;
    player._renderFrameTimer.fire();
    if (player.playedFrames || track.displayedFrames) return 3;

    track.displayTime = 2000;
    player.checkNextFrameAvailability();
    if (!player._renderFrameTimer.active
        || player._renderFrameTimer.delay != 1500) return 4;
    crl::CurrentTime = 2000;
    player._renderFrameTimer.fire();
    return (player.playedFrames == 1 && track.displayedFrames == 1)
        ? 0
        : 5;
}
'''
        compile_and_run(
            cpp,
            "ayu-streaming-speed-race-",
            preserve=True,
        )

    def test_pause_holds_a_published_frame_until_resume(self):
        path = SOURCE / "media/streaming/media_streaming_player.cpp"
        source = path.read_text()
        check_next = block_body(source, "void Player::checkNextFrameRender()")
        check_available = block_body(
            source,
            "void Player::checkNextFrameAvailability()",
        )
        update_paused = block_body(source, "void Player::updatePausedState()")
        render = block_body(source, "void Player::renderFrame(crl::time now)")
        cpp = r'''
#include <cassert>
#include <cstdint>
#include <limits>
namespace crl {
using time = std::int64_t;
inline time CurrentTime = 0;
time now() { return CurrentTime; }
}
#define Expects(...) assert(__VA_ARGS__)
#define Assert(...) assert(__VA_ARGS__)
inline constexpr auto kTimeUnknown = std::numeric_limits<crl::time>::min();
inline constexpr auto kFrameDisplayTimeAlreadyDone
    = std::numeric_limits<crl::time>::max();
struct Timer {
    bool active = false;
    int cancellations = 0;
    crl::time delay = 0;
    bool isActive() const { return active; }
    void cancel() { active = false; ++cancellations; }
    void callOnce(crl::time value) { active = true; delay = value; }
};
struct VideoTrack {
    crl::time displayTime = kTimeUnknown;
    int paused = 0;
    int resumed = 0;
    int shownFrames = 0;
    crl::time nextFrameDisplayTime() const { return displayTime; }
    crl::time markFrameDisplayed(crl::time now) {
        return (now < displayTime) ? kTimeUnknown : 42;
    }
    bool markFrameShown() { ++shownFrames; return true; }
    void addTimelineDelay(crl::time) { }
    void pause(crl::time) { ++paused; }
    void resume(crl::time) { ++resumed; }
};
struct Options { bool waitForMarkAsShown = false; };
struct Weak { explicit operator bool() const { return true; } };
namespace base {
template <typename Type>
Weak make_weak(Type *) { return {}; }
}
enum class Stage { Ready, Started, Uninitialized };
class Player {
public:
    void checkNextFrameAvailability();
    void checkNextFrameRender();
    void renderFrame(crl::time now);
    void updatePausedState();
    void start() { }
    void videoPlayedTill(crl::time) { ++playedFrames; }

    bool _pausedByUser = false;
    bool _pausedByWaitingForData = false;
    bool _paused = false;
    Stage _stage = Stage::Started;
    VideoTrack *_video = nullptr;
    VideoTrack *_audio = nullptr;
    int _sessionGuard = 0;
    crl::time _pausedTime = kTimeUnknown;
    crl::time _startedTime = kTimeUnknown;
    crl::time _nextFrameTime = kTimeUnknown;
    crl::time _currentFrameTime = kTimeUnknown;
    Options _options;
    Timer _renderFrameTimer;
    int playedFrames = 0;
};
void Player::checkNextFrameRender() {''' + check_next + r'''
}
void Player::checkNextFrameAvailability() {''' + check_available + r'''
}
void Player::renderFrame(crl::time now) {''' + render + r'''
}
void Player::updatePausedState() {''' + update_paused + r'''
}
int main() {
    auto track = VideoTrack();
    track.displayTime = 1000;
    auto player = Player();
    player._video = &track;
    player._pausedByUser = true;
    player._nextFrameTime = track.displayTime;
    player._renderFrameTimer.callOnce(950);
    crl::CurrentTime = 50;
    player.updatePausedState();
    if (!player._paused
        || player._renderFrameTimer.active
        || player._renderFrameTimer.cancellations != 1
        || track.paused != 1) {
        return 1;
    }

    crl::CurrentTime = 500;
    player.checkNextFrameRender();
    if (player.playedFrames != 0
        || track.shownFrames != 0
        || player._nextFrameTime != 1000
        || player._renderFrameTimer.active) {
        return 2;
    }

    player._pausedByUser = false;
    track.displayTime = 1300;
    crl::CurrentTime = 900;
    player.updatePausedState();
    player.checkNextFrameAvailability();
    if (player._paused
        || track.resumed != 1
        || player._nextFrameTime != 1300
        || !player._renderFrameTimer.active
        || player._renderFrameTimer.delay != 400) {
        return 3;
    }

    crl::CurrentTime = 1300;
    player.checkNextFrameRender();
    return (player.playedFrames == 1 && track.shownFrames == 1) ? 0 : 4;
}
'''
        with test_temporary_directory(
                prefix="ayu-streaming-player-pause-") as directory:
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
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_video_read_timer_stops_while_paused_and_restarts_on_resume(self):
        path = SOURCE / "media/streaming/media_streaming_video_track.cpp"
        source = path.read_text()
        queue = block_body(source, "void VideoTrackObject::queueReadFrames(")
        read = block_body(source, "void VideoTrackObject::readFrames()")
        pause = block_body(source, "void VideoTrackObject::pause(crl::time time)")
        resume = block_body(source, "void VideoTrackObject::resume(crl::time time)")
        cpp = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <type_traits>
#include <utility>
#include <variant>
using float64 = double;
namespace crl {
using time = std::int64_t;
inline constexpr auto kTimeUnknown = std::numeric_limits<time>::min();
}
constexpr auto kTimeUnknown = crl::kTimeUnknown;
constexpr auto kDurationUnavailable = crl::time(-1);
#define Expects(...) assert(__VA_ARGS__)
#define Ensures(...) assert(__VA_ARGS__)
#define Assert(...) assert(__VA_ARGS__)
struct TimePoint {
    crl::time worldTime = 0;
    crl::time trackTime = 0;
    bool valid() const { return trackTime != crl::kTimeUnknown; }
};
enum class FrameResult { Done, Finished, Looped };
namespace v {
struct null_t {};
template <typename... Types>
struct Overloaded : Types... { using Types::operator()...; };
template <typename... Types>
Overloaded(Types...) -> Overloaded<Types...>;
template <typename Variant, typename... Functions>
void match(Variant &&variant, Functions &&...functions) {
    std::visit(
        Overloaded{ std::forward<Functions>(functions)... },
        std::forward<Variant>(variant));
}
template <typename... Types>
bool is_null(const std::variant<Types...> &variant) {
    return std::holds_alternative<null_t>(variant);
}
}
struct Shared {
    using PrepareNextCheck = crl::time;
    bool rebaseDisplayTime(TimePoint, float64) { return false; }
};
using ReadEnoughState = std::variant<
    v::null_t,
    FrameResult,
    Shared::PrepareNextCheck>;
struct Timer {
    bool active = false;
    int cancellations = 0;
    crl::time delay = 0;
    bool isActive() const { return active; }
    void cancel() { active = false; ++cancellations; }
    void callOnce(crl::time value) { active = true; delay = value; }
};
class VideoTrackObject;
struct Weak {
    VideoTrackObject *owner = nullptr;
    template <typename Callback>
    void with(Callback);
};
struct EventStream {
    int fires = 0;
    void fire(std::initializer_list<int>) { ++fires; }
};
struct PlaybackOptions { float64 speed = 1.; };
class VideoTrackObject {
public:
    void queueReadFrames(crl::time delay = 0);
    void readFrames();
    void pause(crl::time time);
    void resume(crl::time time);
    bool interrupted() const { return _interrupted; }
    TimePoint trackTime() const { return _syncTimePoint; }
    ReadEnoughState readEnoughFrames(crl::time) {
        ++readCalls;
        return Shared::PrepareNextCheck(75);
    }
    void presentFrameIfNeeded() { }
    crl::time computeDuration() const { return 1000; }

    Weak _weak;
    Shared shared;
    Shared *_shared = &shared;
    EventStream _checkNextFrame;
    PlaybackOptions _options;
    Timer _readFramesTimer;
    TimePoint _syncTimePoint = { 0, 0 };
    crl::time _pausedTime = crl::kTimeUnknown;
    crl::time _resumedTime = crl::kTimeUnknown;
    bool _queued = false;
    bool _interrupted = false;
    int scheduledReadCallbacks = 0;
    int readCalls = 0;
};
template <typename Callback>
void Weak::with(Callback) {
    ++owner->scheduledReadCallbacks;
}
void VideoTrackObject::queueReadFrames(crl::time delay) {''' + queue + r'''
}
void VideoTrackObject::readFrames() {''' + read + r'''
}
void VideoTrackObject::pause(crl::time time) {''' + pause + r'''
}
void VideoTrackObject::resume(crl::time time) {''' + resume + r'''
}
int main() {
    auto track = VideoTrackObject();
    track._weak.owner = &track;
    track.queueReadFrames(45);
    if (!track._readFramesTimer.active
        || track._readFramesTimer.delay != 45) {
        return 1;
    }

    track.pause(10);
    if (track._pausedTime != 10
        || track._readFramesTimer.active
        || track._readFramesTimer.cancellations != 1) {
        return 2;
    }
    track.queueReadFrames(30);
    track.readFrames();
    if (track._readFramesTimer.active || track.readCalls != 0) {
        return 3;
    }

    track.resume(50);
    if (track._pausedTime != crl::kTimeUnknown
        || track._syncTimePoint.worldTime != 40
        || track.scheduledReadCallbacks != 1) {
        return 4;
    }
    track.readFrames();
    return (track.readCalls == 1
        && track._readFramesTimer.active
        && track._readFramesTimer.delay == 75) ? 0 : 5;
}
'''
        with test_temporary_directory(
                prefix="ayu-streaming-video-read-pause-") as directory:
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
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_player_rearms_after_published_frame_deadline_update(self):
        path = SOURCE / "media/streaming/media_streaming_player.cpp"
        source = path.read_text()
        check_available = block_body(
            source,
            "void Player::checkNextFrameAvailability()",
        )
        check_next = block_body(source, "void Player::checkNextFrameRender()")
        render = block_body(source, "void Player::renderFrame(crl::time now)")
        cpp = r'''
#include <cassert>
#include <cstdint>
#include <limits>
#define Expects(...) assert(__VA_ARGS__)
#define Assert(...) assert(__VA_ARGS__)
namespace crl {
using time = std::int64_t;
inline time CurrentTime = 0;
time now() { return CurrentTime; }
}
namespace Media::Streaming {
inline constexpr auto kTimeUnknown = std::numeric_limits<crl::time>::min();
inline constexpr auto kFrameDisplayTimeAlreadyDone
    = std::numeric_limits<crl::time>::max();
struct Timer {
    bool active = false;
    int cancellations = 0;
    crl::time delay = 0;
    bool isActive() const { return active; }
    void cancel() { active = false; ++cancellations; }
    void callOnce(crl::time value) { active = true; delay = value; }
};
struct VideoTrack {
    crl::time displayTime = kTimeUnknown;
    crl::time displayTimeDuringMark = kTimeUnknown;
    bool moveDuringMark = false;
    int shownFrames = 0;
    crl::time nextFrameDisplayTime() const { return displayTime; }
    crl::time markFrameDisplayed(crl::time now) {
        if (moveDuringMark) {
            displayTime = displayTimeDuringMark;
            moveDuringMark = false;
        }
        return (now < displayTime) ? kTimeUnknown : 42;
    }
    bool markFrameShown() { ++shownFrames; return true; }
    void addTimelineDelay(crl::time) { }
};
struct Options { bool waitForMarkAsShown = false; };
class Player {
public:
    void checkNextFrameAvailability();
    void checkNextFrameRender();
    void renderFrame(crl::time now);
    void videoPlayedTill(crl::time) { ++playedFrames; }

    VideoTrack * _video = nullptr;
    bool _paused = false;
    crl::time _nextFrameTime = kTimeUnknown;
    crl::time _currentFrameTime = kTimeUnknown;
    Options _options;
    Timer _renderFrameTimer;
    int renderedFrames = 0;
    int playedFrames = 0;
};
void Player::checkNextFrameRender() {''' + check_next + r'''
}
void Player::checkNextFrameAvailability() {''' + check_available + r'''
}
void Player::renderFrame(crl::time now) {''' + render + r'''
}
}
int main() {
    using namespace Media::Streaming;
    auto video = VideoTrack();
    auto player = Player();
    player._video = &video;
    player._nextFrameTime = 1000;
    player._renderFrameTimer.callOnce(975);
    video.displayTime = 63;
    crl::CurrentTime = 25;
    player.checkNextFrameAvailability();
    if (player._nextFrameTime != 63
        || player._renderFrameTimer.delay != 38
        || player._renderFrameTimer.cancellations != 1
        || player.renderedFrames != 0) {
        return 1;
    }

    auto slowVideo = VideoTrack();
    auto slowPlayer = Player();
    slowPlayer._video = &slowVideo;
    slowPlayer._nextFrameTime = 63;
    slowPlayer._renderFrameTimer.callOnce(38);
    slowVideo.displayTime = 1000;
    crl::CurrentTime = 25;
    slowPlayer.checkNextFrameAvailability();
    if (slowPlayer._nextFrameTime != 1000
        || slowPlayer._renderFrameTimer.delay != 975
        || slowPlayer._renderFrameTimer.cancellations != 1
        || slowPlayer.renderedFrames != 0) {
        return 6;
    }

    video.displayTime = kFrameDisplayTimeAlreadyDone;
    player.checkNextFrameAvailability();
    if (player._nextFrameTime != kFrameDisplayTimeAlreadyDone
        || player._renderFrameTimer.active
        || player._renderFrameTimer.cancellations != 2) {
        return 2;
    }

    video.displayTime = 25;
    video.displayTimeDuringMark = 63;
    video.moveDuringMark = true;
    player._nextFrameTime = 25;
    player._renderFrameTimer.callOnce(0);
    player.checkNextFrameRender();
    if (player._nextFrameTime != 63
        || player._renderFrameTimer.delay != 38
        || !player._renderFrameTimer.active
        || player._renderFrameTimer.cancellations != 4
        || video.shownFrames != 0
        || player.playedFrames != 0) {
        return 3;
    }

    video.displayTime = 51;
    player._renderFrameTimer.active = false;
    player.checkNextFrameRender();
    if (player._nextFrameTime != 51
        || player._renderFrameTimer.delay != 26
        || player._renderFrameTimer.cancellations != 4
        || player.renderedFrames != 0) {
        return 4;
    }

    video.displayTime = kTimeUnknown;
    player.checkNextFrameRender();
    if (player._nextFrameTime != kTimeUnknown
        || player._renderFrameTimer.active
        || player._renderFrameTimer.cancellations != 5) {
        return 5;
    }
    return 0;
}
'''
        with test_temporary_directory(prefix="ayu-streaming-player-deadline-") as directory:
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

    def test_published_frame_event_rearms_the_player_timer(self):
        path = SOURCE / "media/streaming/media_streaming_player.cpp"
        source = path.read_text()
        start = block_body(source, "void Player::start(")
        frame_event = block_body(start, "_video->checkNextFrame(")
        self.assertIn("checkNextFrameAvailability();", frame_event)
        self.assertNotIn("checkVideoStep();", frame_event)

    def test_speed_change_rearms_an_active_frame_read(self):
        path = SOURCE / "media/streaming/media_streaming_video_track.cpp"
        source = path.read_text()
        set_speed = block_body(
            source,
            "void VideoTrackObject::setSpeed(float64 speed)",
        )
        resume = block_body(source, "void VideoTrackObject::resume(crl::time time)")
        track_time = block_body(source, "TimePoint VideoTrackObject::trackTime() const")
        add_timeline_delay = block_body(
            source,
            "void VideoTrackObject::addTimelineDelay(crl::time delayed)",
        )
        cpp = r'''
#include <cmath>
#include <cstdint>
#include <initializer_list>
using float64 = double;
namespace crl {
using time = int;
inline time CurrentTime = 0;
time now() { return CurrentTime; }
}
namespace base {
int SafeRound(double value) { return static_cast<int>(std::round(value)); }
}
constexpr int kTimeUnknown = -1;
#define Expects(...) assert(__VA_ARGS__)
#define Assert(...) assert(__VA_ARGS__)
#define Ensures(...) assert(__VA_ARGS__)
#include <cassert>
struct TimePoint {
    int worldTime = 0;
    int trackTime = kTimeUnknown;
    bool valid() const { return trackTime != -1; }
    explicit operator bool() const { return valid(); }
};
struct AudioId {
    bool external = false;
    bool externalPlayId() const { return external; }
};
inline TimePoint ExternalSyncPoint;
namespace Media::Player {
struct Mixer {
    TimePoint getExternalSyncTimePoint(const AudioId &) const {
        return ExternalSyncPoint;
    }
};
Mixer *mixer() { static auto value = Mixer(); return &value; }
}
struct PlaybackOptions {
    float64 speed = 1.;
    bool syncVideoByAudio = false;
};
struct Shared {
    int rebases = 0;
    bool rebaseResult = true;
    TimePoint lastTime;
    float64 lastSpeed = 0.;
    crl::time framePosition = 100;
    crl::time lastDeadline = kTimeUnknown;
    bool rebaseDisplayTime(TimePoint time, float64 speed) {
        ++rebases;
        lastTime = time;
        lastSpeed = speed;
        lastDeadline = time.worldTime
            + base::SafeRound((framePosition - time.trackTime) / speed);
        return rebaseResult;
    }
};
struct EventStream {
    int fires = 0;
    void fire(std::initializer_list<int>) { ++fires; }
};
struct Timer {
    bool active = false;
    int cancellations = 0;
    bool isActive() const { return active; }
    void cancel() { active = false; ++cancellations; }
};
class VideoTrackObject {
public:
    void setSpeed(float64 speed);
    void resume(crl::time time);
    TimePoint trackTime() const;
    void addTimelineDelay(crl::time delayed);
    bool interrupted() const { return wasInterrupted; }
    void queueReadFrames() { ++queuedReads; }

    bool wasInterrupted = false;
    AudioId _audioId;
    PlaybackOptions _options;
    mutable TimePoint _syncTimePoint = { 10, 20 };
    int _resumedTime = 0;
    int _pausedTime = kTimeUnknown;
    Shared shared;
    Shared *_shared = &shared;
    EventStream _checkNextFrame;
    Timer _readFramesTimer;
    int queuedReads = 0;
};
TimePoint VideoTrackObject::trackTime() const {''' + track_time + r'''
}
void VideoTrackObject::addTimelineDelay(crl::time delayed) {''' + add_timeline_delay + r'''
}
void VideoTrackObject::setSpeed(float64 speed) {''' + set_speed + r'''
}
void VideoTrackObject::resume(crl::time time) {''' + resume + r'''
}
int main() {
    crl::CurrentTime = 50;
    auto active = VideoTrackObject();
    active._readFramesTimer.active = true;
    active.setSpeed(2.);
    if (active._readFramesTimer.active
        || active._readFramesTimer.cancellations != 1
        || active.queuedReads != 1
        || active._options.speed != 2.
        || active._syncTimePoint.worldTime != 50
        || active._syncTimePoint.trackTime != 60
        || active.shared.rebases != 1
        || active.shared.lastTime.worldTime != 50
        || active.shared.lastTime.trackTime != 60
        || active.shared.lastSpeed != 2.
        || active._checkNextFrame.fires != 1) {
        return 1;
    }

    crl::CurrentTime = 50;
    auto inactive = VideoTrackObject();
    inactive.setSpeed(0.5);
    if (inactive._readFramesTimer.cancellations != 0
        || inactive.queuedReads != 0
        || inactive._options.speed != 0.5
        || inactive.shared.rebases != 1
        || inactive.shared.lastTime.worldTime != 50
        || inactive.shared.lastTime.trackTime != 60
        || inactive.shared.lastSpeed != 0.5
        || inactive._checkNextFrame.fires != 1) {
        return 2;
    }

    auto interrupted = VideoTrackObject();
    interrupted.wasInterrupted = true;
    interrupted._readFramesTimer.active = true;
    interrupted.setSpeed(3.);
    if (!interrupted._readFramesTimer.active
        || interrupted._readFramesTimer.cancellations != 0
        || interrupted.queuedReads != 0
        || interrupted._options.speed != 1.) {
        return 3;
    }

    auto resumed = VideoTrackObject();
    resumed._pausedTime = 5;
    crl::CurrentTime = 80;
    resumed.resume(80);
    if (resumed._pausedTime != kTimeUnknown
        || resumed._syncTimePoint.worldTime != 85
        || resumed.shared.rebases != 1
        || resumed.shared.lastTime.worldTime != 80
        || resumed.shared.lastTime.trackTime != 15
        || resumed._checkNextFrame.fires != 1
        || resumed.queuedReads != 1) {
        return 4;
    }

    auto pendingFast = VideoTrackObject();
    pendingFast._syncTimePoint = { 0, 0 };
    crl::CurrentTime = 10;
    pendingFast.addTimelineDelay(20);
    pendingFast.setSpeed(2.);
    if (pendingFast._syncTimePoint.worldTime != 20
        || pendingFast._syncTimePoint.trackTime != 0
        || pendingFast.shared.lastTime.worldTime != 10
        || pendingFast.shared.lastTime.trackTime != -20
        || pendingFast.shared.lastSpeed != 2.
        || pendingFast.shared.lastDeadline != 70) {
        return 5;
    }

    auto pendingSlow = VideoTrackObject();
    pendingSlow._syncTimePoint = { 0, 0 };
    crl::CurrentTime = 10;
    pendingSlow.addTimelineDelay(20);
    pendingSlow.setSpeed(0.5);
    if (pendingSlow._syncTimePoint.worldTime != 20
        || pendingSlow.shared.lastTime.trackTime != -5
        || pendingSlow.shared.lastSpeed != 0.5
        || pendingSlow.shared.lastDeadline != 220) {
        return 6;
    }

    auto pausedPending = VideoTrackObject();
    pausedPending._syncTimePoint = { 0, 0 };
    pausedPending._pausedTime = 10;
    crl::CurrentTime = 10;
    pausedPending.addTimelineDelay(20);
    pausedPending.setSpeed(2.);
    if (pausedPending._syncTimePoint.worldTime != 20
        || pausedPending.shared.rebases != 0) {
        return 7;
    }
    crl::CurrentTime = 30;
    pausedPending.resume(30);
    if (pausedPending._syncTimePoint.worldTime != 40
        || pausedPending.shared.lastTime.worldTime != 30
        || pausedPending.shared.lastTime.trackTime != -20
        || pausedPending.shared.lastSpeed != 2.
        || pausedPending.shared.lastDeadline != 90) {
        return 8;
    }

    auto pendingAudioFast = VideoTrackObject();
    pendingAudioFast._syncTimePoint = { 0, 0 };
    pendingAudioFast._options.syncVideoByAudio = true;
    pendingAudioFast._audioId.external = true;
    crl::CurrentTime = 10;
    pendingAudioFast.addTimelineDelay(20);
    ExternalSyncPoint = { 11, 0 };
    pendingAudioFast.setSpeed(2.);
    if (pendingAudioFast._syncTimePoint.worldTime != 20
        || pendingAudioFast.shared.lastTime.trackTime != -20
        || pendingAudioFast.shared.lastDeadline != 70) {
        return 9;
    }

    auto pausedAudioPending = VideoTrackObject();
    pausedAudioPending._syncTimePoint = { 0, 0 };
    pausedAudioPending._pausedTime = 10;
    pausedAudioPending._options.syncVideoByAudio = true;
    pausedAudioPending._audioId.external = true;
    crl::CurrentTime = 10;
    pausedAudioPending.addTimelineDelay(20);
    pausedAudioPending.setSpeed(2.);
    crl::CurrentTime = 30;
    ExternalSyncPoint = { 31, 10 };
    pausedAudioPending.resume(30);
    if (pausedAudioPending._syncTimePoint.worldTime != 40
        || pausedAudioPending._syncTimePoint.trackTime != 0
        || pausedAudioPending.shared.lastTime.worldTime != 30
        || pausedAudioPending.shared.lastTime.trackTime != -20
        || pausedAudioPending.shared.lastDeadline != 90) {
        return 10;
    }

    auto elapsedAudioDelay = VideoTrackObject();
    elapsedAudioDelay._syncTimePoint = { 20, 0 };
    elapsedAudioDelay._options.syncVideoByAudio = true;
    elapsedAudioDelay._audioId.external = true;
    crl::CurrentTime = 20;
    ExternalSyncPoint = { 20, 15 };
    elapsedAudioDelay.setSpeed(2.);
    if (elapsedAudioDelay._syncTimePoint.worldTime != 20
        || elapsedAudioDelay._syncTimePoint.trackTime != 15
        || elapsedAudioDelay.shared.lastTime.trackTime != 15
        || elapsedAudioDelay.shared.lastDeadline != 63) {
        return 11;
    }

    auto stoppedAudioAfterFrameDelay = VideoTrackObject();
    stoppedAudioAfterFrameDelay._syncTimePoint = { 40, 20 };
    stoppedAudioAfterFrameDelay._options.syncVideoByAudio = true;
    stoppedAudioAfterFrameDelay._audioId.external = true;
    crl::CurrentTime = 40;
    ExternalSyncPoint = { 20, 20 };
    stoppedAudioAfterFrameDelay.setSpeed(2.);
    if (stoppedAudioAfterFrameDelay._syncTimePoint.worldTime != 40
        || stoppedAudioAfterFrameDelay._syncTimePoint.trackTime != 20
        || stoppedAudioAfterFrameDelay.shared.lastTime.worldTime != 40
        || stoppedAudioAfterFrameDelay.shared.lastTime.trackTime != 20
        || stoppedAudioAfterFrameDelay.shared.lastDeadline != 80) {
        return 12;
    }
    return 0;
}
'''
        with test_temporary_directory(prefix="ayu-streaming-speed-setter-") as directory:
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

    def test_pending_frame_rebase_uses_current_track_time(self):
        path = SOURCE / "media/streaming/media_streaming_video_track.cpp"
        source = path.read_text()
        frame_time = block_body(source, "[[nodiscard]] crl::time FrameDisplayTime(")
        rebase = block_body(
            source,
            "bool VideoTrack::Shared::rebaseDisplayTime(",
        )
        mark_displayed = block_body(
            source,
            "crl::time VideoTrack::Shared::markFrameDisplayed(crl::time now)",
        )
        mark_displayed_wrapper = block_body(
            source,
            "crl::time VideoTrack::markFrameDisplayed(crl::time now)",
        )
        mark_frame_shown = block_body(
            source,
            "bool VideoTrack::Shared::markFrameShown()",
        )
        cpp = r'''
#include <array>
#include <atomic>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <functional>
#include <limits>
#include <utility>
#define Assert(...) assert(__VA_ARGS__)
#define Ensures(...) assert(__VA_ARGS__)
#define Unexpected(...) __builtin_unreachable()
using float64 = double;
namespace crl { using time = std::int64_t; }
class TestAtomicTime {
public:
    explicit TestAtomicTime(crl::time value) : _value(value) { }

    crl::time load(
            std::memory_order order = std::memory_order_seq_cst) const {
        return _value.load(order);
    }
    void store(crl::time value, std::memory_order order) {
        _value.store(value, order);
    }
    bool compare_exchange_strong(
            crl::time &expected,
            crl::time desired,
            std::memory_order success,
            std::memory_order failure) {
        if (beforeCompare) {
            auto callback = std::exchange(beforeCompare, {});
            callback();
        }
        return _value.compare_exchange_strong(
            expected,
            desired,
            success,
            failure);
    }
    bool compare_exchange_weak(
            crl::time &expected,
            crl::time desired,
            std::memory_order success,
            std::memory_order failure) {
        return _value.compare_exchange_weak(
            expected,
            desired,
            success,
            failure);
    }

    std::function<void()> beforeCompare;

private:
    std::atomic<crl::time> _value;
};
namespace base {
double SafeRound(double value) { return std::round(value); }
}
namespace Media::Streaming { struct TimePoint; struct AudioMsgId; }
namespace Media::Player { struct Mixer; Mixer *mixer(); }
namespace Media::Streaming {
inline constexpr auto kTimeUnknown = std::numeric_limits<crl::time>::min();
inline constexpr auto kFrameDisplayTimeAlreadyDone
    = std::numeric_limits<crl::time>::max();
struct TimePoint { crl::time worldTime; crl::time trackTime; };
namespace {
[[nodiscard]] crl::time FrameDisplayTime(
        TimePoint trackTime,
        crl::time position,
        float64 playbackSpeed) {''' + frame_time + r'''
}
}
struct Frame {
    crl::time position = kTimeUnknown;
    crl::time displayed = kTimeUnknown;
};
class VideoTrack {
public:
    class Shared {
    public:
        bool rebaseDisplayTime(TimePoint trackTime, float64 playbackSpeed);
        crl::time markFrameDisplayed(crl::time now);
        bool markFrameShown();
        int counter() const { return _counter.load(std::memory_order_acquire); }
        Frame *getFrame(int index) { return &_frames[index]; }

        static constexpr auto kFramesCount = 4;
        std::atomic<int> _counter = -1;
        TestAtomicTime _pendingDisplayTime{ kTimeUnknown };
        std::array<Frame, kFramesCount> _frames;
    };
    crl::time markFrameDisplayed(crl::time now);
    Shared *_shared = nullptr;
};
bool VideoTrack::Shared::rebaseDisplayTime(
        TimePoint trackTime,
        float64 playbackSpeed) {''' + rebase + r'''
}
crl::time VideoTrack::Shared::markFrameDisplayed(crl::time now) {''' + mark_displayed + r'''
}
crl::time VideoTrack::markFrameDisplayed(crl::time now) {''' + mark_displayed_wrapper + r'''
}
bool VideoTrack::Shared::markFrameShown() {''' + mark_frame_shown + r'''
}
}
int main() {
    using namespace Media::Streaming;
    if (FrameDisplayTime({ 0, 0 }, 125, 1.) + 20 != 145) {
        return 1;
    }
    auto shared = VideoTrack::Shared();
    shared._counter.store(1, std::memory_order_release);
    shared._frames[1] = { 125 };
    shared._pendingDisplayTime.store(145, std::memory_order_release);
    auto video = VideoTrack();
    video._shared = &shared;
    if (!shared.rebaseDisplayTime({ 45, 5 }, 2.)
        || shared._pendingDisplayTime.load() != 105) {
        return 1;
    }
	if (!shared.rebaseDisplayTime({ 40, 20 }, 0.5)
		|| shared._pendingDisplayTime.load() != 250) {
		return 2;
	}
	if (video.markFrameDisplayed(249) != kTimeUnknown
		|| shared._pendingDisplayTime.load() != 250
		|| shared._frames[1].displayed != kTimeUnknown) {
		return 3;
	}
	if (shared.markFrameDisplayed(249) != kTimeUnknown
		|| shared._pendingDisplayTime.load() != 250
		|| shared._frames[1].displayed != kTimeUnknown) {
		return 4;
	}
	if (shared.markFrameDisplayed(250) != 125
		|| shared._pendingDisplayTime.load() != kFrameDisplayTimeAlreadyDone
		|| shared._frames[1].displayed != 250) {
		return 5;
	}

    shared._pendingDisplayTime.store(
        kFrameDisplayTimeAlreadyDone,
        std::memory_order_release);
    if (shared.rebaseDisplayTime({ 30, 30 }, 0.5)
        || shared._pendingDisplayTime.load() != kFrameDisplayTimeAlreadyDone) {
        return 6;
    }

    shared._counter.store(2, std::memory_order_release);
    shared._pendingDisplayTime.store(85, std::memory_order_release);
    if (shared.rebaseDisplayTime({ 30, 30 }, 0.5)
        || shared._pendingDisplayTime.load() != 85) {
        return 7;
    }

    shared._counter.store(1, std::memory_order_release);
    shared._frames[1] = { 125, 125 };
    shared._pendingDisplayTime.store(145, std::memory_order_release);
    if (!shared.rebaseDisplayTime({ 45, 5 }, 2.)
        || shared._pendingDisplayTime.load() != 105) {
        return 8;
    }
    if (!shared.markFrameShown()
        || shared.counter() != 2
        || shared._pendingDisplayTime.load() != kTimeUnknown) {
        return 9;
    }

    shared._counter.store(1, std::memory_order_release);
    shared._frames[1] = { 125, 125 };
    shared._pendingDisplayTime.store(145, std::memory_order_release);
    if (!shared.markFrameShown()
        || shared.counter() != 2
        || shared._pendingDisplayTime.load() != kTimeUnknown) {
        return 10;
    }
    if (shared.rebaseDisplayTime({ 25, 5 }, 2.)
        || shared.counter() != 2
        || shared._pendingDisplayTime.load() != kTimeUnknown) {
        return 11;
    }

    shared._counter.store(1, std::memory_order_release);
    shared._frames[1] = { 125, 125 };
    shared._pendingDisplayTime.store(145, std::memory_order_release);
    auto shownDuringRebase = false;
    shared._pendingDisplayTime.beforeCompare = [&] {
        shownDuringRebase = shared.markFrameShown();
    };
    if (shared.rebaseDisplayTime({ 45, 5 }, 2.)
        || !shownDuringRebase
        || shared.counter() != 2
        || shared._pendingDisplayTime.load() != kTimeUnknown) {
        return 12;
    }

    return 0;
}
'''
        with test_temporary_directory(prefix="ayu-streaming-rebase-") as directory:
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

    def test_speed_change_rebases_pending_frame_after_timeline_delay(self):
        path = SOURCE / "media/streaming/media_streaming_video_track.cpp"
        source = path.read_text()
        frame_time = block_body(source, "[[nodiscard]] crl::time FrameDisplayTime(")
        add_delay = block_body(
            source,
            "void VideoTrackObject::addTimelineDelay(crl::time delayed)",
        )
        track_time = block_body(
            source,
            "TimePoint VideoTrackObject::trackTime() const",
        )
        rebase = block_body(
            source,
            "bool VideoTrack::Shared::rebaseDisplayTime(",
        )
        set_speed = block_body(
            source,
            "void VideoTrackObject::setSpeed(float64 speed)",
        )
        cpp = r'''
#include <array>
#include <atomic>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <initializer_list>
#include <limits>
#define Assert(...) assert(__VA_ARGS__)
#define Expects(...) assert(__VA_ARGS__)
#define Unexpected(...) __builtin_unreachable()
using float64 = double;
namespace crl {
using time = std::int64_t;
inline auto currentTime = time(10);
time now() { return currentTime; }
}
namespace base {
double SafeRound(double value) { return std::round(value); }
}
namespace Media::Streaming {
inline constexpr auto kTimeUnknown = std::numeric_limits<crl::time>::min();
inline constexpr auto kFrameDisplayTimeAlreadyDone
    = std::numeric_limits<crl::time>::max();
struct TimePoint {
    crl::time trackTime = kTimeUnknown;
    crl::time worldTime = kTimeUnknown;
    bool valid() const {
        return trackTime != kTimeUnknown && worldTime != kTimeUnknown;
    }
    explicit operator bool() const { return valid(); }
};
namespace {
[[nodiscard]] crl::time FrameDisplayTime(
        TimePoint trackTime,
        crl::time position,
        float64 playbackSpeed) {''' + frame_time + r'''
}
}
struct Frame { crl::time position = kTimeUnknown; };
struct PlaybackOptions {
    float64 speed = 1.;
    bool syncVideoByAudio = false;
};
struct AudioMsgId {
    bool externalPlayId() const { return false; }
};
}
namespace Media::Player {
struct Mixer {
    Media::Streaming::TimePoint getExternalSyncTimePoint(
            const Media::Streaming::AudioMsgId &) const { return {}; }
};
Mixer *mixer() { return nullptr; }
}
namespace Media::Streaming {
class VideoTrack {
public:
    class Shared {
    public:
        static constexpr auto kFramesCount = 4;
        bool rebaseDisplayTime(TimePoint trackTime, float64 playbackSpeed);
        int counter() const {
            return _counter.load(std::memory_order_acquire);
        }
        Frame *getFrame(int index) { return &_frames[index]; }
        std::atomic<int> _counter = -1;
        std::atomic<crl::time> _pendingDisplayTime = kTimeUnknown;
        std::array<Frame, 4> _frames;
    };
};
class Timer {
public:
    bool isActive() const { return false; }
    void cancel() { }
};
class EventStream {
public:
    void fire(std::initializer_list<int>) { ++fires; }
    int fires = 0;
};
class VideoTrackObject {
public:
    void addTimelineDelay(crl::time delayed);
    [[nodiscard]] TimePoint trackTime() const;
    void setSpeed(float64 speed);
    bool interrupted() const { return !_shared; }
    void queueReadFrames() { }
    PlaybackOptions _options;
    VideoTrack::Shared *_shared = nullptr;
    AudioMsgId _audioId;
    crl::time _pausedTime = kTimeUnknown;
    crl::time _resumedTime = 0;
    mutable TimePoint _syncTimePoint;
    EventStream _checkNextFrame;
    Timer _readFramesTimer;
};
bool VideoTrack::Shared::rebaseDisplayTime(
        TimePoint trackTime,
        float64 playbackSpeed) {''' + rebase + r'''
}
void VideoTrackObject::addTimelineDelay(crl::time delayed) {''' + add_delay + r'''
}
TimePoint VideoTrackObject::trackTime() const {''' + track_time + r'''
}
void VideoTrackObject::setSpeed(float64 speed) {''' + set_speed + r'''
}
}
int main() {
    using namespace Media::Streaming;
    auto shared = VideoTrack::Shared();
    shared._counter.store(1, std::memory_order_release);
    shared._frames[1].position = 100;
    shared._pendingDisplayTime.store(
        FrameDisplayTime({ 0, 0 }, 100, 1.) + 20,
        std::memory_order_release);
    auto video = VideoTrackObject();
    video._shared = &shared;
    video._syncTimePoint = { 0, 0 };
    video._resumedTime = 0;
    video.addTimelineDelay(20);
    if (video._syncTimePoint.worldTime != 20
        || video.trackTime().trackTime != -10) {
        return 1;
    }
    video.setSpeed(2.);
    if (video._options.speed != 2.
        || shared._pendingDisplayTime.load(std::memory_order_acquire) != 70
        || video._checkNextFrame.fires != 1) {
        return 2;
    }
    return 0;
}
'''
        with test_temporary_directory(prefix="ayu-streaming-delay-speed-") as directory:
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

    def test_frame_delays_follow_playback_speed(self):
        path = SOURCE / "media/streaming/media_streaming_video_track.cpp"
        source = path.read_text()
        # Extract the production helper itself so the arithmetic is exercised,
        # while the stubs keep this focused test independent of the Qt build.
        self.assertTrue("WorldTimeDelay(" in source, "frame-delay speed helper is missing")
        helper_signature = source.index("[[nodiscard]] crl::time WorldTimeDelay(")
        opening = source.index("{", helper_signature)
        depth = 1
        helper_end = opening + 1
        while depth:
            if source[helper_end] == "{":
                depth += 1
            elif source[helper_end] == "}":
                depth -= 1
            helper_end += 1
        helper_declaration = source[helper_signature:helper_end]
        cpp = r'''
#include <cmath>
#include <cstdint>
using float64 = double;
namespace crl { using time = std::int64_t; }
namespace base {
template <typename T>
auto SafeRound(T value) { return std::llround(value); }
}
namespace Media::Streaming {
namespace {
''' + helper_declaration + r'''
}
}
int main() {
    using Media::Streaming::WorldTimeDelay;
    return (WorldTimeDelay(100, 1.) == 100
        && WorldTimeDelay(100, 2.) == 50
        && WorldTimeDelay(100, 0.5) == 200
        && WorldTimeDelay(0, 2.) == 0
        && WorldTimeDelay(100, 0.) == 100) ? 0 : 1;
}
'''
        with test_temporary_directory(prefix="ayu-streaming-speed-") as directory:
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

        prepare_state = block_body(source, "auto VideoTrack::Shared::prepareState(")
        present_frame = block_body(source, "auto VideoTrack::Shared::presentFrame(")
        rebase_display = block_body(
            source,
            "bool VideoTrack::Shared::rebaseDisplayTime(",
        )
        mark_frame_shown = block_body(
            source,
            "bool VideoTrack::Shared::markFrameShown()",
        )
        set_speed = block_body(source, "void VideoTrackObject::setSpeed(float64 speed)")
        resume = block_body(source, "void VideoTrackObject::resume(crl::time time)")
        prepare_state = re.sub(r"\s+", " ", prepare_state)
        present_frame = re.sub(r"\s+", " ", present_frame)
        rebase_display = re.sub(r"\s+", " ", rebase_display)
        mark_frame_shown = re.sub(r"\s+", " ", mark_frame_shown)
        set_speed = re.sub(r"\s+", " ", set_speed)
        resume = re.sub(r"\s+", " ", resume)
        self.assertIn(
            "WorldTimeDelay( frame->position - trackTime + 1, playbackSpeed)",
            prepare_state,
        )
        self.assertIn("WorldTimeDelay(delay, playbackSpeed)", present_frame)
        self.assertIn(
            "FrameDisplayTime( time, position, playbackSpeed) + addedWorldTimeDelay",
            present_frame,
        )
        self.assertNotIn("_pendingDisplayTimeDelay", rebase_display)
        self.assertLess(
            present_frame.index("_pendingDisplayTime.store("),
            present_frame.index("_counter.store("),
        )
        self.assertLess(
            mark_frame_shown.index("_pendingDisplayTime.store(kTimeUnknown"),
            mark_frame_shown.index("_counter.store("),
        )
        self.assertIn("rebaseDisplayTime(time, speed)", set_speed)
        self.assertIn("_checkNextFrame.fire({})", set_speed)
        self.assertIn("rebaseDisplayTime(trackTime(), _options.speed)", resume)


    def test_speed_change_retargets_pending_frame_timer(self):
        track_path = SOURCE / "media/streaming/media_streaming_video_track.cpp"
        track = track_path.read_text()
        set_speed = block_body(
            track, "void VideoTrackObject::setSpeed(float64 speed)")
        object_check = block_body(
            track, "rpl::producer<> VideoTrackObject::checkNextFrame() const")
        track_check = block_body(
            track, "rpl::producer<> VideoTrack::checkNextFrame() const")
        self.assertRegex(
            set_speed,
            r"if \(_shared->rebaseDisplayTime\(time, speed\)\) \{\s*_checkNextFrame\.fire\(\{\}\);",
        )
        self.assertIn("_checkNextFrame.events()", object_check)
        self.assertIn("_wrapped.producer_on_main(", track_check)
        self.assertIn("unwrapped.checkNextFrame()", track_check)

        player_path = SOURCE / "media/streaming/media_streaming_player.cpp"
        player = player_path.read_text()
        start = block_body(player, "void Player::start(")
        callback = block_body(start, "_video->checkNextFrame(")
        availability = block_body(
            player, "void Player::checkNextFrameAvailability()")
        self.assertIn("checkNextFrameAvailability();", callback)
        self.assertIn("_video->nextFrameDisplayTime()", availability)
        self.assertIn("_renderFrameTimer.cancel();", availability)
        self.assertIn("checkNextFrameRender();", availability)


if __name__ == "__main__":
    unittest.main()
