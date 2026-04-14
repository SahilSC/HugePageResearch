from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "temp_data_analysis" / "replay_3min_22rows_analysis.py"

SPEC = importlib.util.spec_from_file_location(
    "replay_3min_22rows_analysis",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load replay_3min_22rows_analysis.py for testing")

ANALYSIS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYSIS)


class Replay3MinDashboardPreviewTest(unittest.TestCase):
    def test_replace_inline_json_script_embeds_pretty_json(self):
        html = """
<script id="replay3minMetricsData" type="application/json">
{}
</script>
"""

        updated = ANALYSIS._replace_inline_json_script(
            html,
            "replay3minMetricsData",
            {"rows": 22, "label": "base_pages"},
        )

        self.assertIn('"rows": 22', updated)
        self.assertIn('"label": "base_pages"', updated)
        self.assertIn('id="replay3minMetricsData"', updated)

    def test_build_dashboard_html_mentions_both_result_files(self):
        html = ANALYSIS._build_dashboard_html()

        self.assertIn("Replay 3-Minute 22-Row Dashboard", html)
        self.assertIn("data/replay_3min_results_22rows.parquet", html)
        self.assertIn("data/replay_3min_dtlb_results_22rows.parquet", html)
        self.assertIn("replay3minRowsData", html)


if __name__ == "__main__":
    unittest.main()
