import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]


class MacOSDeploymentTarget(unittest.TestCase):
    def test_non_xcode_swift_uses_app_minimum_or_rejects_ambiguous_arches(self):
        swift_cmake = (ROOT / "Telegram/lib_translate/CMakeLists.txt").read_text()

        self.assertIn(
            'if (NOT CMAKE_GENERATOR STREQUAL "Xcode"\n'
            "        AND CMAKE_OSX_DEPLOYMENT_TARGET)",
            swift_cmake,
        )
        self.assertIn(
            "list(LENGTH CMAKE_OSX_ARCHITECTURES swift_arch_count)",
            swift_cmake,
        )
        self.assertIn(
            "if (swift_arch_count EQUAL 0)",
            swift_cmake,
        )
        self.assertIn('set(swift_arch "${CMAKE_SYSTEM_PROCESSOR}")', swift_cmake)
        self.assertIn("elseif (swift_arch_count EQUAL 1)", swift_cmake)
        self.assertIn("message(FATAL_ERROR", swift_cmake)
        self.assertIn("target_compile_options(lib_translate PRIVATE", swift_cmake)
        self.assertIn(
            '"${swift_arch}-apple-macosx${CMAKE_OSX_DEPLOYMENT_TARGET}"',
            swift_cmake,
        )

    def test_build_and_release_metadata_match_qt_minimum(self):
        prepare = (ROOT / "Telegram/build/prepare/prepare.py").read_text()
        cmake = (ROOT / "cmake/validate_special_target.cmake").read_text()
        build = (ROOT / "Telegram/build/build.sh").read_text()
        release = (ROOT / "Telegram/build/release.py").read_text()
        plist = (ROOT / "Telegram/Telegram.plist").read_text()

        self.assertIn("'MACOSX_DEPLOYMENT_TARGET': '13.0'", prepare)
        self.assertIn("'-mmacosx-version-min=13.0'", prepare)
        self.assertIn("CMAKE_OSX_DEPLOYMENT_TARGET 13.0 CACHE STRING", cmake)
        self.assertEqual(build.count("macOS 13+"), 2)
        self.assertIn("macOS 13+: Installer", release)
        self.assertIn("<key>LSMinimumSystemVersion</key>", plist)
        self.assertIn("<string>@CMAKE_OSX_DEPLOYMENT_TARGET@</string>", plist)


if __name__ == "__main__":
    unittest.main()
