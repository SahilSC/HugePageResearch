from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "temp_data_analysis" / "basepages_10keys_analysis.py"
DASHBOARD_PATH = ROOT / "temp_data_analysis" / "basepages_10keys_dashboard.html"

SPEC = importlib.util.spec_from_file_location(
    "basepages_10keys_analysis",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load basepages_10keys_analysis.py for testing")

BASEPAGES_ANALYSIS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASEPAGES_ANALYSIS)


class BasepagesDashboardPreviewTest(unittest.TestCase):
    def test_replace_inline_json_script_embeds_pretty_json(self):
        html = """
<script id="basepagesMetricsData" type="application/json">
{}
</script>
"""

        updated = BASEPAGES_ANALYSIS._replace_inline_json_script(
            html,
            "basepagesMetricsData",
            {"rows": 12, "label": "base_pages"},
        )

        self.assertIn('"rows": 12', updated)
        self.assertIn('"label": "base_pages"', updated)
        self.assertIn('id="basepagesMetricsData"', updated)

    def test_dashboard_template_contains_inline_preview_blocks(self):
        dashboard_html = DASHBOARD_PATH.read_text(encoding="utf-8")

        metrics_html = BASEPAGES_ANALYSIS._replace_inline_json_script(
            dashboard_html,
            "basepagesMetricsData",
            {"rows": 12},
        )
        rows_html = BASEPAGES_ANALYSIS._replace_inline_json_script(
            metrics_html,
            "basepagesRowsData",
            [{"row_type": "base_pages", "short_label": "base_pages"}],
        )

        self.assertIn('"rows": 12', rows_html)
        self.assertIn('"row_type": "base_pages"', rows_html)
        self.assertIn('"short_label": "base_pages"', rows_html)


if __name__ == "__main__":
    unittest.main()
