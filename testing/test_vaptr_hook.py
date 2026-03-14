# ruff: noqa: E402
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

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


if __name__ == "__main__":
    unittest.main()
