# ruff: noqa: E402
from __future__ import annotations

import errno
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from replay import hardware_collectors
from replay.hardware_collectors import HardwareCollector


def _u64_bytes(value: int) -> bytes:
    return value.to_bytes(8, byteorder=sys.byteorder, signed=False)


class ReplayHardwareCollectorsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.collector = HardwareCollector()

    def tearDown(self) -> None:
        self.collector.close()

    def test_start_and_stop_sums_thread_totals_without_polling(self):
        with (
            mock.patch.object(
                hardware_collectors,
                "_list_task_thread_ids",
                return_value=[11, 12],
            ) as list_threads_mock,
            mock.patch.object(
                hardware_collectors,
                "_perf_event_open",
                side_effect=[101, 102, 201, 202],
            ) as open_mock,
            mock.patch.object(hardware_collectors, "ioctl") as ioctl_mock,
            mock.patch.object(
                hardware_collectors.os,
                "read",
                side_effect=[
                    _u64_bytes(100),
                    _u64_bytes(200),
                    _u64_bytes(3),
                    _u64_bytes(4),
                ],
            ) as read_mock,
            mock.patch.object(hardware_collectors.os, "close") as close_mock,
            mock.patch.object(
                hardware_collectors,
                "poll",
                side_effect=AssertionError("poll should not be used"),
                create=True,
            ),
        ):
            self.collector.start_counters(["dtlb_loads", "dtlb_misses"], 4321)
            totals = self.collector.stop_counters(["dtlb_loads", "dtlb_misses"])
            metrics = hardware_collectors.DTLBCounterMetrics(
                dtlb_loads=totals["dtlb_loads"],
                dtlb_misses=totals["dtlb_misses"],
            )

        self.assertEqual(
            metrics,
            hardware_collectors.DTLBCounterMetrics(
                dtlb_loads=300,
                dtlb_misses=7,
            ),
        )
        list_threads_mock.assert_called_once_with(4321)
        self.assertEqual(open_mock.call_count, 4)
        self.assertEqual(ioctl_mock.call_count, 8)
        self.assertEqual(read_mock.call_count, 4)
        self.assertEqual(close_mock.call_count, 4)

    def test_partial_start_failure_closes_open_fds_and_clears_state(self):
        with (
            mock.patch.object(
                hardware_collectors,
                "_list_task_thread_ids",
                return_value=[11, 12],
            ),
            mock.patch.object(
                hardware_collectors,
                "_perf_event_open",
                side_effect=[
                    101,
                    102,
                    OSError(errno.EPERM, "Operation not permitted"),
                ],
            ),
            mock.patch.object(hardware_collectors, "_read_perf_event_paranoid", return_value="4"),
            mock.patch.object(hardware_collectors, "ioctl") as ioctl_mock,
            mock.patch.object(hardware_collectors.os, "close") as close_mock,
        ):
            with self.assertRaisesRegex(PermissionError, "perf_event_paranoid=4"):
                self.collector.start_counters(["dtlb_loads", "dtlb_misses"], 4321)

        self.assertEqual(self.collector._active_counters, {})
        self.assertEqual(close_mock.call_args_list, [mock.call(101), mock.call(102)])
        self.assertGreaterEqual(ioctl_mock.call_count, 2)

    def test_unsupported_counter_fails_fast(self):
        with (
            mock.patch.object(
                hardware_collectors,
                "_list_task_thread_ids",
                return_value=[11],
            ),
            mock.patch.object(
                hardware_collectors,
                "_perf_event_open",
                side_effect=OSError(errno.EINVAL, "Invalid argument"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "does not support the dtlb_loads"):
                self.collector.start_counters(["dtlb_loads"], 4321)

    def test_perf_event_attr_matches_current_kernel_header_size(self):
        self.assertEqual(
            hardware_collectors.ctypes.sizeof(hardware_collectors._PerfEventAttr),
            136,
        )


class ReplayHardwareCollectorsSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.collector = HardwareCollector()

    def tearDown(self) -> None:
        self.collector.close()

    @unittest.skipUnless(
        os.environ.get("RUN_REPLAY_DTLB_SMOKE") == "1",
        "Set RUN_REPLAY_DTLB_SMOKE=1 to enable the root perf smoke test.",
    )
    def test_dtlb_counters_count_a_memory_touch_workload(self):
        if os.geteuid() != 0:
            self.skipTest("Replay DTLB smoke test requires root.")

        ready_r, ready_w = os.pipe()
        go_r, go_w = os.pipe()
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import os, sys\n"
                    "ready_fd = int(sys.argv[1])\n"
                    "go_fd = int(sys.argv[2])\n"
                    "os.write(ready_fd, b'1')\n"
                    "os.close(ready_fd)\n"
                    "os.read(go_fd, 1)\n"
                    "os.close(go_fd)\n"
                    "buf = bytearray(64 * 1024 * 1024)\n"
                    "for _ in range(8):\n"
                    "    for idx in range(0, len(buf), 4096):\n"
                    "        buf[idx] = (buf[idx] + 1) % 256\n"
                ),
                str(ready_w),
                str(go_r),
            ],
            pass_fds=(ready_w, go_r),
        )
        os.close(ready_w)
        os.close(go_r)

        try:
            self.assertEqual(os.read(ready_r, 1), b"1")
            try:
                self.collector.start_counters(["dtlb_loads", "dtlb_misses"], child.pid)
            except (PermissionError, RuntimeError) as error:
                self.skipTest(f"Replay DTLB counters unavailable on this host: {error}")

            os.write(go_w, b"1")
            return_code = child.wait(timeout=30)
            self.assertEqual(return_code, 0)
            totals = self.collector.stop_counters(["dtlb_loads", "dtlb_misses"])
            metrics = hardware_collectors.DTLBCounterMetrics(
                dtlb_loads=totals["dtlb_loads"],
                dtlb_misses=totals["dtlb_misses"],
            )
        finally:
            os.close(ready_r)
            os.close(go_w)
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

        self.assertGreater(metrics.dtlb_loads, 0)
        self.assertGreaterEqual(metrics.dtlb_misses, 0)
        self.assertLessEqual(metrics.dtlb_misses, metrics.dtlb_loads)


if __name__ == "__main__":
    unittest.main()
