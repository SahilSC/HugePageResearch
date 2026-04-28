from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "temp_data_analysis" / "replay3min22rowsdashboard.py"

SPEC = importlib.util.spec_from_file_location(
    "replay3min22rowsdashboard",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load replay3min22rowsdashboard.py for testing")

MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Replay3Min22RowsRuntimeDashboardPreviewTest(unittest.TestCase):
    def test_replace_inline_json_script_embeds_pretty_json(self):
        html = """
<script id="replay3minRuntimeMetricsData" type="application/json">
{}
</script>
"""

        updated = MODULE._replace_inline_json_script(
            html,
            "replay3minRuntimeMetricsData",
            {"rows": 22, "label": "base_pages"},
        )

        self.assertIn('"rows": 22', updated)
        self.assertIn('"label": "base_pages"', updated)
        self.assertIn('id="replay3minRuntimeMetricsData"', updated)

    def test_build_dashboard_html_mentions_runtime_only_result_file(self):
        html = MODULE._build_dashboard_html()

        self.assertIn("Replay 3-Minute 22-Row Runtime Dashboard", html)
        self.assertIn("data/replay_3min_results_22rows.parquet", html)
        self.assertNotIn("data/replay_3min_dtlb_results_22rows.parquet", html)


if __name__ == "__main__":
    unittest.main()
