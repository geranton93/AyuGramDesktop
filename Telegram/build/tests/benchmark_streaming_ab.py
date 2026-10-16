#!/usr/bin/env python3
"""Run retained, paired offline streaming comparisons."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import platform
import statistics
import tempfile

import benchmark_streaming as streaming

MIN_ACCEPTANCE_PAIRS = 20
MIN_IMPROVEMENT_PERCENT = 10.0
MAX_REGRESSION_PERCENT = 5.0
MAX_PAIRED_EARLY_STOP_WINDOW_DELTA_MS = 10
SCORING_METRICS = (
    "first_decoded_image_ms",
    "network_bytes_at_first_image",
    "network_bytes_received",
    "bytes_requested",
    "video_wait_count",
    "video_wait_ms",
    "event_loop_lag_p95_ms",
    "peak_rss_mib",
    "wall_ms",
)


def is_measurement(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(streaming.FIXTURE_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def source_paths(
        source_root: pathlib.Path | None,
        harness_source_root: pathlib.Path | None,
) -> dict[str, pathlib.Path]:
    if source_root is None:
        return {}
    if not source_root.is_dir():
        raise ValueError(f"streaming source root does not exist: {source_root}")
    streaming_root = source_root / "media/streaming"
    if not streaming_root.is_dir():
        raise ValueError(f"source root has no streaming sources: {source_root}")
    files = {
        path.relative_to(source_root).as_posix(): path
        for path in streaming_root.rglob("*")
        if path.is_file() and path.suffix in {".cpp", ".h", ".mm"}
    }
    if not files:
        raise ValueError(f"source root has no streaming sources: {source_root}")
    harness_source_root = harness_source_root or source_root
    for relative in (
        "tests/streaming_benchmark.cpp",
        "tests/streaming_benchmark_stubs.cpp",
        "tests/streaming_benchmark_pch.h",
        "data/data_audio_msg_id.cpp",
    ):
        path = harness_source_root / relative
        if not path.is_file():
            raise ValueError(
                "benchmark harness source is missing from "
                f"{harness_source_root}: {relative}"
            )
        files[relative] = path
    return dict(sorted(files.items()))


def source_files(
        source_root: pathlib.Path | None,
        harness_source_root: pathlib.Path | None = None,
) -> list[str]:
    return list(source_paths(source_root, harness_source_root))


def source_sha256(
        source_root: pathlib.Path | None,
        harness_source_root: pathlib.Path | None = None,
) -> str | None:
    if source_root is None:
        return None
    digest = hashlib.sha256()
    for relative, path in source_paths(source_root, harness_source_root).items():
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def compare_summaries(
        baseline: dict,
        candidate: dict,
        pair_count: int,
        paired_metrics: dict,
        observation_windows: dict,
) -> dict:
    metrics = {}
    for name in SCORING_METRICS:
        baseline_summary = baseline.get(name)
        candidate_summary = candidate.get(name)
        baseline_p95 = (
            baseline_summary.get("p95")
            if isinstance(baseline_summary, dict)
            else None
        )
        candidate_p95 = (
            candidate_summary.get("p95")
            if isinstance(candidate_summary, dict)
            else None
        )
        paired_metric = paired_metrics.get(name)
        available_pairs = (
            paired_metric.get("available_pairs")
            if isinstance(paired_metric, dict)
            else None
        )
        available = (
            is_measurement(baseline_p95)
            and is_measurement(candidate_p95)
            and available_pairs == pair_count
        )
        if not available:
            metrics[name] = {
                "baseline_p95": baseline_p95,
                "candidate_p95": candidate_p95,
                "change_percent": None,
                "improved_by_at_least_10_percent": False,
                "regressed_by_more_than_5_percent": False,
                "available": False,
                "available_pairs": available_pairs,
                "expected_pairs": pair_count,
            }
            continue
        if baseline_p95 == 0:
            change_percent = 0.0 if candidate_p95 == 0 else None
            improved = False
            regressed = candidate_p95 > 0
        else:
            change_percent = (
                (candidate_p95 - baseline_p95) / baseline_p95 * 100.0
            )
            improved = candidate_p95 <= baseline_p95 * 0.9
            regressed = candidate_p95 > baseline_p95 * 1.05
        metrics[name] = {
            "baseline_p95": baseline_p95,
            "candidate_p95": candidate_p95,
            "change_percent": change_percent,
            "improved_by_at_least_10_percent": improved,
            "regressed_by_more_than_5_percent": regressed,
            "available": True,
            "available_pairs": available_pairs,
            "expected_pairs": pair_count,
        }

    improved_metrics = [
        name for name, value in metrics.items()
        if value["improved_by_at_least_10_percent"]
    ]
    regressed_metrics = [
        name for name, value in metrics.items()
        if value["regressed_by_more_than_5_percent"]
    ]
    unavailable_metrics = [
        name for name, value in metrics.items()
        if not value["available"]
    ]
    enough_pairs = pair_count >= MIN_ACCEPTANCE_PAIRS
    observation_windows_match = observation_windows.get("valid") is True
    return {
        "thresholds": {
            "minimum_improvement_percent": MIN_IMPROVEMENT_PERCENT,
            "maximum_regression_percent": MAX_REGRESSION_PERCENT,
            "minimum_pairs": MIN_ACCEPTANCE_PAIRS,
        },
        "pair_count": pair_count,
        "observation_windows": observation_windows,
        "improved_metrics": improved_metrics,
        "regressed_metrics": regressed_metrics,
        "unavailable_metrics": unavailable_metrics,
        "accepted": bool(
            enough_pairs
            and observation_windows_match
            and improved_metrics
            and not regressed_metrics
            and not unavailable_metrics
        ),
        "reason": (
            "accepted"
            if enough_pairs
            and observation_windows_match
            and improved_metrics
            and not regressed_metrics
            and not unavailable_metrics
            else "insufficient paired samples"
            if not enough_pairs
            else (
                "paired early-stop observation windows differ by more than "
                "the allowed tolerance"
            )
            if not observation_windows_match
            else "a measured metric regressed by more than 5 percent"
            if regressed_metrics
            else "one or more metrics are unavailable"
            if unavailable_metrics
            else "no measured metric improved by at least 10 percent"
        ),
        "metrics": metrics,
    }


def paired_deltas(samples: dict) -> dict:
    result = {}
    baseline = samples["baseline"]
    candidate = samples["candidate"]
    for name in SCORING_METRICS:
        differences = []
        percent_changes = []
        for before, after in zip(baseline, candidate, strict=True):
            left = before.get(name)
            right = after.get(name)
            if not is_measurement(left) or not is_measurement(right):
                continue
            differences.append(right - left)
            if left != 0:
                percent_changes.append((right - left) / left * 100.0)
            elif right == 0:
                percent_changes.append(0.0)
        result[name] = {
            "available_pairs": len(differences),
            "percent_change_pairs": len(percent_changes),
            "percent_change_unavailable_pairs": (
                len(differences) - len(percent_changes)
            ),
            "median_candidate_minus_baseline": (
                statistics.median(differences) if differences else None
            ),
            "p95_candidate_minus_baseline": (
                streaming.percentile(differences, 0.95)
                if differences else None
            ),
            "median_pair_percent_change": (
                statistics.median(percent_changes)
                if percent_changes else None
            ),
            "p95_pair_percent_change": (
                streaming.percentile(percent_changes, 0.95)
                if percent_changes else None
            ),
        }
    return result


def compare_observation_windows(samples: dict, mode: str) -> dict:
    baseline = samples["baseline"]
    candidate = samples["candidate"]
    if len(baseline) != len(candidate):
        raise ValueError("baseline and candidate sample counts differ")
    if mode != "early-stop":
        return {
            "applicable": False,
            "valid": True,
            "compared_pairs": 0,
            "max_delta_ms": None,
            "mismatched_pairs": [],
        }

    deltas = []
    mismatched_pairs = []
    for index, (before, after) in enumerate(
        zip(baseline, candidate, strict=True),
        start=1,
    ):
        before_ms = before.get("early_stop_after_image_ms")
        after_ms = after.get("early_stop_after_image_ms")
        if not is_measurement(before_ms) or not is_measurement(after_ms):
            mismatched_pairs.append(index)
            continue
        delta_ms = abs(after_ms - before_ms)
        deltas.append(delta_ms)
        if delta_ms > MAX_PAIRED_EARLY_STOP_WINDOW_DELTA_MS:
            mismatched_pairs.append(index)
    return {
        "applicable": True,
        "valid": not mismatched_pairs,
        "tolerance_ms": MAX_PAIRED_EARLY_STOP_WINDOW_DELTA_MS,
        "compared_pairs": len(deltas),
        "max_delta_ms": max(deltas) if deltas else None,
        "mismatched_pairs": mismatched_pairs,
    }


def pair_order(index: int) -> tuple[str, str]:
    return ("baseline", "candidate") if index % 2 == 0 else (
        "candidate", "baseline"
    )


def new_cache_path(root: pathlib.Path, prefix: str) -> pathlib.Path:
    directory = pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=root))
    return directory / "media-cache"


def reserve_output(path: pathlib.Path) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    os.close(descriptor)


def write_report(path: pathlib.Path, report: dict) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-exe", required=True, type=pathlib.Path)
    parser.add_argument("--candidate-exe", required=True, type=pathlib.Path)
    parser.add_argument("--fixture", type=pathlib.Path)
    parser.add_argument(
        "--baseline-source-root",
        type=pathlib.Path,
        help="path to baseline Telegram/SourceFiles",
    )
    parser.add_argument(
        "--candidate-source-root",
        type=pathlib.Path,
        help="path to candidate Telegram/SourceFiles",
    )
    parser.add_argument(
        "--profile",
        choices=("cold", "warm"),
        default="cold",
    )
    parser.add_argument(
        "--mode",
        choices=("playback", "early-stop"),
        default="playback",
    )
    parser.add_argument("--samples", default=20, type=int)
    parser.add_argument("--artifact-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    baseline_exe = args.baseline_exe.resolve()
    candidate_exe = args.candidate_exe.resolve()
    for label, path in (
        ("baseline executable", baseline_exe),
        ("candidate executable", candidate_exe),
    ):
        if not path.is_file():
            parser.error(f"{label} does not exist: {path}")
    fixture = streaming.select_fixture(args.fixture)
    if not fixture.is_file():
        parser.error(f"offline fixture does not exist: {fixture}")
    try:
        streaming.validate_fixture_size(fixture.stat().st_size)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    artifact_root = args.artifact_root.resolve()
    if not artifact_root.is_dir():
        parser.error(f"artifact root does not exist: {artifact_root}")
    output = args.output.resolve()
    if not output.parent.is_dir():
        parser.error(f"output parent does not exist: {output.parent}")
    if args.samples < MIN_ACCEPTANCE_PAIRS:
        parser.error(
            f"at least {MIN_ACCEPTANCE_PAIRS} paired samples are required"
        )
    source_roots = {
        "baseline": args.baseline_source_root.resolve()
        if args.baseline_source_root else None,
        "candidate": args.candidate_source_root.resolve()
        if args.candidate_source_root else None,
    }
    harness_source_root = pathlib.Path(__file__).resolve().parents[2] / "SourceFiles"
    for label, source_root in source_roots.items():
        try:
            source_files(source_root, harness_source_root)
        except ValueError as error:
            parser.error(f"{label} source root: {error}")
    try:
        reserve_output(output)
    except FileExistsError:
        parser.error(
            "output already exists or another run reserved it; "
            f"refusing to overwrite: {output}"
        )
    write_report(output, {"complete": False, "state": "initializing"})

    binaries = {"baseline": baseline_exe, "candidate": candidate_exe}
    report = {
        "complete": False,
        "state": "running",
        "mode": args.mode,
        "profile": args.profile,
        "fixture": str(fixture),
        "fixture_sha256": streaming.fixture_sha256(fixture),
        "network_profile": {
            "latency_ms": streaming.DEFAULT_NETWORK_SHAPE[0],
            "bandwidth_bytes_per_second": streaming.DEFAULT_NETWORK_SHAPE[1],
            "max_concurrent_range_requests": streaming.DEFAULT_NETWORK_SHAPE[2],
        },
        "host": {
            "architecture": platform.machine(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "artifact_root": str(artifact_root),
        "baseline_binary": str(baseline_exe),
        "candidate_binary": str(candidate_exe),
        "baseline_binary_sha256": file_sha256(baseline_exe),
        "candidate_binary_sha256": file_sha256(candidate_exe),
        "baseline_streaming_source_root": (
            str(source_roots["baseline"])
            if source_roots["baseline"] else None
        ),
        "baseline_streaming_source_files": source_files(
            source_roots["baseline"],
            harness_source_root,
        ),
        "baseline_streaming_source_sha256": source_sha256(
            source_roots["baseline"],
            harness_source_root,
        ),
        "candidate_streaming_source_root": (
            str(source_roots["candidate"])
            if source_roots["candidate"] else None
        ),
        "candidate_streaming_source_files": source_files(
            source_roots["candidate"],
            harness_source_root,
        ),
        "candidate_streaming_source_sha256": source_sha256(
            source_roots["candidate"],
            harness_source_root,
        ),
        "measurement_harness_sha256": {
            "benchmark_streaming.py": file_sha256(
                pathlib.Path(streaming.__file__)
            ),
            "benchmark_streaming_ab.py": file_sha256(
                pathlib.Path(__file__)
            ),
        },
        "measurement_harness_source_root": str(harness_source_root),
        "samples_per_variant": args.samples,
        "pair_order": [],
        "samples": {"baseline": [], "candidate": []},
        "summaries": {"baseline": None, "candidate": None},
        "paired_deltas": {},
        "observation_window_comparison": None,
        "acceptance": None,
        "measurement_scope": (
            "headless production Reader and Player using one offline fixture; "
            "streaming source digests cover the sorted .cpp, .h and .mm files "
            "under media/streaming plus benchmark sources, stubs, its "
            "precompiled header and the data helper; "
            "first_decoded_image_ms records QImage availability rather than "
            "paint or display vsync; event_loop_lag_p95_ms is a headless UI "
            "proxy; network_bytes_received is actual synthetic payload bytes "
            "accounted through synchronous cancellation; early-stop mode "
            "stops 2000 ms after the first decoded image; warm mode uses a "
            "primed cache and fail-on-miss loading; all cache artifacts are "
            "retained under artifact_root"
        ),
    }
    write_report(output, report)
    warm_caches = {}
    if args.profile == "warm":
        for variant, executable in binaries.items():
            cache = new_cache_path(artifact_root, f"ayu-warm-{variant}-")
            streaming.prepare_warm_cache(executable, cache, fixture)
            warm_caches[variant] = cache
    for index in range(args.samples):
        order = pair_order(index)
        report["pair_order"].append({"pair": index + 1, "order": list(order)})
        pair_samples = {}
        for variant in order:
            if args.profile == "warm":
                cache = warm_caches[variant]
            else:
                cache = new_cache_path(
                    artifact_root,
                    f"ayu-cold-pair-{index + 1:02d}-{variant}-",
                )
            pair_samples[variant] = streaming.run_sample(
                binaries[variant],
                args.profile,
                args.mode,
                cache,
                streaming.DEFAULT_NETWORK_SHAPE
                if args.profile == "cold" else None,
                fixture,
            )
        for variant in ("baseline", "candidate"):
            report["samples"][variant].append(pair_samples[variant])
        pair_count = index + 1
        report["summaries"] = {
            variant: streaming.summarize(report["samples"][variant])
            for variant in ("baseline", "candidate")
        }
        report["paired_deltas"] = paired_deltas(report["samples"])
        report["observation_window_comparison"] = (
            compare_observation_windows(report["samples"], args.mode)
        )
        report["acceptance"] = compare_summaries(
            report["summaries"]["baseline"],
            report["summaries"]["candidate"],
            pair_count,
            report["paired_deltas"],
            report["observation_window_comparison"],
        )
        report["complete"] = pair_count == args.samples
        report["state"] = "complete" if report["complete"] else "running"
        write_report(output, report)
        print(
            f"paired-samples {args.profile}/{args.mode} "
            f"{pair_count}/{args.samples}",
            flush=True,
        )
    print(f"artifact={output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
