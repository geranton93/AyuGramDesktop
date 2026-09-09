#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "generate_update_feed.py"
REPOSITORY = "geranton93/AyuGramDesktop"
PLATFORMS = {
    "armac": "td-update-mac-arm-7002007",
    "linux": "td-update-linux-x64-7002007",
    "mac": "td-update-mac-x64-7002007",
    "win": "td-update-win-x86-7002007",
    "win64": "td-update-win-x64-7002007",
    "winarm": "td-update-win-arm-7002007",
}


class GenerateUpdateFeedTest(unittest.TestCase):
    def run_generator(
        self,
        directory,
        output,
        *,
        channel="stable",
        base=7002007,
        tag="v7.2.7",
        repository=REPOSITORY,
        assets=PLATFORMS,
        existing=None,
    ):
        command = [
            sys.executable,
            str(SCRIPT),
            "--output",
            str(output),
            "--repo",
            repository,
            "--tag",
            tag,
            "--base",
            str(base),
            "--channel",
            channel,
        ]
        if existing is not None:
            command.extend(["--existing", str(existing)])
        for platform, filename in assets.items():
            command.extend(["--asset", f"{platform}={filename}"])
        return subprocess.run(
            command,
            cwd=directory,
            capture_output=True,
            text=True,
        )

    def test_generates_complete_stable_feed_with_fork_release_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output = directory / "current6"

            result = self.run_generator(directory, output)

            self.assertEqual(result.returncode, 0, result.stderr)
            feed = json.loads(output.read_text())
            self.assertEqual(set(feed), set(PLATFORMS))
            for platform, filename in PLATFORMS.items():
                self.assertEqual(
                    feed[platform]["stable"],
                    {
                        "link": (
                            "https://github.com/geranton93/AyuGramDesktop/"
                            f"releases/download/v7.2.7/{filename}"
                        ),
                        "released": 7002007,
                    },
                )
                self.assertEqual(feed[platform]["beta"], {})

    def test_beta_update_preserves_the_stable_channel(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            feed = directory / "current6"
            beta_output = directory / "current6.beta"

            stable = self.run_generator(directory, feed)
            self.assertEqual(stable.returncode, 0, stable.stderr)

            beta_assets = {
                platform: filename.replace("7002007", "7002008")
                for platform, filename in PLATFORMS.items()
            }
            beta = self.run_generator(
                directory,
                beta_output,
                channel="beta",
                base=7002008,
                tag="v7.2.8-beta",
                assets=beta_assets,
                existing=feed,
            )

            self.assertEqual(beta.returncode, 0, beta.stderr)
            result = json.loads(beta_output.read_text())
            for platform, stable_filename in PLATFORMS.items():
                self.assertEqual(
                    result[platform]["stable"]["released"],
                    7002007,
                )
                self.assertTrue(
                    result[platform]["stable"]["link"].endswith(
                        f"/v7.2.7/{stable_filename}"
                    )
                )
                self.assertEqual(
                    result[platform]["beta"]["released"],
                    7002008,
                )

    def test_refuses_a_downgrade_and_leaves_existing_output_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            feed = directory / "current6"
            stable = self.run_generator(directory, feed)
            self.assertEqual(stable.returncode, 0, stable.stderr)
            before = feed.read_bytes()

            downgrade = self.run_generator(
                directory,
                feed,
                base=7002006,
                assets={
                    platform: filename.replace("7002007", "7002006")
                    for platform, filename in PLATFORMS.items()
                },
                existing=feed,
            )

            self.assertNotEqual(downgrade.returncode, 0)
            self.assertIn("lower", downgrade.stderr.lower())
            self.assertEqual(feed.read_bytes(), before)

    def test_same_release_is_idempotent_but_repointing_a_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            feed = directory / "current6"
            stable = self.run_generator(directory, feed)
            self.assertEqual(stable.returncode, 0, stable.stderr)
            before = feed.read_bytes()

            repeat = self.run_generator(
                directory,
                feed,
                existing=feed,
            )
            self.assertEqual(repeat.returncode, 0, repeat.stderr)
            self.assertEqual(feed.read_bytes(), before)

            repointed_assets = dict(PLATFORMS)
            repointed_assets["win64"] = "td-update-win-x64-repointed-7002007"
            repointed = self.run_generator(
                directory,
                feed,
                assets=repointed_assets,
                existing=feed,
            )
            self.assertNotEqual(repointed.returncode, 0)
            self.assertIn("different Release URL", repointed.stderr)
            self.assertEqual(feed.read_bytes(), before)

    def test_rejects_wrong_repository_and_unsafe_asset_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            wrong_repository = self.run_generator(
                directory,
                directory / "wrong-repository",
                repository="AyuGram/AyuGramDesktop",
            )
            self.assertNotEqual(wrong_repository.returncode, 0)
            self.assertIn("repository", wrong_repository.stderr.lower())

            unsafe_assets = dict(PLATFORMS)
            unsafe_assets["win64"] = "../td-update-win-x64-7002007"
            unsafe = self.run_generator(
                directory,
                directory / "unsafe",
                assets=unsafe_assets,
            )
            self.assertNotEqual(unsafe.returncode, 0)
            self.assertIn("asset", unsafe.stderr.lower())

    def test_rejects_duplicate_or_missing_platforms(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output = directory / "feed"
            duplicate = self.run_generator(directory, output)
            duplicate_command = [
                sys.executable,
                str(SCRIPT),
                "--output",
                str(output),
                "--repo",
                REPOSITORY,
                "--tag",
                "v7.2.7",
                "--base",
                "7002007",
                "--channel",
                "stable",
                *[
                    argument
                    for platform, filename in PLATFORMS.items()
                    for argument in ("--asset", f"{platform}={filename}")
                ],
                "--asset",
                "win64=td-update-win-x64-duplicate-7002007",
            ]
            duplicate = subprocess.run(
                duplicate_command,
                cwd=directory,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("duplicate", duplicate.stderr.lower())

            missing_assets = dict(PLATFORMS)
            del missing_assets["linux"]
            missing = self.run_generator(
                directory,
                output,
                assets=missing_assets,
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("missing", missing.stderr.lower())

    def test_output_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first = directory / "first"
            second = directory / "second"

            first_result = self.run_generator(directory, first)
            second_result = self.run_generator(directory, second)

            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            self.assertEqual(first.read_bytes(), second.read_bytes())


if __name__ == "__main__":
    unittest.main()
