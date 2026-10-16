"""Keep cross-platform dependency source pins explicit and in sync."""
import os
import pathlib
import re
import unittest

ROOT = pathlib.Path(os.environ.get(
    "AYU_REPO_ROOT",
    pathlib.Path(__file__).resolve().parents[3],
))


class DependencyPinParity(unittest.TestCase):
    def test_openssl_latest_3x_pin_matches_prepare_and_docker_recipes(self):
        prepare = (ROOT / "Telegram/build/prepare/prepare.py").read_text()
        dockerfile = (
            ROOT / "Telegram/build/docker/centos_env/Dockerfile"
        ).read_text()
        prepare_pins = re.findall(
            r"git clone -b (openssl-[0-9.]+)",
            prepare,
        )
        docker_pins = re.findall(
            r"git clone -b (openssl-[0-9.]+)",
            dockerfile,
        )

        self.assertEqual(prepare_pins, ["openssl-3.6.5"])
        self.assertEqual(docker_pins, prepare_pins)

    def test_media_dependency_pins_match_prepare_and_docker_recipes(self):
        prepare = (ROOT / "Telegram/build/prepare/prepare.py").read_text()
        dockerfile = (
            ROOT / "Telegram/build/docker/centos_env/Dockerfile"
        ).read_text()
        patterns = {
            "XZ Utils": (
                r"git clone -b (v[0-9.]+).*https://github\.com/"
                r"tukaani-project/xz\.git",
                "v5.8.4",
            ),
            "Little CMS": (
                r"git clone -b (lcms2[0-9.]+).*https://github\.com/"
                r"mm2/Little-CMS\.git",
                "lcms2.19.1",
            ),
            "libheif": (
                r"git clone -b (v[0-9.]+).*https://github\.com/"
                r"strukturag/libheif\.git",
                "v1.23.5",
            ),
        }

        for dependency, (pattern, expected) in patterns.items():
            with self.subTest(dependency=dependency):
                prepare_pins = re.findall(pattern, prepare)
                docker_pins = re.findall(pattern, dockerfile)
                self.assertEqual(prepare_pins, [expected])
                self.assertEqual(docker_pins, prepare_pins)

    def test_msys2_installer_date_matches_archive_name(self):
        prepare = (ROOT / "Telegram/build/prepare/prepare.py").read_text()
        match = re.search(
            r"https://github\.com/msys2/msys2-installer/releases/download/"
            r"(\d{4}-\d{2}-\d{2})/msys2-base-x86_64-(\d{8})\.sfx\.exe",
            prepare,
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).replace("-", ""), match.group(2))
        self.assertEqual(match.group(1), "2026-09-27")


if __name__ == "__main__":
    unittest.main()
