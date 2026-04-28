from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "temp_data_analysis" / "replay_runtime_experiment_dashboard.py"

SPEC = importlib.util.spec_from_file_location(
    "replay_runtime_experiment_dashboard",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        "Could not load replay_runtime_experiment_dashboard.py for testing"
    )

MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReplayReadmixDashboardPreviewTest(unittest.TestCase):
    def test_replace_inline_json_script_embeds_pretty_json(self):
        html = """
<script id="runtimeExperimentManifestData" type="application/json">
{}
</script>
"""

        updated = MODULE._replace_inline_json_script(
            html,
            "runtimeExperimentManifestData",
            {"experiment_name": "control", "capture": {"record_count": 15000}},
        )

        self.assertIn('"experiment_name": "control"', updated)
        self.assertIn('"record_count": 15000', updated)
        self.assertIn('id="runtimeExperimentManifestData"', updated)

    def test_build_dashboard_html_mentions_runtime_file(self):
        html = MODULE._build_dashboard_html(
            title="Example",
            results_relpath="data/example.parquet",
        )

        self.assertIn("data/example.parquet", html)
        self.assertIn("Section 1 — Runtime Overview", html)
        self.assertIn("Section 5 — Experiment Config", html)
        self.assertIn("runtimeErrorBarPlugin", html)
        self.assertNotIn("dtlb_loads", html)

    def test_runtime_chart_rows_skip_zero_success_split_rows(self):
        records = [
            {
                "row_type": "base_pages",
                "short_label": "base_pages",
                "split_successes_total": 0,
            },
            {
                "row_type": "no_break",
                "short_label": "no_break",
                "split_successes_total": 0,
            },
            {
                "row_type": "hot_split_only",
                "short_label": "hot#1 (2/3)",
                "split_successes_total": 2,
            },
            {
                "row_type": "random_split_only",
                "short_label": "rand#1 (0/3)",
                "split_successes_total": 0,
            },
        ]

        chart_rows = MODULE._runtime_chart_rows(records)

        self.assertEqual(
            [record["short_label"] for record in chart_rows],
            ["base_pages", "no_break", "hot#1 (2/3)"],
        )


if __name__ == "__main__":
    unittest.main()
