from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "temp_data_analysis" / "replay_cap1m_32rows_analysis.py"

SPEC = importlib.util.spec_from_file_location(
    "replay_cap1m_32rows_analysis",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load replay_cap1m_32rows_analysis.py for testing")

CAP1M_ANALYSIS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAP1M_ANALYSIS)


class ReplayCap1mDashboardPreviewTest(unittest.TestCase):
    def test_replace_inline_json_script_embeds_pretty_json(self):
        html = """
<script id="basepagesMetricsData" type="application/json">
{}
</script>
"""

        updated = CAP1M_ANALYSIS._replace_inline_json_script(
            html,
            "basepagesMetricsData",
            {"rows": 32, "label": "base_pages"},
        )

        self.assertIn('"rows": 32', updated)
        self.assertIn('"label": "base_pages"', updated)
        self.assertIn('id="basepagesMetricsData"', updated)

    def test_build_dashboard_template_swaps_file_names_and_title(self):
        html = CAP1M_ANALYSIS._build_dashboard_template()

        self.assertIn("Focused dashboard for the capped 32-row rerun", html)
        self.assertIn("replay_cap1m_32rows_metrics.json", html)
        self.assertIn("replay_cap1m_32rows_rows.json", html)
        self.assertIn(
            "<code>data/replay_cap1m_results_32rows.parquet</code>.",
            html,
        )


if __name__ == "__main__":
    unittest.main()
