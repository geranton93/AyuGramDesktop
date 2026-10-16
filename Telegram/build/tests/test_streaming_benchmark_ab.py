from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
import io
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock


TEST_ROOT = pathlib.Path(__file__).resolve().parent
SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[3] / "Telegram/SourceFiles"
sys.path.insert(0, str(TEST_ROOT))
import benchmark_streaming_ab as benchmark_ab


def summary(**overrides):
    result = {
        name: {"p95": 100.0}
        for name in benchmark_ab.SCORING_METRICS
    }
    result.update(overrides)
    return result


def paired_metric_coverage(available_pairs=20):
    return {
        name: {"available_pairs": available_pairs}
        for name in benchmark_ab.SCORING_METRICS
    }


def observation_windows(valid=True):
    return {
        "applicable": True,
        "valid": valid,
        "mismatched_pairs": [] if valid else [1],
    }


def compare(
        baseline,
        candidate,
        pair_count=20,
        paired_metrics=None,
        windows=None,
):
    return benchmark_ab.compare_summaries(
        baseline,
        candidate,
        pair_count,
        paired_metrics or paired_metric_coverage(pair_count),
        windows or observation_windows(),
    )


def create_harness_sources(root):
    for relative in (
        "tests/streaming_benchmark.cpp",
        "tests/streaming_benchmark_stubs.cpp",
        "tests/streaming_benchmark_pch.h",
        "data/data_audio_msg_id.cpp",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")


class StreamingBenchmarkAb(unittest.TestCase):
    def test_source_digest_covers_streaming_headers_and_benchmark_sources(self):
        with tempfile.TemporaryDirectory() as root:
            source_root = pathlib.Path(root)
            reader = source_root / "media/streaming/media_streaming_reader.cpp"
            reader.parent.mkdir(parents=True)
            reader.write_text("reader implementation", encoding="utf-8")
            player = reader.parent / "media_streaming_player.cpp"
            player.write_text("player implementation", encoding="utf-8")
            track = reader.parent / "media_streaming_video_track.cpp"
            track.write_text("track implementation", encoding="utf-8")
            header = reader.parent / "media_streaming_reader.h"
            header.write_text("reader declaration v1", encoding="utf-8")
            create_harness_sources(source_root)

            manifest = benchmark_ab.source_files(source_root)
            self.assertIn("media/streaming/media_streaming_reader.h", manifest)
            self.assertIn("tests/streaming_benchmark.cpp", manifest)
            original = benchmark_ab.source_sha256(source_root)
            header.write_text("reader declaration v2", encoding="utf-8")

            self.assertNotEqual(original, benchmark_ab.source_sha256(source_root))

    def test_source_digest_uses_the_build_checkout_for_benchmark_harness(self):
        with tempfile.TemporaryDirectory() as root:
            source_root = pathlib.Path(root) / "streaming-source"
            harness_root = pathlib.Path(root) / "build-source"
            streaming = source_root / "media/streaming"
            streaming.mkdir(parents=True)
            (streaming / "media_streaming_reader.cpp").write_text(
                "reader implementation",
                encoding="utf-8",
            )
            create_harness_sources(harness_root)
            harness = harness_root / "tests/streaming_benchmark.cpp"
            harness.write_text("benchmark implementation v1", encoding="utf-8")

            original = benchmark_ab.source_sha256(source_root, harness_root)
            harness.write_text("benchmark implementation v2", encoding="utf-8")

            self.assertIn(
                "tests/streaming_benchmark.cpp",
                benchmark_ab.source_files(source_root, harness_root),
            )
            self.assertNotEqual(
                original,
                benchmark_ab.source_sha256(source_root, harness_root),
            )

    def test_non_null_source_root_without_streaming_files_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "no streaming sources"):
                benchmark_ab.source_sha256(
                    pathlib.Path(root),
                    SOURCE_ROOT,
                )

    def test_invalid_source_root_does_not_reserve_an_output_report(self):
        with tempfile.TemporaryDirectory() as root:
            temporary_root = pathlib.Path(root)
            baseline = temporary_root / "baseline"
            candidate = temporary_root / "candidate"
            fixture = temporary_root / "fixture.mp4"
            artifact_root = temporary_root / "artifacts"
            artifact_root.mkdir()
            output = artifact_root / "report.json"
            invalid_source_root = temporary_root / "wrong-source-root"
            invalid_source_root.mkdir()
            baseline.write_bytes(b"baseline")
            candidate.write_bytes(b"candidate")
            with fixture.open("wb") as target:
                target.truncate(benchmark_ab.streaming.MIN_FIXTURE_BYTES + 1)

            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    benchmark_ab.main([
                        "--baseline-exe", str(baseline),
                        "--candidate-exe", str(candidate),
                        "--fixture", str(fixture),
                        "--baseline-source-root", str(invalid_source_root),
                        "--artifact-root", str(artifact_root),
                        "--output", str(output),
                    ])

            self.assertFalse(output.exists())

    def test_pair_order_alternates_to_balance_drift(self):
        self.assertEqual(
            ("baseline", "candidate"),
            benchmark_ab.pair_order(0),
        )
        self.assertEqual(
            ("candidate", "baseline"),
            benchmark_ab.pair_order(1),
        )
        self.assertEqual(
            benchmark_ab.pair_order(0),
            benchmark_ab.pair_order(2),
        )

    def test_accepts_ten_percent_improvement_and_exact_five_percent_guardrail(self):
        baseline = summary()
        candidate = summary(
            first_decoded_image_ms={"p95": 90.0},
            video_wait_ms={"p95": 105.0},
        )

        result = compare(baseline, candidate)

        self.assertTrue(result["accepted"])
        self.assertEqual(
            ["first_decoded_image_ms"],
            result["improved_metrics"],
        )
        self.assertEqual([], result["regressed_metrics"])

    def test_rejects_regression_above_five_percent(self):
        result = compare(
            summary(),
            summary(
                first_decoded_image_ms={"p95": 90.0},
                video_wait_ms={"p95": 105.01},
            ),
        )

        self.assertFalse(result["accepted"])
        self.assertEqual(["video_wait_ms"], result["regressed_metrics"])

    def test_rejects_candidate_without_ten_percent_improvement(self):
        result = compare(
            summary(),
            summary(first_decoded_image_ms={"p95": 99.0}),
        )

        self.assertFalse(result["accepted"])
        self.assertEqual(
            "no measured metric improved by at least 10 percent",
            result["reason"],
        )

    def test_rejects_fewer_than_twenty_pairs(self):
        result = compare(
            summary(),
            summary(first_decoded_image_ms={"p95": 90.0}),
            19,
        )

        self.assertFalse(result["accepted"])
        self.assertEqual("insufficient paired samples", result["reason"])

    def test_rejects_unavailable_guardrail_metrics(self):
        candidate = summary()
        candidate["video_wait_ms"] = None

        result = compare(summary(), candidate)

        self.assertFalse(result["accepted"])
        self.assertEqual(["video_wait_ms"], result["unavailable_metrics"])

    def test_pair_deltas_require_matching_sample_counts(self):
        with self.assertRaises(ValueError):
            benchmark_ab.paired_deltas({
                "baseline": [{"first_decoded_image_ms": 100}],
                "candidate": [],
            })

    def test_rejects_early_stop_pairs_with_different_observation_windows(self):
        samples = {
            "baseline": [
                {"early_stop_after_image_ms": 2_000}
                for _ in range(20)
            ],
            "candidate": [
                {"early_stop_after_image_ms": 2_250}
                for _ in range(20)
            ],
        }
        windows = benchmark_ab.compare_observation_windows(
            samples,
            "early-stop",
        )

        result = compare(
            summary(),
            summary(first_decoded_image_ms={"p95": 90.0}),
            windows=windows,
        )

        self.assertFalse(windows["valid"])
        self.assertEqual(20, len(windows["mismatched_pairs"]))
        self.assertFalse(result["accepted"])
        self.assertEqual(
            "paired early-stop observation windows differ by more than "
            "the allowed tolerance",
            result["reason"],
        )

    def test_accepts_early_stop_window_difference_at_tolerance(self):
        samples = {
            "baseline": [
                {"early_stop_after_image_ms": 2_000}
                for _ in range(20)
            ],
            "candidate": [
                {"early_stop_after_image_ms": 2_010}
                for _ in range(20)
            ],
        }

        result = benchmark_ab.compare_observation_windows(
            samples,
            "early-stop",
        )

        self.assertTrue(result["valid"])
        self.assertEqual(10, result["max_delta_ms"])

    def test_rejects_metric_measured_in_only_one_of_twenty_pairs(self):
        coverage = paired_metric_coverage()
        coverage["network_bytes_received"] = {"available_pairs": 1}

        result = compare(
            summary(),
            summary(first_decoded_image_ms={"p95": 90.0}),
            paired_metrics=coverage,
        )

        self.assertFalse(result["accepted"])
        self.assertIn(
            "network_bytes_received",
            result["unavailable_metrics"],
        )
        self.assertEqual(
            1,
            result["metrics"]["network_bytes_received"]["available_pairs"],
        )

    def test_rejects_boolean_and_non_finite_measurements(self):
        result = benchmark_ab.paired_deltas({
            "baseline": [
                {"first_decoded_image_ms": True},
                {"first_decoded_image_ms": float("nan")},
            ],
            "candidate": [
                {"first_decoded_image_ms": 90},
                {"first_decoded_image_ms": 90},
            ],
        })

        self.assertEqual(
            0,
            result["first_decoded_image_ms"]["available_pairs"],
        )

    def test_cli_parses_an_explicit_twenty_sample_count_as_an_integer(self):
        args = benchmark_ab.build_argument_parser().parse_args([
            "--baseline-exe", "baseline",
            "--candidate-exe", "candidate",
            "--artifact-root", "artifacts",
            "--output", "report.json",
            "--samples", "20",
        ])

        self.assertEqual(20, args.samples)
        self.assertIsInstance(args.samples, int)

    def test_output_reservation_is_exclusive_and_preserves_existing_report(self):
        with tempfile.TemporaryDirectory() as root:
            output = pathlib.Path(root) / "report.json"
            output.write_text("existing report\n", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                benchmark_ab.reserve_output(output)

            self.assertEqual(
                "existing report\n",
                output.read_text(encoding="utf-8"),
            )

    def test_reserved_output_is_not_accessible_to_group_or_other_users(self):
        with tempfile.TemporaryDirectory() as root:
            output = pathlib.Path(root) / "report.json"

            benchmark_ab.reserve_output(output)

            permissions = stat.S_IMODE(output.stat().st_mode)
            self.assertEqual(0, permissions & 0o077)

    def test_concurrent_runs_cannot_reserve_the_same_output_path(self):
        with tempfile.TemporaryDirectory() as root:
            output = pathlib.Path(root) / "report.json"

            def reserve() -> bool:
                try:
                    benchmark_ab.reserve_output(output)
                    return True
                except FileExistsError:
                    return False

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: reserve(), range(2)))

            self.assertCountEqual([True, False], results)
            self.assertTrue(output.exists())

    def test_interrupted_atomic_checkpoint_keeps_previous_report_intact(self):
        with tempfile.TemporaryDirectory() as root:
            output = pathlib.Path(root) / "report.json"
            benchmark_ab.reserve_output(output)
            benchmark_ab.write_report(output, {"checkpoint": 1})
            previous = output.read_text(encoding="utf-8")

            with mock.patch.object(
                benchmark_ab.os,
                "replace",
                side_effect=OSError("simulated interrupted checkpoint"),
            ):
                with self.assertRaises(OSError):
                    benchmark_ab.write_report(output, {"checkpoint": 2})

            self.assertEqual(previous, output.read_text(encoding="utf-8"))
            self.assertEqual([output], list(pathlib.Path(root).iterdir()))


if __name__ == "__main__":
    unittest.main()
