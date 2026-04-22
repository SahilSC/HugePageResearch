# ruff: noqa: E402
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from data_collection.bpf_instrumentation.smaps_rollup_hook import SmapsRollupHook
from data_schema.thp_harness import SmapsRollupSampleTable


class SmapsRollupHookTest(unittest.TestCase):
    def test_poll_noops_until_target_process_exists(self):
        hook = SmapsRollupHook(process_name="gups")
        hook.load("cid")

        with mock.patch.object(hook, "_find_target_pid", return_value=None):
            hook.poll()

        self.assertEqual(hook.samples, [])

    def test_poll_parses_rollup_into_existing_table_shape(self):
        hook = SmapsRollupHook(process_name="gups")
        hook.load("cid")
        raw_rollup = "\n".join(
            [
                "Rss:                1024 kB",
                "Pss:                 512 kB",
                "Anonymous:           768 kB",
                "Referenced:          256 kB",
                "AnonHugePages:      2048 kB",
                "Ra_pages:              0 kB",
                "Ra_state:              0 kB",
            ]
        )

        with mock.patch.object(hook, "_find_target_pid", return_value=321):
            with mock.patch.object(Path, "read_text", return_value=raw_rollup):
                hook.poll()

        tables = hook.pop_data()
        self.assertEqual(len(tables), 1)
        self.assertIsInstance(tables[0], SmapsRollupSampleTable)
        table = tables[0].table
        self.assertEqual(int(table[0, "tgid"]), 321)
        self.assertEqual(int(table[0, "anon_hugepages_kb"]), 2048)
        self.assertEqual(str(table[0, "collection_id"]), "cid")


if __name__ == "__main__":
    unittest.main()
