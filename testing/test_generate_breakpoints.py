# ruff: noqa: E402
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from replay import generate_breakpoints


class GenerateBreakpointsTest(unittest.TestCase):
    def test_parse_log_keeps_only_primary_user_split_targets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "monitor.log"
            log_path.write_text(
                "\n".join(
                    [
                        '1743900000.000001 [0 127.0.0.1:1111] "DEL" "user123"',
                        '1743900000.000002 [0 127.0.0.1:1111] "ZREM" "_indices" "user123"',
                        '1743900000.000003 [0 127.0.0.1:1111] "HGETALL" "user123"',
                        '1743900000.000004 [0 127.0.0.1:1111] "DEL" "other-key"',
                    ]
                ),
                encoding="utf-8",
            )

            counts = generate_breakpoints.parse_log(log_path)

        self.assertEqual(counts, {"user123": 2})

    def test_parser_help_calls_out_replay_split_targets(self):
        parser = generate_breakpoints._build_parser()

        help_text = parser.format_help()

        self.assertIn("replay-time split targets", help_text)
        self.assertIn("base_pages", help_text)
        self.assertIn("data/breakpoints.parquet", help_text)

    def test_generate_combinations_produces_baselines_hot_keys_and_random(self):
        access_counts = {
            "user_cold": 1,
            "user_cool": 2,
            "user_hot": 9,
            "user_mild": 3,
            "user_warm": 4,
        }

        random.seed(0)
        combos = generate_breakpoints.generate_combinations(
            access_counts,
            hot_keys=3,
            random_rows=2,
        )

        # Row 0: base_pages sentinel row
        self.assertEqual(
            combos[0],
            {"user_cold": 0, "user_cool": 0, "user_hot": 0, "user_mild": 0, "user_warm": 0},
        )
        # Row 1: no-break baseline
        self.assertEqual(
            combos[1],
            {"user_cold": 2, "user_cool": 3, "user_hot": 10, "user_mild": 4, "user_warm": 5},
        )
        # Rows 2-4: split-only hot-key rows (hottest first)
        self.assertEqual(
            combos[2],
            {"user_cold": 2, "user_cool": 3, "user_hot": 0, "user_mild": 4, "user_warm": 5},
        )
        self.assertEqual(
            combos[3],
            {"user_cold": 2, "user_cool": 3, "user_hot": 10, "user_mild": 4, "user_warm": 0},
        )
        self.assertEqual(
            combos[4],
            {"user_cold": 2, "user_cool": 3, "user_hot": 10, "user_mild": 0, "user_warm": 5},
        )
        # Rows 5-6: random single-key rows from the remaining keys
        self.assertEqual(len(combos), 7)
        for combo in combos[5:]:
            zero_keys = [key for key, value in combo.items() if value == 0]
            self.assertEqual(len(zero_keys), 1)
            self.assertIn(zero_keys[0], {"user_cool", "user_cold"})

    def test_generate_combinations_defaults_to_three_random_rows(self):
        access_counts = {
            "user_a": 5,
            "user_b": 3,
            "user_c": 2,
            "user_d": 7,
            "user_e": 1,
        }

        random.seed(0)
        combos = generate_breakpoints.generate_combinations(
            access_counts,
            hot_keys=2,
        )

        # 2 baselines + 2 hot-key rows + 3 random rows = 7
        self.assertEqual(len(combos), 7)


if __name__ == "__main__":
    unittest.main()
