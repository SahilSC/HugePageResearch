# ruff: noqa: E402
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from replay import generate_gups_breakpoints


class GenerateGUPSBreakpointsTest(unittest.TestCase):
    def test_load_page_summaries_requires_repeat_stability(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path = Path(tmpdir) / "page_summary.csv"
            summary_path.write_text(
                "\n".join(
                    [
                        "repeat_index,page_index,start_offset_bytes,update_count,first_touch_update,last_touch_update",
                        "0,7,14680064,10,0,9",
                        "1,7,14680064,11,0,10",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "not deterministic"):
                generate_gups_breakpoints.load_page_summaries(summary_path)

    def test_generate_breakpoint_rows_emits_base_no_break_hot_and_random(self):
        summaries = [
            generate_gups_breakpoints.PageSummary(9, 90, 0, 89),
            generate_gups_breakpoints.PageSummary(7, 70, 10, 79),
            generate_gups_breakpoints.PageSummary(5, 50, 20, 69),
            generate_gups_breakpoints.PageSummary(3, 30, 30, 59),
        ]

        rows = generate_gups_breakpoints.generate_breakpoint_rows(
            summaries,
            hot_pages=2,
            random_rows=1,
            random_seed=0,
        )

        self.assertEqual(rows[0]["row_kind"], "base_pages")
        self.assertEqual(rows[1]["row_kind"], "no_break")
        self.assertEqual(rows[2]["row_label"], "hot page #1")
        self.assertEqual(rows[3]["row_label"], "hot page #2")
        self.assertEqual(rows[4]["row_label"], "random page #1")
        self.assertEqual(rows[2]["target_column"], "hp_000009")
        self.assertEqual(rows[2]["target_break_after_page_accesses"], 1)
        self.assertEqual(rows[3]["target_column"], "hp_000007")
        self.assertIn(rows[4]["target_column"], {"hp_000005", "hp_000003"})
        self.assertEqual(rows[0]["hp_000009"], 0)
        self.assertEqual(rows[1]["hp_000009"], 91)
        self.assertEqual(rows[2]["hp_000009"], 0)
        self.assertEqual(rows[2]["hp_000007"], 71)


if __name__ == "__main__":
    unittest.main()
