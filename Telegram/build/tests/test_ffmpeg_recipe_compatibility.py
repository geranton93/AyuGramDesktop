import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PREPARE = ROOT / "Telegram/build/prepare/prepare.py"
DOCKERFILE = ROOT / "Telegram/build/docker/centos_env/Dockerfile"
KIMAGEFORMATS = ROOT / "cmake/external/qt/qt_static_plugins/kimageformats/CMakeLists.txt"


class FfmpegRecipeCompatibility(unittest.TestCase):
    def test_ffmpeg_version_is_pinned_to_9_0_2_in_both_recipes(self):
        for path in (PREPARE, DOCKERFILE):
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                pins = re.findall(
                    r"git clone -b (n[0-9]+\.[0-9]+\.[0-9]+)"
                    r"[^\n]*https://github\.com/FFmpeg/FFmpeg\.git",
                    source,
                )
                self.assertEqual(["n9.0.2"], pins)

    def test_windows_recipe_does_not_apply_obsolete_ffmpeg_patch(self):
        prepare = PREPARE.read_text(encoding="utf-8")
        ffmpeg_recipe = prepare.split("stage('ffmpeg',", 1)[1].split(
            "stage('libheif',", 1
        )[0]
        self.assertNotIn("depends:patches/ffmpeg.patch", ffmpeg_recipe)
        self.assertNotIn("git apply ../patches/ffmpeg.patch", ffmpeg_recipe)
        self.assertNotIn("ffmpeg_n9.patch", ffmpeg_recipe)

    def test_application_does_not_call_deprecated_packet_initializer(self):
        paths = (
            ROOT / "Telegram/SourceFiles/media/audio/media_audio_edit.cpp",
            ROOT / "Telegram/SourceFiles/ui/controls/round_video_recorder.cpp",
        )
        for path in paths:
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn(
                    "av_init_packet(",
                    source,
                    f"{path.name} still calls the deprecated FFmpeg API",
                )

    def test_stack_packets_are_value_initialized_before_reading(self):
        cases = (
            (
                ROOT / "Telegram/SourceFiles/media/audio/media_audio_edit.cpp",
                2,
                (
                    "av_read_frame(input.get(), &packet)",
                    "av_read_frame(input, &packet)",
                ),
            ),
            (
                ROOT / "Telegram/SourceFiles/ui/controls/round_video_recorder.cpp",
                1,
                ("av_read_frame(input, &packet)",),
            ),
        )
        for path, count, read_calls in cases:
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                initializers = [
                    match.start()
                    for match in re.finditer(
                        re.escape("auto packet = AVPacket();"), source
                    )
                ]
                reads = []
                for read_call in read_calls:
                    self.assertEqual(1, source.count(read_call))
                    reads.append(source.index(read_call))

                self.assertEqual(count, len(initializers))
                self.assertEqual(count, len(reads))
                for initializer, read in zip(initializers, reads):
                    self.assertLess(initializer, read)

    def test_static_kimageformats_links_ffmpeg_for_custom_libheif(self):
        source = KIMAGEFORMATS.read_text(encoding="utf-8")
        branches = (
            ("WIN32", "elseif (APPLE)"),
            ("APPLE", "endif()"),
        )
        for platform, end_marker in branches:
            with self.subTest(platform=platform):
                branch = source.split(f"elseif ({platform})", 1)[1].split(
                    end_marker, 1
                )[0]
                link_blocks = re.findall(
                    r"target_link_libraries\(external_qt_static_plugins_kimageformats"
                    r"\s+PRIVATE(.*?)\)",
                    branch,
                    re.DOTALL,
                )
                self.assertTrue(link_blocks)
                self.assertTrue(
                    any("desktop-app::external_ffmpeg" in block for block in link_blocks)
                )

    def test_apple_static_libheif_precedes_its_ffmpeg_dependencies(self):
        source = KIMAGEFORMATS.read_text(encoding="utf-8")
        branch = source.split("elseif (APPLE)", 1)[1].split("endif()", 1)[0]
        link_blocks = re.findall(
            r"target_link_libraries\(external_qt_static_plugins_kimageformats"
            r"\s+PRIVATE(.*?)\)",
            branch,
            re.DOTALL,
        )
        block = next(
            (item for item in link_blocks if "libheif.a" in item),
            None,
        )

        self.assertIsNotNone(block)
        self.assertLess(
            block.index("${libs_loc}/local/lib/libheif.a"),
            block.index("desktop-app::external_ffmpeg"),
        )


if __name__ == "__main__":
    unittest.main()
