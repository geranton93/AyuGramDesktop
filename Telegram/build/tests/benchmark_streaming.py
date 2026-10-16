#!/usr/bin/env python3
"""Run isolated offline streaming samples and summarize their distributions."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
import pathlib
import statistics
import subprocess
import sys
import tempfile
import time

DEFAULT_NETWORK_SHAPE = (80, 512 * 1024, 4)
PREFETCH_PROBE_NETWORK_SHAPE = (80, 2 * 1024 * 1024, 4)
MIN_FIXTURE_BYTES = 10 * 1024 * 1024
MAX_FIXTURE_BYTES = 256 * 1024 * 1024
FIXTURE_HASH_CHUNK_BYTES = 1024 * 1024
PREFETCH_PROBE_MIN_SPEED_ESTIMATE_AFTER_REQUEST_MS = 5_000
PREFETCH_PROBE_MIN_RELIABLE_PARTS = 3
PREFETCH_PROBE_WINDOW_MS = 6_500
PREFETCH_PROBE_WINDOW_MAX_OVERRUN_MS = 250
EARLY_STOP_WINDOW_MS = 2_000
EARLY_STOP_WINDOW_MAX_OVERRUN_MS = 250
MAX_CHILD_OVER_RUNNER_WALL_MS = 2_000


@contextmanager
def temporary_directory(
    prefix: str,
    keep_root: pathlib.Path | None,
):
    if keep_root is None:
        with tempfile.TemporaryDirectory(prefix=prefix) as root:
            yield pathlib.Path(root)
    else:
        root = tempfile.mkdtemp(prefix=prefix, dir=str(keep_root))
        yield pathlib.Path(root)


def percentile(values: list[int | float], quantile: float) -> int | float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def select_fixture(
    explicit_fixture: pathlib.Path | None,
    environment: dict[str, str] | None = None,
) -> pathlib.Path:
    if explicit_fixture is not None:
        return explicit_fixture.resolve()
    environment = os.environ if environment is None else environment
    configured_fixture = environment.get("AYU_STREAMING_BENCHMARK_FIXTURE")
    if configured_fixture:
        return pathlib.Path(configured_fixture).expanduser().resolve()
    return (
        pathlib.Path(__file__).parents[2]
        / "SourceFiles/test/data/media_streaming/streaming_720p.mp4"
    ).resolve()


def validate_fixture_size(fixture_bytes: int) -> None:
    if fixture_bytes <= MIN_FIXTURE_BYTES:
        raise ValueError("fixture must exceed the Reader in-memory header limit")
    if fixture_bytes > MAX_FIXTURE_BYTES:
        raise ValueError(
            "offline media fixture exceeds benchmark limit of "
            f"{MAX_FIXTURE_BYTES} bytes"
        )


def fixture_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fixture:
        while chunk := fixture.read(FIXTURE_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def run_sample(
    executable: pathlib.Path,
    profile: str,
    mode: str,
    cache_path: pathlib.Path,
    network_shape: tuple[int, ...] | None = None,
    fixture_path: pathlib.Path | None = None,
) -> dict:
    command = [str(executable), profile, mode, str(cache_path)]
    if profile == "cold" and network_shape is None:
        network_shape = DEFAULT_NETWORK_SHAPE
    if network_shape is not None:
        command.extend(str(value) for value in network_shape)
    environment = None
    if fixture_path is not None:
        environment = os.environ.copy()
        environment["AYU_STREAMING_BENCHMARK_FIXTURE"] = str(
            fixture_path.resolve()
        )
    runner_started_at = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=150,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        runner_wall_ms = round((time.monotonic() - runner_started_at) * 1000)
        raise RuntimeError(
            f"{profile}/{mode} exceeded runner timeout; "
            f"runner_wall_ms={runner_wall_ms}"
        ) from error
    runner_wall_ms = round((time.monotonic() - runner_started_at) * 1000)
    try:
        sample = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"{profile}/{mode} exited {completed.returncode} without JSON; "
            f"runner_wall_ms={runner_wall_ms}:\n"
            f"{completed.stderr}\n{completed.stdout}"
        ) from error
    child_minus_runner_wall_ms = sample["wall_ms"] - runner_wall_ms
    if completed.returncode or sample["error"]:
        raise RuntimeError(
            f"{profile}/{mode} exited {completed.returncode} and reported: "
            f"{sample['error']}; child_wall_ms={sample['wall_ms']}, "
            f"runner_wall_ms={runner_wall_ms}, "
            f"child_minus_runner_wall_ms={child_minus_runner_wall_ms}\n"
            f"{completed.stderr}\n{completed.stdout}"
        )
    if sample["profile"] != profile or sample["mode"] != mode:
        raise RuntimeError(
            f"{profile}/{mode} returned mismatched labels: "
            f"{sample['profile']}/{sample['mode']}"
        )
    if profile == "cold":
        early_completions = sample.get("network_timing_early_completions")
        if early_completions is None or early_completions < 0:
            raise RuntimeError(
                "cold sample omits network timer validation"
            )
        if early_completions:
            raise RuntimeError(
                "network timer completed before its configured duration: "
                f"early_completions={early_completions}"
            )
    if mode == "early-stop":
        stop_window_ms = sample.get("early_stop_after_image_ms")
        maximum_window_ms = (
            EARLY_STOP_WINDOW_MS + EARLY_STOP_WINDOW_MAX_OVERRUN_MS
        )
        if (
            sample.get("early_stop_triggered") is not True
            or stop_window_ms is None
            or not EARLY_STOP_WINDOW_MS <= stop_window_ms <= maximum_window_ms
        ):
            raise RuntimeError(
                "early-stop window is missing or outside accepted bounds: "
                f"triggered={sample.get('early_stop_triggered')}, "
                f"window_ms={stop_window_ms}, "
                f"accepted={EARLY_STOP_WINDOW_MS}..{maximum_window_ms}"
            )
        received_bytes = sample.get("network_bytes_received", 0)
        fixture_bytes = sample.get("fixture_bytes", 0)
        if profile == "cold" and received_bytes >= fixture_bytes:
            raise RuntimeError(
                "early-stop sample downloaded the full fixture: "
                f"received={received_bytes}, "
                f"fixture={fixture_bytes}"
            )
    first_request_ms = sample.get("first_network_request_ms")
    estimate_ms = sample.get("speed_estimate_ms")
    estimate_after_request_ms = (
        estimate_ms - first_request_ms
        if estimate_ms is not None and first_request_ms is not None
        else None
    )
    sample["speed_estimate_after_request_ms"] = estimate_after_request_ms
    if mode == "prefetch-probe":
        probe_window_ms = sample.get("prefetch_probe_window_ms")
        if probe_window_ms is None:
            raise RuntimeError(
                "prefetch probe observation window is missing"
            )
        minimum_window_ms = PREFETCH_PROBE_WINDOW_MS
        maximum_window_ms = (
            PREFETCH_PROBE_WINDOW_MS
            + PREFETCH_PROBE_WINDOW_MAX_OVERRUN_MS
        )
        if not minimum_window_ms <= probe_window_ms <= maximum_window_ms:
            raise RuntimeError(
                "prefetch probe observation window outside accepted bounds: "
                f"window_ms={probe_window_ms}, "
                f"accepted={minimum_window_ms}..{maximum_window_ms}"
            )
        estimate_parts = sample.get("speed_estimate_completed_parts")
        if (
            estimate_after_request_ms is None
            or estimate_after_request_ms
                < PREFETCH_PROBE_MIN_SPEED_ESTIMATE_AFTER_REQUEST_MS
            or estimate_parts is None
            or estimate_parts < PREFETCH_PROBE_MIN_RELIABLE_PARTS
        ):
            raise RuntimeError(
                "prefetch probe ended before a reliable speed estimate: "
                f"first_network_request_ms={first_request_ms}, "
                f"speed_estimate_ms={estimate_ms}, "
                f"elapsed_after_request_ms={estimate_after_request_ms}, "
                f"completed_parts={estimate_parts}"
            )
    if child_minus_runner_wall_ms > MAX_CHILD_OVER_RUNNER_WALL_MS:
        raise RuntimeError(
            f"{profile}/{mode} child time exceeds process time by more "
            f"than {MAX_CHILD_OVER_RUNNER_WALL_MS}ms: "
            f"child_wall_ms={sample['wall_ms']}, "
            f"runner_wall_ms={runner_wall_ms}, "
            f"child_minus_runner_wall_ms={child_minus_runner_wall_ms}"
        )
    if profile == "cold":
        expected_shape = network_shape or DEFAULT_NETWORK_SHAPE
        actual_shape = (
            sample["network_latency_ms"],
            sample["network_bandwidth_bytes_per_second"],
        )
        if actual_shape != expected_shape[:2]:
            raise RuntimeError(
                f"{profile}/{mode} returned network shape "
                f"{actual_shape}, expected {expected_shape[:2]}"
            )
        if len(expected_shape) > 2 and sample[
            "network_max_concurrent_range_requests"
        ] != expected_shape[2]:
            raise RuntimeError(
                f"{profile}/{mode} returned max concurrent range requests "
                f"{sample['network_max_concurrent_range_requests']}, "
                f"expected {expected_shape[2]}"
            )
    if profile == "warm" and mode != "prime":
        if sample["range_requests"] or sample["network_bytes_received"]:
            raise RuntimeError(
                f"warm cache miss attempted during {mode}: "
                f"requests={sample['range_requests']} "
                f"bytes={sample['network_bytes_received']}"
            )
    sample["runner_wall_ms"] = runner_wall_ms
    sample["child_minus_runner_wall_ms"] = child_minus_runner_wall_ms
    return sample


def prepare_warm_cache(
    executable: pathlib.Path,
    cache_path: pathlib.Path,
    fixture_path: pathlib.Path | None = None,
) -> dict:
    prime = run_sample(
        executable,
        "warm",
        "prime",
        cache_path,
        fixture_path=fixture_path,
    )
    if not prime["cache_full"]:
        raise RuntimeError("warm cache prime did not fill the full fixture")
    return prime


def run_prefetch_probe(
    executable: pathlib.Path,
    keep_root: pathlib.Path | None = None,
    fixture_path: pathlib.Path | None = None,
) -> dict:
    with temporary_directory(
        "ayu-prefetch-control-",
        keep_root,
    ) as root:
        control = run_sample(
            executable,
            "cold",
            "prefetch-probe",
            pathlib.Path(root) / "media-cache",
            fixture_path=fixture_path,
        )
    with temporary_directory(
        "ayu-prefetch-fast-",
        keep_root,
    ) as root:
        high_throughput = run_sample(
            executable,
            "cold",
            "prefetch-probe",
            pathlib.Path(root) / "media-cache",
            network_shape=PREFETCH_PROBE_NETWORK_SHAPE,
            fixture_path=fixture_path,
        )
    control_outstanding = control["max_outstanding_range_requests"]
    high_outstanding = high_throughput[
        "max_outstanding_range_requests"
    ]
    return {
        "control": control,
        "high_throughput": high_throughput,
        "max_outstanding_range_requests_delta": (
            high_outstanding - control_outstanding
        ),
        "max_outstanding_range_requests_expanded": (
            high_outstanding > control_outstanding
        ),
        "bytes_requested_delta": (
            high_throughput["bytes_requested"] - control["bytes_requested"]
        ),
        "interpretation": (
            "Cumulative requested bytes are affected by both prefetch-window "
            "size and range completion speed, so this comparison does not "
            "isolate the Reader's computed window."
        ),
    }


def summarize(samples: list[dict]) -> dict:
    metrics = (
        "first_ready_ms",
        "first_decoded_image_ms",
        "first_video_progress_ms",
        "first_network_request_ms",
        "speed_estimate_ms",
        "speed_estimate_completed_parts",
        "speed_estimate_after_request_ms",
        "early_stop_after_image_ms",
        "image_prepare_ms",
        "bytes_delivered_at_first_image",
        "network_bytes_at_first_image",
        "network_bytes_received",
        "bytes_delivered_to_reader",
        "bytes_requested",
        "range_requests",
        "max_outstanding_range_requests",
        "video_wait_count",
        "video_wait_ms",
        "event_loop_lag_p95_ms",
        "event_loop_lag_max_ms",
        "peak_rss_mib",
        "wall_ms",
        "runner_wall_ms",
        "child_minus_runner_wall_ms",
    )
    result = {
        "samples": len(samples),
        "fixture_bytes": samples[0]["fixture_bytes"],
        "cache_semantics": samples[0]["cache_semantics"],
        "loader_semantics": samples[0]["loader_semantics"],
        "network_latency_ms": samples[0]["network_latency_ms"],
        "network_bandwidth_bytes_per_second": samples[0][
            "network_bandwidth_bytes_per_second"
        ],
        "network_max_concurrent_range_requests": samples[0][
            "network_max_concurrent_range_requests"
        ],
    }
    for metric in metrics:
        values = [sample[metric] for sample in samples]
        values = [value for value in values if value is not None]
        if not values:
            result[metric] = None
            continue
        result[metric] = {
            "median": statistics.median(values),
            "p95": percentile(values, 0.95),
            "max": max(values),
        }
    result["cache_full_samples"] = sum(
        1 for sample in samples if sample["cache_full"]
    )
    return result


def modes_for_selection(
    selection: str,
    samples: int,
    playback_samples: int,
) -> list[tuple[str, int]]:
    if selection == "startup":
        return [("startup", samples)]
    if selection == "playback":
        return [("playback", playback_samples)]
    if selection == "early-stop":
        return [("early-stop", playback_samples)]
    return [("startup", samples), ("playback", playback_samples)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=pathlib.Path)
    parser.add_argument("--fixture", type=pathlib.Path)
    parser.add_argument(
        "--mode",
        choices=("all", "startup", "playback", "early-stop"),
        default="all",
    )
    parser.add_argument("--samples", default=20, type=int)
    parser.add_argument("--playback-samples", default=20, type=int)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--prefetch-probe", action="store_true")
    parser.add_argument(
        "--keep-temp-root",
        type=pathlib.Path,
        help="keep cache directories under this existing path",
    )
    args = parser.parse_args()

    executable = args.exe.resolve()
    if not executable.is_file():
        parser.error(f"benchmark executable does not exist: {executable}")
    fixture = select_fixture(args.fixture)
    if not fixture.is_file():
        parser.error(f"offline fixture does not exist: {fixture}")
    try:
        validate_fixture_size(fixture.stat().st_size)
    except OSError as error:
        parser.error(f"could not inspect offline fixture {fixture}: {error}")
    except ValueError as error:
        parser.error(str(error))

    if args.keep_temp_root is not None:
        if not args.keep_temp_root.is_dir():
            parser.error(
                f"temporary artifact root is not a directory: "
                f"{args.keep_temp_root}"
            )
        args.keep_temp_root = args.keep_temp_root.resolve()

    if args.samples < 1 or args.playback_samples < 1:
        parser.error("sample counts must be positive")

    results = {
        "benchmark_binary": str(executable),
        "fixture": str(fixture),
        "fixture_sha256": fixture_sha256(fixture),
        "temporary_artifacts_root": (
            str(args.keep_temp_root) if args.keep_temp_root else None
        ),
        "mode_selection": args.mode,
        "measurement_scope": (
            "production Telegram Reader and Player with video-only playback "
            "and hardware decoding disabled; "
            "first_decoded_image_ms measures QImage availability, not paint "
            "or display vsync; first_video_progress_ms measures the first "
            "positive video position reported by Player, not display vsync; "
            "bytes_requested counts logical Loader requests, "
            "bytes_delivered_to_reader counts complete synthetic fixture "
            "parts, and network_bytes_received includes partial canceled "
            "ranges; network_bytes_at_first_image is sampled immediately "
            "after QImage availability in the same synchronous update "
            "callback; the offline Loader supports a configurable number of "
            "in-flight synthetic ranges while serializing payload bytes over "
            "one shared link; the default is 80 ms, 512 KiB/s and four "
            "in-flight ranges over that shared bandwidth, so these are "
            "controlled inputs rather than observed "
            "network estimates; --prefetch-probe compares the default with "
            "2 MiB/s at the same latency for 6500 ms after the first decoded "
            "image, requires a reliable speed estimate at least 5000 ms after "
            "the first network request and three completed parts; requires "
            "the actual observation window to be 6500..6750 ms; validates "
            "that synthetic network timers do not complete early; and reports "
            "estimate timing, cumulative requested bytes and the peak number "
            "of scheduled ranges awaiting synthetic delivery; this excludes "
            "load calls still queued for Qt dispatch; cumulative bytes do not "
            "isolate the computed prefetch window because range completion "
            "speed also affects requests made during the observation window; "
            "final network byte "
            "counts are captured after a "
            "synchronous loader stop; early-stop mode requests cancellation "
            "2000 ms after the first decoded image and validates that the "
            "cold fixture was not fully downloaded; "
            "a persistent Telegram Storage::Cache::Database uses an empty "
            "cache plus synthetic offline network shaping for cold runs, "
            "while warm runs reopen a primed cache with a fail-on-miss "
            "loader; event-loop lag is a headless Qt proxy; the opt-in "
            "AYU_STREAMING_BENCH_TRACE=1 timeline is diagnostic and should "
            "not be used for performance scoring"
        ),
        "runs": {},
    }
    with temporary_directory(
        "ayu-streaming-warm-",
        args.keep_temp_root,
    ) as warm_root:
        warm_cache = warm_root / "media-cache"
        results["warm_cache_prime"] = prepare_warm_cache(
            executable,
            warm_cache,
            fixture,
        )

        for profile in ("cold", "warm"):
            for mode, count in modes_for_selection(
                args.mode,
                args.samples,
                args.playback_samples,
            ):
                samples = []
                for _ in range(count):
                    if profile == "warm":
                        sample = run_sample(
                            executable,
                            profile,
                            mode,
                            warm_cache,
                            fixture_path=fixture,
                        )
                    else:
                        with temporary_directory(
                            "ayu-streaming-cold-",
                            args.keep_temp_root,
                        ) as cold_root:
                            sample = run_sample(
                                executable,
                                profile,
                                mode,
                                pathlib.Path(cold_root) / "media-cache",
                                fixture_path=fixture,
                            )
                    samples.append(sample)
                results["runs"][f"{profile}/{mode}"] = {
                    "summary": summarize(samples),
                    "raw": samples,
                }

        if args.prefetch_probe:
            results["prefetch_probe"] = run_prefetch_probe(
                executable,
                args.keep_temp_root,
                fixture,
            )

    rendered = json.dumps(results, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
