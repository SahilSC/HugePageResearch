# ruff: noqa: E402
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from replay import replay_gups


class ReplayGUPSTest(unittest.TestCase):
    def test_write_split_schedule_skips_baselines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            schedule_path = Path(tmpdir) / "split_schedule.csv"

            self.assertIsNone(
                replay_gups._write_split_schedule(
                    {
                        "row_kind": "base_pages",
                        "target_page_index": -1,
                    },
                    repeats=4,
                    schedule_path=schedule_path,
                )
            )

    def test_run_benchmark_uses_base_pages_no_break_and_split_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            output_path = base / "results.parquet"
            artifacts_dir = base / "artifacts"
            breakpoints_df = pl.DataFrame(
                [
                    {
                        "row_kind": "base_pages",
                        "row_label": "base_pages",
                        "target_page_index": -1,
                        "target_column": "",
                        "target_update_count": 0,
                        "target_break_after_page_accesses": -1,
                        "hp_000007": 0,
                    },
                    {
                        "row_kind": "no_break",
                        "row_label": "no_break",
                        "target_page_index": -1,
                        "target_column": "",
                        "target_update_count": 0,
                        "target_break_after_page_accesses": -1,
                        "hp_000007": 91,
                    },
                    {
                        "row_kind": "split_only",
                        "row_label": "hot page #1",
                        "target_page_index": 7,
                        "target_column": "hp_000007",
                        "target_update_count": 90,
                        "target_break_after_page_accesses": 1,
                        "hp_000007": 0,
                    },
                ]
            )

            calls: list[dict[str, object]] = []

            def fake_run_once(**kwargs):
                calls.append(kwargs)
                return (
                    {
                        "repeat_count": 4,
                        "runtime_s_mean": 1.25,
                        "runtime_s_total": 5.0,
                        "gups_mean": 2.5,
                        "gups_min": 2.4,
                        "gups_max": 2.6,
                        "split_events": 4 if kwargs["split_schedule_path"] else 0,
                        "split_successes": 4 if kwargs["split_schedule_path"] else 0,
                        "split_failures": 0,
                        "split_syscall_attempts": 4 if kwargs["split_schedule_path"] else 0,
                        "split_max_attempts": 1 if kwargs["split_schedule_path"] else 0,
                        "split_total_wall_ms": 8.0 if kwargs["split_schedule_path"] else 0.0,
                        "split_max_wall_ms": 2.0 if kwargs["split_schedule_path"] else 0.0,
                    },
                    {"dtlb_loads": 123, "dtlb_misses": 45},
                    "gups --results ...",
                    kwargs["run_dir"] / "gups_results.jsonl",
                    (
                        kwargs["run_dir"] / "split_events.csv"
                        if kwargs["split_schedule_path"] is not None
                        else None
                    ),
                )

            with (
                mock.patch.object(replay_gups, "_resolve_gups_binary", return_value=base / "gups"),
                mock.patch.object(replay_gups, "setup_system", return_value=mock.sentinel.system),
                mock.patch.object(replay_gups, "teardown_system") as teardown_mock,
                mock.patch.object(replay_gups, "_set_thp_enabled_mode") as set_thp_mode_mock,
                mock.patch.object(replay_gups, "_run_gups_once", side_effect=fake_run_once),
                mock.patch.object(replay_gups, "HardwareCollector") as hw_collector_cls,
            ):
                hw_collector_cls.return_value = mock.Mock()
                metadata_path = replay_gups.run_benchmark(
                    breakpoints_df=breakpoints_df,
                    output=output_path,
                    artifacts_dir=artifacts_dir,
                    benchmark_dir=base,
                    table_size_gib=None,
                    table_size_mib=64,
                    repeats=4,
                    updates_multiplier=4,
                    stream_seed=7,
                    runs=1,
                    collectors=("dtlb_loads", "dtlb_misses"),
                )

            self.assertEqual(set_thp_mode_mock.call_args_list[0], mock.call("never"))
            self.assertEqual(set_thp_mode_mock.call_args_list[1], mock.call("always"))
            self.assertEqual(set_thp_mode_mock.call_args_list[2], mock.call("always"))
            self.assertIsNone(calls[0]["split_schedule_path"])
            self.assertIsNone(calls[1]["split_schedule_path"])
            self.assertIsNotNone(calls[2]["split_schedule_path"])
            schedule_text = Path(calls[2]["split_schedule_path"]).read_text(encoding="utf-8")
            self.assertIn("0,7,1,hot page #1", schedule_text)
            self.assertTrue(output_path.is_file())
            results_df = pl.read_parquet(output_path)
            self.assertEqual(results_df.height, 3)
            self.assertEqual(results_df[2, "split_successes_1"], 4)
            self.assertEqual(results_df[2, "dtlb_loads_1"], 123)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["table_size_mib"], 64)
            teardown_mock.assert_called_once_with(mock.sentinel.system)


if __name__ == "__main__":
    unittest.main()
