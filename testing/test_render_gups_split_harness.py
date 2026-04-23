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
    def test_load_row_summaries_normalizes_against_base_pages(self):
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
                        "row_kind": "no_break",
                        "row_label": "no_break",
                        "runtime_s_1": 0.8,
                        "runtime_s_2": 0.9,
                        "gups_1": 2.5,
                        "gups_2": 2.4,
                        "split_successes_1": 0,
                        "split_successes_2": 0,
                        "split_max_attempts_1": 0,
                        "split_max_attempts_2": 0,
                    },
                ]
            ).write_parquet(results_path)

            summaries = RENDER.load_row_summaries(results_path)

        self.assertEqual(summaries[0].runtime_pct_vs_base_pages_mean, 0.0)
        self.assertEqual(summaries[0].speedup_pct_vs_base_pages_mean, 0.0)
        self.assertAlmostEqual(
            summaries[1].runtime_pct_vs_base_pages_mean,
            (((0.8 / 1.0) - 1.0) * 100.0 + ((0.9 / 1.2) - 1.0) * 100.0) / 2.0,
        )
        self.assertAlmostEqual(
            summaries[1].speedup_pct_vs_base_pages_mean,
            (((1.0 / 0.8) - 1.0) * 100.0 + ((1.2 / 0.9) - 1.0) * 100.0) / 2.0,
        )

    def test_load_row_summaries_requires_base_pages_baseline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            results_path = Path(tmpdir) / "results.parquet"
            pl.DataFrame(
                [
                    {
                        "row_kind": "no_break",
                        "row_label": "no_break",
                        "runtime_s_1": 0.8,
                        "gups_1": 2.5,
                        "split_successes_1": 0,
                        "split_max_attempts_1": 0,
                    }
                ]
            ).write_parquet(results_path)

            with self.assertRaisesRegex(RuntimeError, "base_pages"):
                RENDER.load_row_summaries(results_path)

    def test_load_row_summaries_fails_fast_for_missing_runtime_run(self):
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
                        "row_kind": "no_break",
                        "row_label": "no_break",
                        "runtime_s_1": 0.8,
                        "runtime_s_2": None,
                        "gups_1": 2.5,
                        "gups_2": 2.4,
                        "split_successes_1": 0,
                        "split_successes_2": 0,
                        "split_max_attempts_1": 0,
                        "split_max_attempts_2": 0,
                    },
                ]
            ).write_parquet(results_path)

            with self.assertRaisesRegex(RuntimeError, "missing required runtime_s"):
                RENDER.load_row_summaries(results_path)

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

    def test_load_row_summaries_labels_multi_page_expected_successes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            results_path = Path(tmpdir) / "results.parquet"
            pl.DataFrame(
                [
                    {
                        "row_kind": "base_pages",
                        "row_label": "base_pages",
                        "target_page_count": 0,
                        "page_group": "",
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
                        "row_kind": "split_multi",
                        "row_label": "hot 2 pages",
                        "target_page_count": 2,
                        "page_group": "hot",
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
                        "row_kind": "split_multi",
                        "row_label": "random 2 pages",
                        "target_page_count": 2,
                        "page_group": "random",
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

        self.assertEqual(summaries[1].display_label, "(4/4) hot 2 pages")
        self.assertEqual(summaries[1].split_expected_successes, 4)
        self.assertEqual(summaries[1].target_page_count, 2)
        self.assertEqual(summaries[1].page_group, "hot")
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
            self.assertTrue(outputs["runtime_pct_png"].is_file())
            self.assertTrue(outputs["speedup_pct_png"].is_file())
            self.assertTrue(outputs["gups_png"].is_file())
            self.assertTrue(outputs["html"].is_file())
            config_text = outputs["config_md"].read_text(encoding="utf-8")
            self.assertIn("baseline row: `base_pages`", config_text)
            self.assertIn("runtime percent formula", config_text)
            self.assertIn("Omitted Split-Only Rows", config_text)
            self.assertIn("random page #1", config_text)
            html_text = outputs["html"].read_text(encoding="utf-8")
            self.assertIn("The normalization baseline is always <code>base_pages</code>.", html_text)
            self.assertIn("Runtime % vs base_pages", html_text)
            self.assertIn("Speedup % vs base_pages", html_text)

    def test_render_dashboard_writes_multi_page_count_curves(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            results_path = base / "results.parquet"
            metadata_path = base / "results.metadata.json"
            output_dir = base / "dashboard"

            rows = [
                {
                    "row_kind": "base_pages",
                    "row_label": "base_pages",
                    "target_page_count": 0,
                    "page_group": "",
                    "runtime_s_1": 1.0,
                    "runtime_s_2": 1.2,
                    "gups_1": 2.0,
                    "gups_2": 1.8,
                    "split_successes_1": 0,
                    "split_successes_2": 0,
                    "split_max_attempts_1": 0,
                    "split_max_attempts_2": 0,
                }
            ]
            for page_group in ("hot", "random"):
                for count in (2, 3):
                    rows.append(
                        {
                            "row_kind": "split_multi",
                            "row_label": f"{page_group} {count} pages",
                            "target_page_count": count,
                            "page_group": page_group,
                            "runtime_s_1": 0.9 + (count * 0.01),
                            "runtime_s_2": 1.0 + (count * 0.01),
                            "gups_1": 2.3,
                            "gups_2": 2.2,
                            "split_successes_1": count,
                            "split_successes_2": count,
                            "split_max_attempts_1": 1,
                            "split_max_attempts_2": 1,
                        }
                    )
            pl.DataFrame(rows).write_parquet(results_path)

            metadata_path.write_text(
                json.dumps(
                    {
                        "output": str(results_path),
                        "split_mode": "pre_split",
                    }
                ),
                encoding="utf-8",
            )

            outputs = RENDER.render_dashboard(
                results_path=results_path,
                metadata_path=metadata_path,
                output_dir=output_dir,
                label="multi",
            )

            count_curve_pngs = outputs["count_curve_pngs"]
            self.assertEqual(len(count_curve_pngs), 4)
            for png_path in count_curve_pngs:
                self.assertTrue(png_path.is_file())
            self.assertTrue((output_dir / "runtime_by_broken_page_count.png").is_file())
            self.assertTrue(
                (output_dir / "runtime_pct_vs_base_pages_by_broken_page_count.png").is_file()
            )
            html_text = outputs["html"].read_text(encoding="utf-8")
            self.assertIn("Broken Page Count Curves", html_text)
            self.assertIn("(4/4) hot 2 pages", html_text)


if __name__ == "__main__":
    unittest.main()
