# ruff: noqa: E402
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "python" / "kernmlops" / "replay" / "generate_gups_breakpoints.py"

SPEC = importlib.util.spec_from_file_location("generate_gups_breakpoints", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load generate_gups_breakpoints.py for testing")

generate_gups_breakpoints = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = generate_gups_breakpoints
SPEC.loader.exec_module(generate_gups_breakpoints)


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
        self.assertEqual(rows[2]["target_page_indices"], "9")
        self.assertEqual(rows[2]["target_page_count"], 1)
        self.assertEqual(rows[2]["page_group"], "hot")
        self.assertEqual(rows[0]["hp_000009"], 0)
        self.assertEqual(rows[1]["hp_000009"], 91)
        self.assertEqual(rows[2]["hp_000009"], 0)
        self.assertEqual(rows[2]["hp_000007"], 71)

    def test_generate_breakpoint_rows_emits_cumulative_multi_page_rows(self):
        summaries = [
            generate_gups_breakpoints.PageSummary(page_index, 100 - page_index, 0, 99)
            for page_index in range(20)
        ]

        rows = generate_gups_breakpoints.generate_breakpoint_rows(
            summaries,
            hot_pages=10,
            random_rows=3,
            random_seed=0,
            multi_page_counts=(2, 10),
            multi_page_groups=("hot", "random"),
        )

        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0]["row_kind"], "base_pages")
        self.assertEqual(rows[1]["row_kind"], "no_break")
        hot_rows = [row for row in rows if row["page_group"] == "hot"]
        random_rows = [row for row in rows if row["page_group"] == "random"]
        self.assertEqual([row["target_page_count"] for row in hot_rows], list(range(2, 11)))
        self.assertEqual([row["target_page_count"] for row in random_rows], list(range(2, 11)))
        self.assertEqual(hot_rows[0]["target_page_indices"], "0,1")
        self.assertEqual(hot_rows[-1]["target_page_indices"], "0,1,2,3,4,5,6,7,8,9")
        self.assertEqual(random_rows[0]["target_page_count"], 2)
        self.assertEqual(
            random_rows[0]["target_page_indices"],
            random_rows[1]["target_page_indices"].rsplit(",", maxsplit=1)[0],
        )
        self.assertEqual(hot_rows[-1]["row_kind"], "split_multi")
        self.assertEqual(hot_rows[-1]["row_label"], "hot 10 pages")

    def test_generate_breakpoint_rows_emits_explicit_cumulative_counts(self):
        summaries = [
            generate_gups_breakpoints.PageSummary(page_index, 200 - page_index, 0, 199)
            for page_index in range(120)
        ]

        rows = generate_gups_breakpoints.generate_breakpoint_rows(
            summaries,
            random_rows=0,
            multi_page_count_list=(10, 100),
            multi_page_groups=("hot",),
        )

        self.assertEqual(
            [row["row_label"] for row in rows],
            ["base_pages", "no_break", "hot 10 pages", "hot 100 pages"],
        )
        self.assertEqual(rows[2]["row_kind"], "split_multi")
        self.assertEqual(rows[2]["target_page_count"], 10)
        self.assertEqual(rows[3]["target_page_count"], 100)
        self.assertEqual(rows[2]["target_page_indices"], "0,1,2,3,4,5,6,7,8,9")
        self.assertEqual(
            rows[3]["target_page_indices"],
            ",".join(str(index) for index in range(100)),
        )
        self.assertEqual(rows[2]["hp_000010"], 191)
        self.assertEqual(rows[3]["hp_000010"], 0)

    def test_generate_breakpoint_rows_emits_explicit_page_chunks(self):
        summaries = [
            generate_gups_breakpoints.PageSummary(page_index, 100 - page_index, 0, 99)
            for page_index in range(40)
        ]

        rows = generate_gups_breakpoints.generate_breakpoint_rows(
            summaries,
            hot_page_chunks=((1, 10), (11, 20), (21, 30)),
            least_hot_page_chunks=((1, 10),),
        )

        self.assertEqual(
            [row["row_label"] for row in rows],
            [
                "base_pages",
                "no_break",
                "hot pages 1 to 10",
                "hot pages 11 to 20",
                "hot pages 21 to 30",
                "least hot pages 1 to 10",
            ],
        )
        self.assertEqual(rows[2]["row_kind"], "split_chunk")
        self.assertEqual(rows[2]["page_group"], "hot")
        self.assertEqual(rows[2]["target_page_count"], 10)
        self.assertEqual(rows[2]["target_page_indices"], "0,1,2,3,4,5,6,7,8,9")
        self.assertEqual(
            rows[3]["target_page_indices"],
            "10,11,12,13,14,15,16,17,18,19",
        )
        self.assertEqual(rows[5]["page_group"], "least_hot")
        self.assertEqual(
            rows[5]["target_page_indices"],
            "39,38,37,36,35,34,33,32,31,30",
        )
        self.assertEqual(rows[2]["hp_000000"], 0)
        self.assertEqual(rows[2]["hp_000010"], 91)
        self.assertEqual(rows[5]["hp_000039"], 0)

    def test_parse_page_chunks_requires_valid_ranges(self):
        self.assertEqual(
            generate_gups_breakpoints.parse_page_chunks("1:10,11:20"),
            ((1, 10), (11, 20)),
        )
        with self.assertRaisesRegex(Exception, "0 < START <= END"):
            generate_gups_breakpoints.parse_page_chunks("10:1")

    def test_parse_multi_page_count_list_requires_valid_counts(self):
        self.assertEqual(
            generate_gups_breakpoints.parse_multi_page_count_list("10,100,1000"),
            (10, 100, 1000),
        )
        with self.assertRaisesRegex(Exception, "positive"):
            generate_gups_breakpoints.parse_multi_page_count_list("10,0")
        with self.assertRaisesRegex(Exception, "unique"):
            generate_gups_breakpoints.parse_multi_page_count_list("10,10")


if __name__ == "__main__":
    unittest.main()
