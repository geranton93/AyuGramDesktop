import importlib.util
import hashlib
import io
import json
import pathlib
import re
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[3]
RUNNER = ROOT / "Telegram/build/tests/benchmark_streaming.py"
SOURCE = ROOT / "Telegram/SourceFiles/tests/streaming_benchmark.cpp"
STUBS = ROOT / "Telegram/SourceFiles/tests/streaming_benchmark_stubs.cpp"
SPEC = importlib.util.spec_from_file_location("benchmark_streaming", RUNNER)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def sample(**overrides):
    result = {
        "profile": "warm",
        "mode": "startup",
        "error": "",
        "fixture_bytes": 1024,
        "cache_semantics": "reopened_persistent_telegram_cache_db",
        "loader_semantics": "cache_only_fail_on_miss",
        "network_latency_ms": 0,
        "network_bandwidth_bytes_per_second": 0,
        "network_max_concurrent_range_requests": 0,
        "speed_estimate_ms": None,
        "speed_estimate_completed_parts": None,
        "prefetch_probe_window_ms": None,
        "early_stop_after_image_ms": None,
        "early_stop_triggered": False,
        "network_timing_early_completions": 0,
        "range_requests": 0,
        "max_outstanding_range_requests": 0,
        "first_ready_ms": 0,
        "first_decoded_image_ms": 0,
        "first_video_progress_ms": 0,
        "first_network_request_ms": None,
        "speed_estimate_after_request_ms": None,
        "image_prepare_ms": 0,
        "bytes_delivered_at_first_image": 0,
        "network_bytes_at_first_image": 0,
        "network_bytes_received": 0,
        "bytes_delivered_to_reader": 0,
        "bytes_requested": 0,
        "video_wait_count": 0,
        "video_wait_ms": 0,
        "event_loop_lag_p95_ms": 0,
        "event_loop_lag_max_ms": 0,
        "peak_rss_mib": 0,
        "wall_ms": 0,
        "runner_wall_ms": 0,
        "child_minus_runner_wall_ms": 0,
        "cache_full": False,
    }
    result.update(overrides)
    return result


