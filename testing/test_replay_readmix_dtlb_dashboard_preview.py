from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "temp_data_analysis" / "replay_readmix_15000_221_dtlb_analysis.py"

SPEC = importlib.util.spec_from_file_location(
    "replay_readmix_15000_221_dtlb_analysis",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        "Could not load replay_readmix_15000_221_dtlb_analysis.py for testing"
    )

MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReplayReadmixDtlbDashboardPreviewTest(unittest.TestCase):
    def test_replace_inline_json_script_embeds_pretty_json(self):
        html = """
<script id="readmixDtlbMetricsData" type="application/json">
{}
</script>
"""

        updated = MODULE._replace_inline_json_script(
            html,
            "readmixDtlbMetricsData",
            {"rows": 5, "label": "base_pages"},
        )

        self.assertIn('"rows": 5', updated)
        self.assertIn('"label": "base_pages"', updated)
        self.assertIn('id="readmixDtlbMetricsData"', updated)

    def test_build_dashboard_html_mentions_dtlb_focus(self):
        html = MODULE._build_dashboard_html(
            title="Example",
            header_title="Header",
            header_description="Description",
            results_relpath="data/example.parquet",
        )

        self.assertIn("data/example.parquet", html)
        self.assertIn("Section 1 — dTLB Miss Delta", html)
        self.assertIn("dTLB misses delta vs no_break", html)


if __name__ == "__main__":
    unittest.main()
