#!/usr/bin/env python3
"""Run isolated offline streaming samples and summarize their distributions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import statistics
import subprocess
import sys
import tempfile

DEFAULT_NETWORK_SHAPE = (80, 512 * 1024)
PREFETCH_PROBE_NETWORK_SHAPE = (80, 2 * 1024 * 1024)


def percentile(values: list[int | float], quantile: float) -> int | float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def run_sample(
    executable: pathlib.Path,
    profile: str,
    mode: str,
    cache_path: pathlib.Path,
    network_shape: tuple[int, int] | None = None,
) -> dict:
    command = [str(executable), profile, mode, str(cache_path)]
    if network_shape is not None:
        command.extend(str(value) for value in network_shape)
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=150,
    )
    if completed.returncode:
        raise RuntimeError(
            f"{profile}/{mode} exited {completed.returncode}:\n"
            f"{completed.stderr}\n{completed.stdout}"
        )
    try:
        sample = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"{profile}/{mode} did not emit a JSON result:\n"
            f"{completed.stderr}\n{completed.stdout}"
        ) from error
    if sample["error"]:
        raise RuntimeError(f"{profile}/{mode} reported: {sample['error']}")
    if sample["profile"] != profile or sample["mode"] != mode:
        raise RuntimeError(
            f"{profile}/{mode} returned mismatched labels: "
            f"{sample['profile']}/{sample['mode']}"
        )
    if profile == "cold":
        expected_shape = network_shape or DEFAULT_NETWORK_SHAPE
        actual_shape = (
            sample["network_latency_ms"],
            sample["network_bandwidth_bytes_per_second"],
        )
        if actual_shape != expected_shape:
            raise RuntimeError(
                f"{profile}/{mode} returned network shape "
                f"{actual_shape}, expected {expected_shape}"
            )
    if profile == "warm" and mode != "prime":
        if sample["range_requests"] or sample["network_bytes_received"]:
            raise RuntimeError(
                f"warm cache miss attempted during {mode}: "
                f"requests={sample['range_requests']} "
                f"bytes={sample['network_bytes_received']}"
            )
    return sample


def prepare_warm_cache(
    executable: pathlib.Path,
    cache_path: pathlib.Path,
) -> dict:
    prime = run_sample(executable, "warm", "prime", cache_path)
    if not prime["cache_full"]:
        raise RuntimeError("warm cache prime did not fill the full fixture")
    return prime


def run_prefetch_probe(executable: pathlib.Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="ayu-prefetch-control-") as root:
        control = run_sample(
            executable,
            "cold",
            "prefetch-probe",
            pathlib.Path(root) / "media-cache",
        )
    with tempfile.TemporaryDirectory(prefix="ayu-prefetch-fast-") as root:
        high_throughput = run_sample(
            executable,
            "cold",
            "prefetch-probe",
            pathlib.Path(root) / "media-cache",
            network_shape=PREFETCH_PROBE_NETWORK_SHAPE,
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
    }
    for metric in metrics:
        values = [sample[metric] for sample in samples]
        result[metric] = {
            "median": statistics.median(values),
            "p95": percentile(values, 0.95),
            "max": max(values),
        }
    result["cache_full_samples"] = sum(
        1 for sample in samples if sample["cache_full"]
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=pathlib.Path)
    parser.add_argument("--samples", default=20, type=int)
    parser.add_argument("--playback-samples", default=20, type=int)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--prefetch-probe", action="store_true")
    args = parser.parse_args()

    executable = args.exe.resolve()
    if not executable.is_file():
        parser.error(f"benchmark executable does not exist: {executable}")
    fixture = pathlib.Path(
        __file__
    ).parents[2] / "SourceFiles/test/data/media_streaming/streaming_720p.mp4"
    if not fixture.is_file():
        parser.error(f"offline fixture does not exist: {fixture}")

    if args.samples < 1 or args.playback_samples < 1:
        parser.error("sample counts must be positive")

    results = {
        "candidate_binary": str(executable),
        "fixture": str(fixture),
        "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "measurement_scope": (
            "production Telegram Reader and Player with video-only playback "
            "and hardware decoding disabled; "
            "first_decoded_image_ms measures QImage availability, not paint "
            "or display vsync; bytes_requested counts logical Loader requests, "
            "bytes_delivered_to_reader counts complete synthetic fixture "
            "parts, and network_bytes_received includes partial canceled "
            "ranges; network_bytes_at_first_image is sampled immediately "
            "after QImage availability in the same synchronous update "
            "callback; the offline Loader processes one active synthetic "
            "range at a time and applies the supplied latency when each range "
            "becomes active; the default is 80 ms and 512 KiB/s "
            "bandwidth, so these are controlled inputs rather than observed "
            "network estimates; --prefetch-probe compares the default with "
            "2 MiB/s at the same latency for 1200 ms after the first decoded "
            "image, and reports cumulative requested bytes and the maximum "
            "number of outstanding logical ranges; cumulative bytes do not "
            "isolate the computed prefetch window because range completion "
            "speed also affects requests made during the observation window; "
            "final network byte "
            "counts are captured after a "
            "synchronous loader stop; "
            "a persistent Telegram Storage::Cache::Database uses an empty "
            "cache plus synthetic offline network shaping for cold runs, "
            "while warm runs reopen a primed cache with a fail-on-miss "
            "loader; event-loop lag is a headless Qt proxy"
        ),
        "runs": {},
    }
    with tempfile.TemporaryDirectory(prefix="ayu-streaming-warm-") as warm_root:
        warm_cache = pathlib.Path(warm_root) / "media-cache"
        results["warm_cache_prime"] = prepare_warm_cache(
            executable,
            warm_cache,
        )

        for profile in ("cold", "warm"):
            for mode, count in (
                ("startup", args.samples),
                ("playback", args.playback_samples),
            ):
                samples = []
                for _ in range(count):
                    if profile == "warm":
                        sample = run_sample(
                            executable,
                            profile,
                            mode,
                            warm_cache,
                        )
                    else:
                        with tempfile.TemporaryDirectory(
                            prefix="ayu-streaming-cold-"
                        ) as cold_root:
                            sample = run_sample(
                                executable,
                                profile,
                                mode,
                                pathlib.Path(cold_root) / "media-cache",
                            )
                    samples.append(sample)
                results["runs"][f"{profile}/{mode}"] = {
                    "summary": summarize(samples),
                    "raw": samples,
                }

        if args.prefetch_probe:
            results["prefetch_probe"] = run_prefetch_probe(executable)

    rendered = json.dumps(results, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
