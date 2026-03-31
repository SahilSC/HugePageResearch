# ruff: noqa: E402
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

import data_collection
import data_collection.bpf_instrumentation as bpf


class BPFInstrumentationInitTest(unittest.TestCase):
    def test_hook_names_keep_expected_order(self):
        self.assertEqual(bpf.hook_names()[0], "file_data")
        self.assertEqual(bpf.hook_names()[-1], "vaptr")
        self.assertIn("perf", bpf.hook_names())
        self.assertIn("thp_trace", bpf.hook_names())
        self.assertIn("smaps_harness", bpf.hook_names())
        self.assertIn("vmstat_harness", bpf.hook_names())
        self.assertIn("thp_intervention", bpf.hook_names())
        self.assertIn("proc_maps", bpf.hook_names())
        self.assertIn("smaps_hook", bpf.hook_names())

    def test_get_hook_returns_registered_type(self):
        hook_type = bpf.get_hook("memory_usage")
        self.assertIsNotNone(hook_type)
        self.assertEqual(hook_type.__name__, "MemoryUsageHook")

    def test_importing_data_collection_exposes_registry_and_helpers(self):
        self.assertIs(data_collection.bpf, bpf)
        self.assertEqual(data_collection.PageAccessTracker.__name__, "PageAccessTracker")


if __name__ == "__main__":
    unittest.main()
