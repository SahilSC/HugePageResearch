# ruff: noqa: E402
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from data_collection.page_access import PageAccessResult, ResolvedPhysicalPage
from data_collection.bpf_instrumentation.vaptr_hook import VAPtrHook


class VAPtrPidCacheTest(unittest.TestCase):
    def test_get_redis_pid_caches_process_lookup(self):
        page_access_tracker = mock.Mock()
        hook = VAPtrHook(page_access_tracker=page_access_tracker)

        with (
            mock.patch.object(hook, "_discover_redis_pid", return_value=321) as discover,
            mock.patch.object(hook, "_redis_pid_alive", return_value=True) as alive,
        ):
            self.assertEqual(hook._get_redis_pid(), 321)
            self.assertEqual(hook._get_redis_pid(), 321)

        discover.assert_called_once()
        alive.assert_called_once_with(321)
        page_access_tracker.invalidate_pid.assert_not_called()

    def test_get_redis_pid_invalidates_and_rediscovers_when_pid_dies(self):
        page_access_tracker = mock.Mock()
        hook = VAPtrHook(page_access_tracker=page_access_tracker)
        hook.redis_pid = 111

        with (
            mock.patch.object(hook, "_redis_pid_alive", return_value=False) as alive,
            mock.patch.object(hook, "_discover_redis_pid", return_value=222) as discover,
        ):
            self.assertEqual(hook._get_redis_pid(), 222)

        alive.assert_called_once_with(111)
        discover.assert_called_once()
        page_access_tracker.invalidate_pid.assert_called_once_with(111)


class VAPtrSamplingOrderTest(unittest.TestCase):
    def test_poll_reads_previous_interval_before_refreshing_addresses(self):
        page_access_tracker = mock.Mock()
        page_access_tracker.page_size = 4096
        hook = VAPtrHook(
            key_names=["user1"],
            page_access_tracker=page_access_tracker,
        )
        first_resolved = ResolvedPhysicalPage(
            pid=321,
            virtual_address=0x1000,
            mapped_pfn=11,
            tracking_pfn=11,
            physical_page_addr=11 * 4096,
            tracking_physical_page_addr=11 * 4096,
        )
        second_resolved = ResolvedPhysicalPage(
            pid=321,
            virtual_address=0x2000,
            mapped_pfn=22,
            tracking_pfn=22,
            physical_page_addr=22 * 4096,
            tracking_physical_page_addr=22 * 4096,
        )
        page_access_tracker.resolve_many.side_effect = [
            [first_resolved],
            [second_resolved],
        ]
        page_access_tracker.read_many.return_value = [
            PageAccessResult(
                pid=321,
                virtual_address=0x1000,
                mapped_pfn=11,
                tracking_pfn=11,
                physical_page_addr=11 * 4096,
                tracking_physical_page_addr=11 * 4096,
                page_idle=True,
                access_bit=False,
                access_bit_valid=True,
            )
        ]

        vaptr_runs = [
            subprocess.CompletedProcess(
                args=["redis-cli"],
                returncode=0,
                stdout="user1\n0x1000\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["redis-cli"],
                returncode=0,
                stdout="user1\n0x2000\n",
                stderr="",
            ),
        ]
        with mock.patch.object(hook, "_run_vaptr", side_effect=vaptr_runs):
            with mock.patch.object(hook, "_get_redis_pid", return_value=321):
                hook.poll()
                hook.poll()

        self.assertEqual(page_access_tracker.mock_calls, [
            mock.call.resolve_many([mock.ANY]),
            mock.call.arm_many([first_resolved]),
            mock.call.read_many([first_resolved]),
            mock.call.resolve_many([mock.ANY]),
            mock.call.arm_many([second_resolved]),
        ])
        self.assertEqual(len(hook.samples), 2)
        self.assertEqual(hook.samples[0].address, "0x1000")
        self.assertFalse(hook.samples[0].access_bit_valid)
        self.assertEqual(hook.samples[1].address, "0x1000")
        self.assertTrue(hook.samples[1].access_bit_valid)
        self.assertFalse(hook.samples[1].access_bit)


if __name__ == "__main__":
    unittest.main()
