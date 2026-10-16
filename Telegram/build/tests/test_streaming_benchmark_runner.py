import importlib.util
import json
import pathlib
import re
import unittest
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
        "range_requests": 0,
        "max_outstanding_range_requests": 0,
        "first_ready_ms": 0,
        "first_decoded_image_ms": 0,
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
        "cache_full": False,
    }
    result.update(overrides)
    return result


class StreamingBenchmarkRunner(unittest.TestCase):
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
        with patch.object(
            benchmark,
            "run_sample",
            side_effect=(control, high_throughput),
        ) as run:
            result = benchmark.run_prefetch_probe(
                pathlib.Path("/tmp/benchmark")
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
        with patch.object(
            benchmark,
            "run_sample",
            side_effect=(control, high_throughput),
        ):
            result = benchmark.run_prefetch_probe(
                pathlib.Path("/tmp/benchmark")
            )

        self.assertFalse(result["max_outstanding_range_requests_expanded"])
        self.assertEqual(0, result["max_outstanding_range_requests_delta"])
        self.assertEqual(500, result["bytes_requested_delta"])

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

    def test_headless_player_receives_periodic_video_update_requests(self):
        source = STUBS.read_text(encoding="utf-8")

        self.assertIn("_timer.setInterval(16);", source)
        self.assertIn("_requests.fire({});", source)
        self.assertIn("return requests.events();", source)
        self.assertNotIn("return rpl::never<>();", source)

    def test_headless_harness_requires_crl_main_queue_delivery(self):
        source = SOURCE.read_text(encoding="utf-8")

        self.assertIn("QApplication(argc, argv)", source)
        self.assertIn("base::ConcurrentTimerEnvironment()", source)
        self.assertIn("ProcessMainQueueCallback(250)", source)
        self.assertIn(
            "Qt event loop did not service crl::on_main callback",
            source,
        )

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

    def test_network_queue_keeps_the_active_transfer_timer(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("void scheduleNetworkQueue()")
        end = source.index("void deliverNetworkRange()", start)
        body = source[start:end]

        self.assertIn("|| _activeNetworkOffset", body)
        self.assertIn("|| _networkQueue.empty()", body)
        self.assertNotIn("QTimer::singleShot", body)
        self.assertIn("_networkTimer.start(", body)

    def test_network_latency_starts_when_a_range_becomes_active(self):
        source = SOURCE.read_text(encoding="utf-8")

        self.assertIn("_activeTransferStartsAt = now + latencyMs();", source)
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


if __name__ == "__main__":
    unittest.main()
