from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "temp_data_analysis" / "render_gups_always_vs_never.py"

SPEC = importlib.util.spec_from_file_location(
    "render_gups_always_vs_never",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load render_gups_always_vs_never.py for testing")

GUPS_RENDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GUPS_RENDER
SPEC.loader.exec_module(GUPS_RENDER)


class GUPSRendererTest(unittest.TestCase):
    def test_parse_collection_id_requires_marker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stdout_log = Path(tmpdir) / "collect.stdout.log"
            stdout_log.write_text("nothing here\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "Missing Collection_id"):
                GUPS_RENDER.parse_collection_id(stdout_log)

    def test_load_collection_series_aligns_rollup_and_perf_to_repeats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            data_root = base / "data" / "curated" / "gups"
            collection_id = "20260420T101010101010"
            run_dir = data_root / collection_id
            run_dir.mkdir(parents=True)

            stdout_log = base / "collect.stdout.log"
            stdout_log.write_text(f"Collection_id: {collection_id}\n", encoding="utf-8")

            (run_dir / "gups_results.jsonl").write_text(
                "\n".join(
                    [
                        '{"repeat_index":0,"start_boottime_ns":1000000000,"end_boottime_ns":2000000000,"runtime_s":1.0,"gups":1.25,"table_bytes":67108864,"updates":33554432,"verification_errors":0,"verification_passed":true,"threads":4}',
                        '{"repeat_index":1,"start_boottime_ns":3000000000,"end_boottime_ns":4000000000,"runtime_s":1.1,"gups":1.10,"table_bytes":67108864,"updates":33554432,"verification_errors":0,"verification_passed":true,"threads":4}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            pl.DataFrame(
                [
                    {
                        "transparent_hugepages": "always",
                        "collection_id": collection_id,
                    }
                ]
            ).write_parquet(run_dir / "system_info.end.parquet")

            pl.DataFrame(
                [
                    {"name": "gups", "tgid": 4242},
                ]
            ).write_parquet(run_dir / "process_trace.end.parquet")

            pl.DataFrame(
                [
                    {"tgid": 4242, "ts_ns": 1_500_000_000, "anon_hugepages_kb": 524288},
                    {"tgid": 4242, "ts_ns": 3_500_000_000, "anon_hugepages_kb": 1048576},
                ]
            ).write_parquet(run_dir / "smaps_rollup_samples.end.parquet")

            pl.DataFrame(
                [
                    {"tgid": 4242, "cpu": 0, "ts_uptime_us": 900_000, "cumulative_count": 10},
                    {"tgid": 4242, "cpu": 0, "ts_uptime_us": 1_900_000, "cumulative_count": 30},
                    {"tgid": 4242, "cpu": 0, "ts_uptime_us": 2_900_000, "cumulative_count": 35},
                    {"tgid": 4242, "cpu": 0, "ts_uptime_us": 3_900_000, "cumulative_count": 50},
                ]
            ).write_parquet(run_dir / "dtlb_misses.end.parquet")

            pl.DataFrame(
                [
                    {"tgid": 4242, "cpu": 0, "ts_uptime_us": 900_000, "cumulative_count": 100},
                    {"tgid": 4242, "cpu": 0, "ts_uptime_us": 1_900_000, "cumulative_count": 180},
                    {"tgid": 4242, "cpu": 0, "ts_uptime_us": 2_900_000, "cumulative_count": 200},
                    {"tgid": 4242, "cpu": 0, "ts_uptime_us": 3_900_000, "cumulative_count": 260},
                ]
            ).write_parquet(run_dir / "dtlb_loads.end.parquet")

            series = GUPS_RENDER.load_collection_series(
                mode_label="THP always",
                color="#2a9d8f",
                stdout_log_path=stdout_log,
                data_root=data_root,
            )

            self.assertEqual(series.collection_id, collection_id)
            self.assertEqual(series.thp_mode, "always")
            self.assertEqual([repeat.repeat_index for repeat in series.repeats], [0, 1])
            self.assertEqual(series.anon_hugepages_gib, [0.5, 1.0])
            self.assertEqual(series.dtlb_misses, [20, 15])
            self.assertEqual(series.dtlb_loads, [80, 60])
            self.assertEqual(series.dtlb_misses_scope, "process-local")
            self.assertEqual(series.dtlb_loads_scope, "process-local")

    def test_load_collection_series_falls_back_to_system_wide_perf_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            data_root = base / "data" / "curated" / "gups"
            collection_id = "20260420T202020202020"
            run_dir = data_root / collection_id
            run_dir.mkdir(parents=True)

            stdout_log = base / "collect.stdout.log"
            stdout_log.write_text(f"Collection_id: {collection_id}\n", encoding="utf-8")

            (run_dir / "gups_results.jsonl").write_text(
                '{"repeat_index":0,"start_boottime_ns":1000000000,"end_boottime_ns":2000000000,"runtime_s":1.0,"gups":1.25,"table_bytes":67108864,"updates":33554432,"verification_errors":0,"verification_passed":true,"threads":4}\n',
                encoding="utf-8",
            )

            pl.DataFrame(
                [
                    {
                        "transparent_hugepages": "never",
                        "collection_id": collection_id,
                    }
                ]
            ).write_parquet(run_dir / "system_info.end.parquet")

            pl.DataFrame([{"name": "gups", "tgid": 4242}]).write_parquet(
                run_dir / "process_trace.end.parquet"
            )

            pl.DataFrame(
                [{"tgid": 4242, "ts_ns": 1_500_000_000, "anon_hugepages_kb": 0}]
            ).write_parquet(run_dir / "smaps_rollup_samples.end.parquet")

            pl.DataFrame(
                [
                    {
                        "tgid": 0,
                        "cpu": 0,
                        "ts_uptime_us": 900_000,
                        "cumulative_dtlb_misses": 10,
                    },
                    {
                        "tgid": 0,
                        "cpu": 0,
                        "ts_uptime_us": 1_900_000,
                        "cumulative_dtlb_misses": 30,
                    },
                ]
            ).write_parquet(run_dir / "dtlb_misses.end.parquet")

            pl.DataFrame(
                [
                    {
                        "tgid": 0,
                        "cpu": 0,
                        "ts_uptime_us": 900_000,
                        "cumulative_dtlb_loads": 100,
                    },
                    {
                        "tgid": 0,
                        "cpu": 0,
                        "ts_uptime_us": 1_900_000,
                        "cumulative_dtlb_loads": 180,
                    },
                ]
            ).write_parquet(run_dir / "dtlb_loads.end.parquet")

            series = GUPS_RENDER.load_collection_series(
                mode_label="THP never",
                color="#e76f51",
                stdout_log_path=stdout_log,
                data_root=data_root,
            )

            self.assertEqual(series.dtlb_misses, [20])
            self.assertEqual(series.dtlb_loads, [80])
            self.assertEqual(series.dtlb_misses_scope, "system-wide")
            self.assertEqual(series.dtlb_loads_scope, "system-wide")

    def test_latest_rollup_requires_prior_sample(self):
        points = [GUPS_RENDER.RollupPoint(ts_ns=200, anon_hugepages_kb=4096)]

        with self.assertRaisesRegex(RuntimeError, "No rollup sample exists at or before"):
            GUPS_RENDER.latest_rollup_at_or_before(points, 199)


if __name__ == "__main__":
    unittest.main()
