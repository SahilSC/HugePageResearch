# ruff: noqa: E402
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from data_collection import GenericCollectorConfig
from kernmlops_config.hugepage_harness import HugepageHarnessConfig


class _FakeBenchmark:
    def name(self) -> str:
        return "redis"

    def redis_server_name(self) -> str:
        return "redis-server"


class THPHarnessWiringTest(unittest.TestCase):
    def test_get_hooks_threads_hugepage_config_and_process_name(self):
        collector = GenericCollectorConfig(
            hooks=[
                "thp_trace",
                "smaps_harness",
                "thp_intervention",
                "vmstat_harness",
                "proc_maps",
                "smaps_rollup_hook",
                "smaps_hook",
            ]
        )
        harness = HugepageHarnessConfig(
            redis_name_regex="redis-server",
            vmstat_interval_ms=123,
            smaps_rollup_interval_ms=456,
            smaps_vma_interval_ms=789,
        )

        hooks = collector.get_hooks(
            benchmark=_FakeBenchmark(),
            hugepage_harness=harness,
        )

        hook_names = [hook.name() for hook in hooks]
        self.assertEqual(
            hook_names,
            [
                "thp_trace",
                "smaps_harness",
                "thp_intervention",
                "vmstat_harness",
                "proc_maps",
                "smaps_rollup_hook",
                "smaps_hook",
            ],
        )
        self.assertEqual(hooks[1].pid_regex.pattern, "redis-server")
        self.assertEqual(hooks[2].pid_regex.pattern, "redis-server")
        self.assertEqual(hooks[3].sample_interval_ns, 123_000_000)
        self.assertEqual(hooks[4].pid_regex.pattern, "redis-server")
        self.assertEqual(hooks[5].pid_regex.pattern, "redis-server")
        self.assertEqual(hooks[6].pid_regex.pattern, "redis-server")


if __name__ == "__main__":
    unittest.main()
