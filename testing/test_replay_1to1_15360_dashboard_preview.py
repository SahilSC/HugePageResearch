from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "temp_data_analysis" / "replay_1to1_15360_14rows_dashboard.py"

SPEC = importlib.util.spec_from_file_location(
    "replay_1to1_15360_14rows_dashboard",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        "Could not load replay_1to1_15360_14rows_dashboard.py for testing"
    )

MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Replay1To1DashboardPreviewTest(unittest.TestCase):
    def test_replace_inline_json_script_embeds_pretty_json(self):
        html = """
<script id="replay1to1MetricsData" type="application/json">
{}
</script>
"""

        updated = MODULE._replace_inline_json_script(
            html,
            "replay1to1MetricsData",
            {"rows": 14, "label": "base_pages"},
        )

        self.assertIn('"rows": 14', updated)
        self.assertIn('"label": "base_pages"', updated)
        self.assertIn('id="replay1to1MetricsData"', updated)

    def test_build_dashboard_html_mentions_runtime_only_result_file(self):
        html = MODULE._build_dashboard_html()

        self.assertIn("Replay 1:1 15360/15360 Runtime Dashboard", html)
        self.assertIn("data/replay_1to1_15360_results_14rows.parquet", html)
        self.assertNotIn("dtlb_loads", html)


if __name__ == "__main__":
    unittest.main()