class StreamingBenchmarkRunner(unittest.TestCase):
    def test_mode_selection_can_isolate_startup_from_playback(self):
        self.assertEqual(
            [("startup", 5)],
            benchmark.modes_for_selection("startup", 5, 3),
        )
        self.assertEqual(
            [("playback", 3)],
            benchmark.modes_for_selection("playback", 5, 3),
        )
        self.assertEqual(
            [("startup", 5), ("playback", 3)],
            benchmark.modes_for_selection("all", 5, 3),
        )

    def test_mode_selection_can_isolate_early_stop_from_full_playback(self):
        self.assertEqual(
            [("early-stop", 3)],
            benchmark.modes_for_selection("early-stop", 5, 3),
        )

    def test_early_stop_rejects_an_invalid_observation_window(self):
        network_shape = benchmark.DEFAULT_NETWORK_SHAPE
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="early-stop",
                fixture_bytes=12 * 1024 * 1024,
                network_latency_ms=network_shape[0],
                network_bandwidth_bytes_per_second=network_shape[1],
                network_max_concurrent_range_requests=network_shape[2],
                early_stop_after_image_ms=1000,
                network_bytes_received=1024 * 1024,
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "early-stop window"):
                benchmark.run_sample(
                    pathlib.Path("/tmp/benchmark"),
                    "cold",
                    "early-stop",
                    pathlib.Path("/tmp/media-cache"),
                    network_shape,
                )

    def test_early_stop_accepts_a_bounded_cold_sample(self):
        network_shape = benchmark.DEFAULT_NETWORK_SHAPE
        fixture_bytes = 12 * 1024 * 1024
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="early-stop",
                fixture_bytes=fixture_bytes,
                network_latency_ms=network_shape[0],
                network_bandwidth_bytes_per_second=network_shape[1],
                network_max_concurrent_range_requests=network_shape[2],
                early_stop_after_image_ms=2000,
                early_stop_triggered=True,
                network_bytes_received=fixture_bytes - 1024,
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            result = benchmark.run_sample(
                pathlib.Path("/tmp/benchmark"),
                "cold",
                "early-stop",
                pathlib.Path("/tmp/media-cache"),
                network_shape,
            )

        self.assertEqual(2000, result["early_stop_after_image_ms"])

    def test_early_stop_mode_uses_a_timer_after_first_image(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("const auto stopAtBoundary = [&] {")
        end = source.index("player.updates()", start)
        stop_at_boundary = source[start:end]

        self.assertIn("mode != RunMode::EarlyStop", stop_at_boundary)
        self.assertIn("? kEarlyStopWindowMs", stop_at_boundary)
        self.assertIn(
            "if (mode == RunMode::EarlyStop && result.firstImageMs >= 0)",
            source,
        )
        self.assertIn("result.earlyStopAfterImageMs =", source)
        self.assertIn("result.earlyStopTriggered = true;", source)

    def test_timed_stop_closes_video_wait_before_teardown(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("QObject::connect(&stopTimer")
        end = source.index("const auto stopAtBoundary", start)
        stop_timer = source[start:end]

        self.assertLess(
            stop_timer.index("closeWaitingInterval(stoppedAtMs)"),
            stop_timer.index("player.stop()"),
        )

    def test_early_stop_rejects_a_full_fixture_download(self):
        network_shape = benchmark.DEFAULT_NETWORK_SHAPE
        fixture_bytes = 12 * 1024 * 1024
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="early-stop",
                fixture_bytes=fixture_bytes,
                network_latency_ms=network_shape[0],
                network_bandwidth_bytes_per_second=network_shape[1],
                network_max_concurrent_range_requests=network_shape[2],
                early_stop_after_image_ms=2000,
                early_stop_triggered=True,
                network_bytes_received=fixture_bytes,
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "downloaded the full fixture"):
                benchmark.run_sample(
                    pathlib.Path("/tmp/benchmark"),
                    "cold",
                    "early-stop",
                    pathlib.Path("/tmp/media-cache"),
                    network_shape,
                )

    def test_keep_temp_directory_uses_non_cleaning_mkdtemp(self):
        keep_root = pathlib.Path("/private/tmp/ayu-benchmark-kept")
        created = keep_root / "ayu-cold-sample"
        with patch.object(
            benchmark.tempfile,
            "mkdtemp",
            return_value=str(created),
        ) as mkdtemp:
            with patch.object(
                benchmark.tempfile,
                "TemporaryDirectory",
            ) as temporary_directory:
                with benchmark.temporary_directory("ayu-cold-", keep_root) as actual:
                    self.assertEqual(created, actual)

        mkdtemp.assert_called_once_with(
            prefix="ayu-cold-",
            dir=str(keep_root),
        )
        temporary_directory.assert_not_called()

    def test_all_cache_profiles_use_the_temporary_directory_helper(self):
        source = RUNNER.read_text(encoding="utf-8")

        self.assertEqual(4, source.count("with temporary_directory("))
        self.assertEqual(1, source.count("with tempfile.TemporaryDirectory("))

    def test_run_sample_passes_the_cache_path(self):
        cache_path = pathlib.Path("/tmp/media-cache")
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample()),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed) as run:
            benchmark.run_sample(
                pathlib.Path("/tmp/benchmark"),
                "warm",
                "startup",
                cache_path,
            )

        self.assertEqual(
            ["/tmp/benchmark", "warm", "startup", "/tmp/media-cache"],
            run.call_args.args[0],
        )

    def test_run_sample_passes_an_explicit_fixture_to_the_child(self):
        fixture = pathlib.Path("/tmp/long-media-fixture.mp4")
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample()),
            stderr="",
        )
        with patch.object(
            benchmark.subprocess,
            "run",
            return_value=completed,
        ) as run:
            benchmark.run_sample(
                pathlib.Path("/tmp/benchmark"),
                "warm",
                "startup",
                pathlib.Path("/tmp/media-cache"),
                fixture_path=fixture,
            )

        self.assertEqual(
            str(fixture.resolve()),
            run.call_args.kwargs["env"]["AYU_STREAMING_BENCHMARK_FIXTURE"],
        )

    def test_environment_fixture_is_selected_when_cli_fixture_is_absent(self):
        fixture = pathlib.Path("/tmp/environment-fixture.mp4")

        self.assertEqual(
            fixture.resolve(),
            benchmark.select_fixture(
                None,
                {"AYU_STREAMING_BENCHMARK_FIXTURE": str(fixture)},
            ),
        )

    def test_cli_fixture_takes_priority_over_environment_fixture(self):
        fixture = pathlib.Path("/tmp/cli-fixture.mp4")
        environment_fixture = pathlib.Path("/tmp/environment-fixture.mp4")

        self.assertEqual(
            fixture.resolve(),
            benchmark.select_fixture(
                fixture,
                {"AYU_STREAMING_BENCHMARK_FIXTURE": str(environment_fixture)},
            ),
        )

    def test_fixture_size_validator_accepts_allowed_boundary_sizes(self):
        benchmark.validate_fixture_size(benchmark.MIN_FIXTURE_BYTES + 1)
        benchmark.validate_fixture_size(benchmark.MAX_FIXTURE_BYTES)

    def test_fixture_size_validator_rejects_small_and_oversized_files(self):
        with self.assertRaisesRegex(ValueError, "must exceed"):
            benchmark.validate_fixture_size(benchmark.MIN_FIXTURE_BYTES)
        with self.assertRaisesRegex(ValueError, "exceeds benchmark limit"):
            benchmark.validate_fixture_size(benchmark.MAX_FIXTURE_BYTES + 1)

    def test_fixture_sha256_uses_bounded_reads(self):
        fixture = pathlib.Path("/tmp/media-fixture.mp4")
        with patch.object(
            pathlib.Path,
            "open",
            return_value=io.BytesIO(b"fixture-bytes"),
        ) as open_fixture:
            digest = benchmark.fixture_sha256(fixture)

        self.assertEqual(hashlib.sha256(b"fixture-bytes").hexdigest(), digest)
        open_fixture.assert_called_once_with("rb")

    def test_run_sample_preserves_opt_in_trace_fields(self):
        timeline = [{"phase": "request", "offset": 0, "at_ms": 1}]
        waits = [{"start_ms": 2, "end_ms": 5, "duration_ms": 3}]
        network_shape = benchmark.DEFAULT_NETWORK_SHAPE
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="playback",
                network_latency_ms=network_shape[0],
                network_bandwidth_bytes_per_second=network_shape[1],
                network_max_concurrent_range_requests=network_shape[2],
                range_timeline=timeline,
                video_wait_intervals=waits,
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            result = benchmark.run_sample(
                pathlib.Path("/tmp/benchmark"),
                "cold",
                "playback",
                pathlib.Path("/tmp/media-cache"),
                network_shape,
            )

        self.assertEqual(timeline, result["range_timeline"])
        self.assertEqual(waits, result["video_wait_intervals"])

    def test_prefetch_probe_rejects_missing_reliable_speed_estimate(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="prefetch-probe",
                network_latency_ms=benchmark.PREFETCH_PROBE_NETWORK_SHAPE[0],
                network_bandwidth_bytes_per_second=(
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE[1]
                ),
                network_max_concurrent_range_requests=(
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE[2]
                ),
                prefetch_probe_window_ms=6500,
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(
                RuntimeError,
                "prefetch probe ended before a reliable speed estimate",
            ):
                benchmark.run_sample(
                    pathlib.Path("/tmp/benchmark"),
                    "cold",
                    "prefetch-probe",
                    pathlib.Path("/tmp/media-cache"),
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE,
                )

    def test_prefetch_probe_rejects_estimate_before_three_completed_parts(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="prefetch-probe",
                network_latency_ms=benchmark.PREFETCH_PROBE_NETWORK_SHAPE[0],
                network_bandwidth_bytes_per_second=(
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE[1]
                ),
                network_max_concurrent_range_requests=(
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE[2]
                ),
                prefetch_probe_window_ms=6500,
                first_network_request_ms=100,
                speed_estimate_ms=5100,
                speed_estimate_completed_parts=2,
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(
                RuntimeError,
                "completed_parts=2",
            ):
                benchmark.run_sample(
                    pathlib.Path("/tmp/benchmark"),
                    "cold",
                    "prefetch-probe",
                    pathlib.Path("/tmp/media-cache"),
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE,
                )

    def test_prefetch_probe_accepts_estimate_after_three_completed_parts(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="prefetch-probe",
                network_latency_ms=benchmark.PREFETCH_PROBE_NETWORK_SHAPE[0],
                network_bandwidth_bytes_per_second=(
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE[1]
                ),
                network_max_concurrent_range_requests=(
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE[2]
                ),
                prefetch_probe_window_ms=6500,
                first_network_request_ms=100,
                speed_estimate_ms=5100,
                speed_estimate_completed_parts=3,
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            result = benchmark.run_sample(
                pathlib.Path("/tmp/benchmark"),
                "cold",
                "prefetch-probe",
                pathlib.Path("/tmp/media-cache"),
                benchmark.PREFETCH_PROBE_NETWORK_SHAPE,
            )

        self.assertEqual(5100, result["speed_estimate_ms"])
        self.assertEqual(3, result["speed_estimate_completed_parts"])
        self.assertEqual(5000, result["speed_estimate_after_request_ms"])

    def test_prefetch_probe_rejects_missing_observation_window(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="prefetch-probe",
                network_latency_ms=benchmark.PREFETCH_PROBE_NETWORK_SHAPE[0],
                network_bandwidth_bytes_per_second=(
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE[1]
                ),
                network_max_concurrent_range_requests=(
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE[2]
                ),
                first_network_request_ms=100,
                speed_estimate_ms=5100,
                speed_estimate_completed_parts=3,
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(
                RuntimeError,
                "prefetch probe observation window is missing",
            ):
                benchmark.run_sample(
                    pathlib.Path("/tmp/benchmark"),
                    "cold",
                    "prefetch-probe",
                    pathlib.Path("/tmp/media-cache"),
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE,
                )

    def test_prefetch_probe_rejects_window_outside_tolerance(self):
        for measured_window in (6499, 6751):
            with self.subTest(measured_window=measured_window):
                completed = SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(sample(
                        profile="cold",
                        mode="prefetch-probe",
                        network_latency_ms=(
                            benchmark.PREFETCH_PROBE_NETWORK_SHAPE[0]
                        ),
                        network_bandwidth_bytes_per_second=(
                            benchmark.PREFETCH_PROBE_NETWORK_SHAPE[1]
                        ),
                        network_max_concurrent_range_requests=(
                            benchmark.PREFETCH_PROBE_NETWORK_SHAPE[2]
                        ),
                        first_network_request_ms=100,
                        speed_estimate_ms=5100,
                        speed_estimate_completed_parts=3,
                        prefetch_probe_window_ms=measured_window,
                    )),
                    stderr="",
                )
                with patch.object(
                    benchmark.subprocess,
                    "run",
                    return_value=completed,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "prefetch probe observation window outside accepted bounds",
                    ):
                        benchmark.run_sample(
                            pathlib.Path("/tmp/benchmark"),
                            "cold",
                            "prefetch-probe",
                            pathlib.Path("/tmp/media-cache"),
                            benchmark.PREFETCH_PROBE_NETWORK_SHAPE,
                        )

    def test_prefetch_probe_accepts_maximum_window_overrun(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="prefetch-probe",
                network_latency_ms=benchmark.PREFETCH_PROBE_NETWORK_SHAPE[0],
                network_bandwidth_bytes_per_second=(
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE[1]
                ),
                network_max_concurrent_range_requests=(
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE[2]
                ),
                first_network_request_ms=100,
                speed_estimate_ms=5100,
                speed_estimate_completed_parts=3,
                prefetch_probe_window_ms=6750,
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            result = benchmark.run_sample(
                pathlib.Path("/tmp/benchmark"),
                "cold",
                "prefetch-probe",
                pathlib.Path("/tmp/media-cache"),
                benchmark.PREFETCH_PROBE_NETWORK_SHAPE,
            )

        self.assertEqual(6750, result["prefetch_probe_window_ms"])

    def test_prefetch_probe_rejects_estimate_before_five_second_gate(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="prefetch-probe",
                network_latency_ms=benchmark.PREFETCH_PROBE_NETWORK_SHAPE[0],
                network_bandwidth_bytes_per_second=(
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE[1]
                ),
                network_max_concurrent_range_requests=(
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE[2]
                ),
                prefetch_probe_window_ms=6500,
                first_network_request_ms=100,
                speed_estimate_ms=5099,
                speed_estimate_completed_parts=3,
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(
                RuntimeError,
                "elapsed_after_request_ms=4999",
            ):
                benchmark.run_sample(
                    pathlib.Path("/tmp/benchmark"),
                    "cold",
                    "prefetch-probe",
                    pathlib.Path("/tmp/media-cache"),
                    benchmark.PREFETCH_PROBE_NETWORK_SHAPE,
                )

    def test_run_sample_records_runner_and_child_clock_delta(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(wall_ms=55)),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            with patch.object(
                benchmark.time,
                "monotonic",
                side_effect=(10.0, 10.04),
            ):
                result = benchmark.run_sample(
                    pathlib.Path("/tmp/benchmark"),
                    "warm",
                    "startup",
                    pathlib.Path("/tmp/media-cache"),
                )

        self.assertEqual(40, result["runner_wall_ms"])
        self.assertEqual(15, result["child_minus_runner_wall_ms"])

    def test_run_sample_rejects_child_time_exceeding_process_time(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(wall_ms=933_274)),
            stderr="",
        )
        with patch.object(
            benchmark.subprocess,
            "run",
            return_value=completed,
        ):
            with patch.object(
                benchmark.time,
                "monotonic",
                side_effect=(10.0, 27.0),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "child time exceeds process time by more than "
                    "2000ms: .*"
                    "child_minus_runner_wall_ms=916274",
                ):
                    benchmark.run_sample(
                        pathlib.Path("/tmp/benchmark"),
                        "warm",
                        "startup",
                        pathlib.Path("/tmp/media-cache"),
                    )

    def test_run_sample_accepts_setup_time_exceeding_two_seconds(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(wall_ms=0)),
            stderr="",
        )
        with patch.object(
            benchmark.subprocess,
            "run",
            return_value=completed,
        ):
            with patch.object(
                benchmark.time,
                "monotonic",
                side_effect=(10.0, 27.0),
            ):
                result = benchmark.run_sample(
                    pathlib.Path("/tmp/benchmark"),
                    "warm",
                    "startup",
                    pathlib.Path("/tmp/media-cache"),
                )

        self.assertEqual(-17_000, result["child_minus_runner_wall_ms"])

    def test_run_sample_accepts_timing_skew_at_the_limit(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(wall_ms=2_040)),
            stderr="",
        )
        with patch.object(
            benchmark.subprocess,
            "run",
            return_value=completed,
        ):
            with patch.object(
                benchmark.time,
                "monotonic",
                side_effect=(10.0, 10.04),
            ):
                result = benchmark.run_sample(
                    pathlib.Path("/tmp/benchmark"),
                    "warm",
                    "startup",
                    pathlib.Path("/tmp/media-cache"),
                )

        self.assertEqual(2_000, result["child_minus_runner_wall_ms"])

    def test_failed_sample_reports_runner_and_child_clock_delta(self):
        completed = SimpleNamespace(
            returncode=1,
            stdout=json.dumps(sample(
                profile="cold",
                mode="playback",
                error="benchmark timed out",
                wall_ms=933_274,
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            with patch.object(
                benchmark.time,
                "monotonic",
                side_effect=(10.0, 27.0),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "child_wall_ms=933274, runner_wall_ms=17000, "
                    "child_minus_runner_wall_ms=916274",
                ):
                    benchmark.run_sample(
                        pathlib.Path("/tmp/benchmark"),
                        "cold",
                        "playback",
                        pathlib.Path("/tmp/media-cache"),
                    )

    def test_runner_timeout_reports_outer_elapsed_time(self):
        with patch.object(
            benchmark.subprocess,
            "run",
            side_effect=benchmark.subprocess.TimeoutExpired(
                ["/tmp/benchmark"],
                timeout=150,
            ),
        ):
            with patch.object(
                benchmark.time,
                "monotonic",
                side_effect=(10.0, 160.0),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "cold/playback exceeded runner timeout; "
                    "runner_wall_ms=150000",
                ):
                    benchmark.run_sample(
                        pathlib.Path("/tmp/benchmark"),
                        "cold",
                        "playback",
                        pathlib.Path("/tmp/media-cache"),
                    )

    def test_run_sample_passes_the_network_shape(self):
        cache_path = pathlib.Path("/tmp/media-cache")
        network_shape = (80, 2 * 1024 * 1024)
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="startup",
                network_latency_ms=network_shape[0],
                network_bandwidth_bytes_per_second=network_shape[1],
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed) as run:
            benchmark.run_sample(
                pathlib.Path("/tmp/benchmark"),
                "cold",
                "startup",
                cache_path,
                network_shape=network_shape,
            )

        self.assertEqual(
            [
                "/tmp/benchmark",
                "cold",
                "startup",
                "/tmp/media-cache",
                "80",
                str(2 * 1024 * 1024),
            ],
            run.call_args.args[0],
        )

    def test_cold_run_uses_the_default_concurrency_limit(self):
        cache_path = pathlib.Path("/tmp/media-cache")
        network_shape = benchmark.DEFAULT_NETWORK_SHAPE
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="startup",
                network_latency_ms=network_shape[0],
                network_bandwidth_bytes_per_second=network_shape[1],
                network_max_concurrent_range_requests=network_shape[2],
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed) as run:
            benchmark.run_sample(
                pathlib.Path("/tmp/benchmark"),
                "cold",
                "startup",
                cache_path,
            )

        self.assertEqual(
            [
                "/tmp/benchmark",
                "cold",
                "startup",
                "/tmp/media-cache",
                *(str(value) for value in network_shape),
            ],
            run.call_args.args[0],
        )

    def test_run_sample_passes_and_validates_concurrency_limit(self):
        cache_path = pathlib.Path("/tmp/media-cache")
        network_shape = (80, 2 * 1024 * 1024, 4)
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="startup",
                network_latency_ms=network_shape[0],
                network_bandwidth_bytes_per_second=network_shape[1],
                network_max_concurrent_range_requests=network_shape[2],
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed) as run:
            benchmark.run_sample(
                pathlib.Path("/tmp/benchmark"),
                "cold",
                "startup",
                cache_path,
                network_shape=network_shape,
            )

        self.assertEqual(
            [
                "/tmp/benchmark",
                "cold",
                "startup",
                "/tmp/media-cache",
                "80",
                str(2 * 1024 * 1024),
                "4",
            ],
            run.call_args.args[0],
        )

    def test_prefetch_probe_reports_window_and_cumulative_request_differences(self):
        control = sample(
            profile="cold",
            mode="prefetch-probe",
            max_outstanding_range_requests=8,
            bytes_requested=1_000,
        )
        high_throughput = sample(
            profile="cold",
            mode="prefetch-probe",
            max_outstanding_range_requests=26,
            bytes_requested=2_000,
        )
        keep_root = pathlib.Path("/private/tmp/ayu-benchmark-kept")
        with patch.object(
            benchmark,
            "temporary_directory",
            side_effect=(
                nullcontext(pathlib.Path("/tmp/control")),
                nullcontext(pathlib.Path("/tmp/high-throughput")),
            ),
        ) as temporary_directory:
            with patch.object(
                benchmark,
                "run_sample",
                side_effect=(control, high_throughput),
            ) as run:
                result = benchmark.run_prefetch_probe(
                    pathlib.Path("/tmp/benchmark"),
                    keep_root,
                )

        self.assertTrue(result["max_outstanding_range_requests_expanded"])
        self.assertEqual(18, result["max_outstanding_range_requests_delta"])
        self.assertEqual(1_000, result["bytes_requested_delta"])
        self.assertEqual(2, run.call_count)
        self.assertEqual("prefetch-probe", run.call_args.args[2])
        self.assertEqual(
            benchmark.PREFETCH_PROBE_NETWORK_SHAPE,
            run.call_args.kwargs["network_shape"],
        )
        self.assertEqual(2, temporary_directory.call_count)
        self.assertTrue(all(
            call.args[1] == keep_root
            for call in temporary_directory.call_args_list
        ))

    def test_prefetch_probe_reports_when_range_window_does_not_expand(self):
        control = sample(
            profile="cold",
            mode="prefetch-probe",
            max_outstanding_range_requests=8,
            bytes_requested=1_000,
        )
        high_throughput = sample(
            profile="cold",
            mode="prefetch-probe",
            max_outstanding_range_requests=8,
            bytes_requested=1_500,
        )
        keep_root = pathlib.Path("/private/tmp/ayu-benchmark-kept")
        with patch.object(
            benchmark,
            "temporary_directory",
            side_effect=(
                nullcontext(pathlib.Path("/tmp/control")),
                nullcontext(pathlib.Path("/tmp/high-throughput")),
            ),
        ) as temporary_directory:
            with patch.object(
                benchmark,
                "run_sample",
                side_effect=(control, high_throughput),
            ):
                result = benchmark.run_prefetch_probe(
                    pathlib.Path("/tmp/benchmark"),
                    keep_root,
                )

        self.assertFalse(result["max_outstanding_range_requests_expanded"])
        self.assertEqual(0, result["max_outstanding_range_requests_delta"])
        self.assertEqual(500, result["bytes_requested_delta"])
        self.assertEqual(2, temporary_directory.call_count)
        self.assertTrue(all(
            call.args[1] == keep_root
            for call in temporary_directory.call_args_list
        ))

    def test_warm_sample_rejects_network_fallback(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(range_requests=1)),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "warm cache miss attempted"):
                benchmark.run_sample(
                    pathlib.Path("/tmp/benchmark"),
                    "warm",
                    "startup",
                    pathlib.Path("/tmp/media-cache"),
                )

    def test_warm_cache_prime_requires_the_full_fixture(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(mode="prime", cache_full=False)),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "did not fill the full fixture"):
                benchmark.prepare_warm_cache(
                    pathlib.Path("/tmp/benchmark"),
                    pathlib.Path("/tmp/media-cache"),
                )

    def test_summary_keeps_network_reader_and_requested_bytes_separate(self):
        values = {
            "first_ready_ms": 20,
            "first_decoded_image_ms": 22,
            "early_stop_after_image_ms": 2000,
            "first_video_progress_ms": 35,
            "image_prepare_ms": 2,
            "bytes_delivered_at_first_image": 128,
            "network_bytes_at_first_image": 192,
            "network_bytes_received": 300,
            "bytes_delivered_to_reader": 256,
            "bytes_requested": 384,
            "range_requests": 3,
            "video_wait_count": 1,
            "video_wait_ms": 80,
            "event_loop_lag_p95_ms": 2,
            "event_loop_lag_max_ms": 4,
            "peak_rss_mib": 100,
            "wall_ms": 22,
            "runner_wall_ms": 20,
            "child_minus_runner_wall_ms": 2,
        }
        base = sample(**values, cache_full=True)
        summary = benchmark.summarize([base, base.copy()])

        self.assertEqual(384, summary["bytes_requested"]["median"])
        self.assertEqual(
            300,
            summary["network_bytes_received"]["median"],
        )
        self.assertEqual(
            256,
            summary["bytes_delivered_to_reader"]["median"],
        )
        self.assertEqual(2, summary["cache_full_samples"])
        self.assertEqual(
            35,
            summary["first_video_progress_ms"]["median"],
        )
        self.assertEqual(
            2000,
            summary["early_stop_after_image_ms"]["median"],
        )

    def test_summary_returns_null_for_an_unavailable_metric(self):
        summary = benchmark.summarize([
            sample(first_video_progress_ms=None),
            sample(first_video_progress_ms=None),
        ])

        self.assertIsNone(summary["first_video_progress_ms"])

    def test_summary_ignores_unavailable_metric_samples(self):
        summary = benchmark.summarize([
            sample(first_video_progress_ms=10),
            sample(first_video_progress_ms=None),
            sample(first_video_progress_ms=30),
        ])

        self.assertEqual(
            {"median": 20, "p95": 30, "max": 30},
            summary["first_video_progress_ms"],
        )

    def test_network_bytes_at_first_image_are_summarized_separately(self):
        base = sample(network_bytes_at_first_image=512)
        summary = benchmark.summarize([base, base.copy()])

        self.assertEqual(
            512,
            summary["network_bytes_at_first_image"]["median"],
        )

    def test_reported_network_profile_matches_the_loader(self):
        source = SOURCE.read_text(encoding="utf-8")

        self.assertIn("result.latencyMs = loaderView->latencyMs();", source)
        self.assertIn(
            "result.bandwidthBytesPerSecond = "
            "loaderView->bandwidthBytesPerSecond();",
            source,
        )
        self.assertIn("_networkShape.latencyMs", source)
        self.assertIn("_networkShape.bandwidthBytesPerSecond", source)
        self.assertIn("_networkShape.maxConcurrentRangeRequests", source)
        self.assertIn("network_timing_early_completions", source)

    def test_cold_sample_rejects_early_network_timer_completion(self):
        network_shape = benchmark.DEFAULT_NETWORK_SHAPE
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="startup",
                network_latency_ms=network_shape[0],
                network_bandwidth_bytes_per_second=network_shape[1],
                network_max_concurrent_range_requests=network_shape[2],
                network_timing_early_completions=1,
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(
                RuntimeError,
                "network timer completed before its configured duration",
            ):
                benchmark.run_sample(
                    pathlib.Path("/tmp/benchmark"),
                    "cold",
                    "startup",
                    pathlib.Path("/tmp/media-cache"),
                    network_shape,
                )

    def test_cold_sample_requires_network_timer_validation(self):
        network_shape = benchmark.DEFAULT_NETWORK_SHAPE
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(sample(
                profile="cold",
                mode="startup",
                network_latency_ms=network_shape[0],
                network_bandwidth_bytes_per_second=network_shape[1],
                network_max_concurrent_range_requests=network_shape[2],
                network_timing_early_completions=None,
            )),
            stderr="",
        )
        with patch.object(benchmark.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(
                RuntimeError,
                "cold sample omits network timer validation",
            ):
                benchmark.run_sample(
                    pathlib.Path("/tmp/benchmark"),
                    "cold",
                    "startup",
                    pathlib.Path("/tmp/media-cache"),
                    network_shape,
                )

    def test_headless_player_receives_periodic_video_update_requests(self):
        source = STUBS.read_text(encoding="utf-8")

        self.assertIn("_timer.setInterval(16);", source)
        self.assertIn("_requests.fire({});", source)
        self.assertIn("return requests.events();", source)
        self.assertNotIn("return rpl::never<>();", source)

    def test_headless_harness_requires_crl_main_queue_delivery(self):
        source = SOURCE.read_text(encoding="utf-8")

        self.assertIn("QCoreApplication(argc, argv)", source)
        self.assertIn("QMetaObject::invokeMethod(", source)
        self.assertIn("crl::wrap_main_queue(&QueueMainCallbackOnQt);", source)
        self.assertIn("Ui::Platform::DrainMainQueue();", source)
        self.assertNotIn("Ui::MainQueueProcessor()", source)
        self.assertIn("base::ConcurrentTimerEnvironment()", source)
        self.assertIn("ProcessMainQueueCallback(250)", source)
        self.assertIn(
            "Qt event loop did not service crl::on_main callback",
            source,
        )

    def test_result_is_flushed_after_cache_close_before_process_exit(self):
        source = SOURCE.read_text(encoding="utf-8")
        main = source[source.index("int main("):]

        self.assertLess(
            main.index("CloseCache(cache, &cacheError)"),
            main.index("std::cout.flush();"),
        )
        self.assertLess(
            main.index("std::cout.flush();"),
            main.index("std::_Exit("),
        )

    def test_fixture_size_is_checked_before_the_file_is_read(self):
        source = SOURCE.read_text(encoding="utf-8")
        main = source[source.index("int main("):]

        self.assertLess(main.index("file.size()"), main.index("file.readAll()"))
        self.assertIn("kMaxFixtureBytes", source)

    def test_stale_local_delivery_checks_generation_before_removing_pending(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("void scheduleLocalDelivery(")
        end = source.index("void scheduleNetworkQueue()", start)
        body = source[start:end]

        self.assertLess(
            body.index("_generation.value(offset) != generation"),
            body.index("!_pending.remove(offset)"),
        )

    def test_cancelled_network_range_reflows_pending_queue(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("void cancelOnMain(")
        end = source.index("const QByteArray _bytes", start)
        body = source[start:end]

        self.assertIn("std::erase(_networkQueue, offset);", body)
        self.assertIn("accountPartialNetworkBytes();", body)
        self.assertIn("scheduleNetworkQueue();", body)

    def test_network_queue_overlaps_latency_with_a_concurrency_limit(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("void scheduleNetworkQueue()")
        end = source.index("void startNextNetworkRange()", start)
        body = source[start:end]

        self.assertIn("while (!_networkQueue.empty()", body)
        self.assertIn("_networkShape.maxConcurrentRangeRequests", body)
        self.assertIn("++_inFlightNetworkRequests;", body)
        self.assertIn("_waitingForLatency.insert(offset);", body)
        self.assertIn(
            "QTimer::singleShot(latencyMs(), Qt::PreciseTimer, this,",
            body,
        )
        self.assertIn("_readyForTransfer.push_back(offset);", body)

    def test_network_transfer_uses_one_shared_bandwidth_pipe(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("void startNextNetworkRange()")
        end = source.index("void deliverNetworkRange()", start)
        body = source[start:end]

        self.assertIn("|| _activeNetworkOffset", body)
        self.assertIn("const auto bandwidth = bandwidthBytesPerSecond();", body)
        self.assertIn("_activeTransferStartsAt = _clock->elapsed();", body)
        self.assertIn("_networkTimer.setTimerType(Qt::PreciseTimer);", source)
        self.assertIn("_networkTimer.start(", body)
        self.assertNotIn("_requestedAt", source)

    def test_shaped_network_publishes_the_estimate_used_by_reader(self):
        source = SOURCE.read_text(encoding="utf-8")
        reader = (
            ROOT
            / "Telegram/SourceFiles/media/streaming/media_streaming_reader.cpp"
        ).read_text(encoding="utf-8")
        estimate_start = source.index(
            "rpl::producer<SpeedEstimate> speedEstimate() const override"
        )
        estimate_end = source.index("void attachDownloader(", estimate_start)
        estimate = source[estimate_start:estimate_end]
        publish_start = source.index("void publishNetworkEstimate()")
        publish_end = source.index("void stopOnMain()", publish_start)
        publish = source[publish_start:publish_end]

        self.assertIn("return _speedEstimate.events();", estimate)
        self.assertIn("_speedEstimate.fire({", publish)
        self.assertIn(".bytesPerSecond = int(bandwidthBytesPerSecond())", publish)
        self.assertIn(".unreliable = false", publish)
        self.assertIn(".latencyMs = int(latencyMs())", publish)
        self.assertIn("_loader->speedEstimate() | rpl::on_next", reader)
        self.assertIn(".downloadBytesPerSecond = _downloadBytesPerSecond.load(", reader)
        self.assertIn("ComputePrefetchWindow({", reader)

    def test_prefetch_probe_window_uses_a_precise_timer(self):
        source = SOURCE.read_text(encoding="utf-8")
        run_start = source.index("[[nodiscard]] Result Run(")
        run_end = source.index("[[nodiscard]] QJsonObject ToJson", run_start)
        run = source[run_start:run_end]
        boundary_start = run.index("const auto stopAtBoundary = [&]")
        boundary_end = run.index("player.updates()", boundary_start)
        boundary = run[boundary_start:boundary_end]

        self.assertIn("stopTimer.setTimerType(Qt::PreciseTimer);", run)
        self.assertIn("result.prefetchProbeWindowMs =", run)
        self.assertIn("stopTimer.start(delay);", boundary)
        self.assertNotIn("QTimer::singleShot(delay", boundary)

    def test_first_image_and_final_network_counts_include_partial_ranges_once(self):
        source = SOURCE.read_text(encoding="utf-8")
        run_start = source.index("[[nodiscard]] Result Run(")
        run_end = source.index("[[nodiscard]] QJsonObject ToJson", run_start)
        run = source[run_start:run_end]
        partial_start = source.index("void accountPartialNetworkBytes()")
        partial_end = source.index("void publishNetworkEstimate()", partial_start)
        partial = source[partial_start:partial_end]
        deliver_start = source.index("void deliverNetworkRange()")
        deliver_end = source.index("void accountPartialNetworkBytes()", deliver_start)
        deliver = source[deliver_start:deliver_end]

        self.assertIn("networkBytesReceivedAtNow()", run)
        self.assertIn("loaderView->stopAndWait();", run)
        self.assertLess(
            run.index("loaderView->stopAndWait();"),
            run.index("result.networkBytesReceived ="),
        )
        self.assertNotIn(
            "QCoreApplication::processEvents(QEventLoop::AllEvents, 20)",
            run,
        )
        self.assertIn(
            "const auto delta = received - _activeNetworkBytesAccounted;",
            partial,
        )
        self.assertIn("_activeNetworkBytesAccounted = received;", partial)
        self.assertIn("bytes.size() - _activeNetworkBytesAccounted", deliver)

    def test_opt_in_trace_records_loader_and_video_state_timeline(self):
        source = SOURCE.read_text(encoding="utf-8")

        self.assertIn(
            'std::getenv("AYU_STREAMING_BENCH_TRACE")',
            source,
        )
        for marker in (
            'u"request"_q',
            'u"latency_wait_started"_q',
            'u"latency_elapsed"_q',
            'u"transfer_started"_q',
            'u"range_delivered"_q',
            'u"range_cancelled"_q',
            'object.insert(u"range_timeline"_q, rangeTimeline);',
            'object.insert(u"video_wait_intervals"_q, videoWaitIntervals);',
            'u"received_till"_q',
            'u"position"_q',
            'u"waiting"_q',
            'value.insert(u"buffer_ms"_q, buffer);',
            'object.insert(u"video_state_timeline"_q, videoStateTimeline);',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_first_video_progress_is_measured_separately_from_first_image(self):
        source = SOURCE.read_text(encoding="utf-8")
        runner = RUNNER.read_text(encoding="utf-8")

        self.assertIn("played->position > 0", source)
        self.assertIn(
            "result.firstVideoProgressMs = clock.elapsed();",
            source,
        )
        self.assertIn('u"first_video_progress_ms"_q', source)
        self.assertIn('"first_video_progress_ms"', runner)


if __name__ == "__main__":
    unittest.main()
