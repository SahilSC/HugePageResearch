from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "temp_data_analysis" / "render_gups_split_harness.py"

SPEC = importlib.util.spec_from_file_location(
    "render_gups_split_harness",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load render_gups_split_harness.py for testing")

RENDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RENDER
SPEC.loader.exec_module(RENDER)


class RenderGUPSSplitHarnessTest(unittest.TestCase):
    def test_load_row_summaries_omits_unsplittable_split_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            results_path = Path(tmpdir) / "results.parquet"
            pl.DataFrame(
                [
                    {
                        "row_kind": "base_pages",
                        "row_label": "base_pages",
                        "runtime_s_1": 1.0,
                        "runtime_s_2": 1.2,
                        "gups_1": 2.0,
                        "gups_2": 1.8,
                        "split_successes_1": 0,
                        "split_successes_2": 0,
                        "split_max_attempts_1": 0,
                        "split_max_attempts_2": 0,
                    },
                    {
                        "row_kind": "split_only",
                        "row_label": "hot page #1",
                        "runtime_s_1": 0.8,
                        "runtime_s_2": 0.9,
                        "gups_1": 2.5,
                        "gups_2": 2.4,
                        "split_successes_1": 2,
                        "split_successes_2": 2,
                        "split_max_attempts_1": 1,
                        "split_max_attempts_2": 1,
                    },
                    {
                        "row_kind": "split_only",
                        "row_label": "random page #1",
                        "runtime_s_1": 0.95,
                        "runtime_s_2": 0.96,
                        "gups_1": 2.1,
                        "gups_2": 2.0,
                        "split_successes_1": 0,
                        "split_successes_2": 0,
                        "split_max_attempts_1": 2,
                        "split_max_attempts_2": 2,
                    },
                ]
            ).write_parquet(results_path)

            summaries = RENDER.load_row_summaries(results_path)

        self.assertTrue(summaries[0].include_in_main_charts)
        self.assertEqual(summaries[1].display_label, "(4) hot page #1")
        self.assertFalse(summaries[2].include_in_main_charts)

    def test_render_dashboard_writes_html_pngs_and_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            results_path = base / "results.parquet"
            metadata_path = base / "results.metadata.json"
            output_dir = base / "dashboard"

            pl.DataFrame(
                [
                    {
                        "row_kind": "base_pages",
                        "row_label": "base_pages",
                        "runtime_s_1": 1.0,
                        "runtime_s_2": 1.2,
                        "gups_1": 2.0,
                        "gups_2": 1.8,
                        "dtlb_loads_1": 100,
                        "dtlb_loads_2": 120,
                        "dtlb_misses_1": 10,
                        "dtlb_misses_2": 12,
                        "split_successes_1": 0,
                        "split_successes_2": 0,
                        "split_max_attempts_1": 0,
                        "split_max_attempts_2": 0,
                    },
                    {
                        "row_kind": "split_only",
                        "row_label": "hot page #1",
                        "runtime_s_1": 0.8,
                        "runtime_s_2": 0.9,
                        "gups_1": 2.5,
                        "gups_2": 2.4,
                        "dtlb_loads_1": 80,
                        "dtlb_loads_2": 82,
                        "dtlb_misses_1": 8,
                        "dtlb_misses_2": 7,
                        "split_successes_1": 2,
                        "split_successes_2": 2,
                        "split_max_attempts_1": 1,
                        "split_max_attempts_2": 1,
                    },
                    {
                        "row_kind": "split_only",
                        "row_label": "random page #1",
                        "runtime_s_1": 0.95,
                        "runtime_s_2": 0.96,
                        "gups_1": 2.1,
                        "gups_2": 2.0,
                        "dtlb_loads_1": 90,
                        "dtlb_loads_2": 92,
                        "dtlb_misses_1": 9,
                        "dtlb_misses_2": 9,
                        "split_successes_1": 0,
                        "split_successes_2": 0,
                        "split_max_attempts_1": 2,
                        "split_max_attempts_2": 2,
                    },
                ]
            ).write_parquet(results_path)

            metadata_path.write_text(
                json.dumps(
                    {
                        "output": str(results_path),
                        "breakpoints_path": "/tmp/breakpoints.parquet",
                        "commands_log_path": "/tmp/run_commands.log",
                        "artifacts_dir": "/tmp/artifacts",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            outputs = RENDER.render_dashboard(
                results_path=results_path,
                metadata_path=metadata_path,
                output_dir=output_dir,
                label="verification",
            )

            self.assertTrue(outputs["runtime_png"].is_file())
            self.assertTrue(outputs["gups_png"].is_file())
            self.assertTrue(outputs["html"].is_file())
            config_text = outputs["config_md"].read_text(encoding="utf-8")
            self.assertIn("Omitted Split-Only Rows", config_text)
            self.assertIn("random page #1", config_text)


if __name__ == "__main__":
    unittest.main()
