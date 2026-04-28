# ruff: noqa: E402
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from temp_data_analysis.extract_data import build_access_distribution, build_hotkey_analysis


class TempDataDashboardDataTest(unittest.TestCase):
    def test_hotkey_analysis_reports_percent_and_seconds(self):
        df = pd.DataFrame(
            [
                {
                    "runtime_s_1": 10.0,
                    "runtime_s_2": 10.0,
                    "runtime_s_3": 10.0,
                    "user_alpha": 100,
                    "user_beta": 20,
                    "user_gamma": 5,
                },
                {
                    "runtime_s_1": 8.0,
                    "runtime_s_2": 8.0,
                    "runtime_s_3": 8.0,
                    "user_alpha": 100,
                    "user_beta": 20,
                    "user_gamma": 5,
                },
                {
                    "runtime_s_1": 7.0,
                    "runtime_s_2": 7.0,
                    "runtime_s_3": 7.0,
                    "user_alpha": 0,
                    "user_beta": 20,
                    "user_gamma": 5,
                },
                {
                    "runtime_s_1": 8.8,
                    "runtime_s_2": 8.8,
                    "runtime_s_3": 8.8,
                    "user_alpha": 100,
                    "user_beta": 0,
                    "user_gamma": 5,
                },
            ]
        )

        result = build_hotkey_analysis(df, ["user_alpha", "user_beta", "user_gamma"])
        entries = {entry["spared_key"]: entry for entry in result["entries"]}

        self.assertAlmostEqual(result["no_split_mean_runtime"], 8.0)
        self.assertAlmostEqual(
            entries["user_alpha"]["runtime_delta_vs_no_split_seconds"],
            1.0,
        )
        self.assertAlmostEqual(
            entries["user_alpha"]["runtime_improvement_pct_vs_no_split"],
            12.5,
        )
        self.assertAlmostEqual(
            entries["user_beta"]["runtime_delta_vs_no_split_seconds"],
            -0.8,
        )
        self.assertAlmostEqual(
            entries["user_beta"]["runtime_improvement_pct_vs_no_split"],
            -10.0,
        )

    def test_access_distribution_uses_readable_cold_to_hot_buckets(self):
        df = pd.DataFrame(
            [
                {"runtime_s_1": 0.0, "runtime_s_2": 0.0, "runtime_s_3": 0.0},
                {
                    "runtime_s_1": 0.0,
                    "runtime_s_2": 0.0,
                    "runtime_s_3": 0.0,
                    "user_2": 2,
                    "user_5": 5,
                    "user_6": 6,
                    "user_10": 10,
                    "user_11": 11,
                    "user_20": 20,
                    "user_21": 21,
                    "user_50": 50,
                    "user_51": 51,
                    "user_100": 100,
                    "user_101": 101,
                    "user_200": 200,
                    "user_201": 201,
                    "user_500": 500,
                    "user_501": 501,
                    "user_1000": 1000,
                    "user_1001": 1001,
                },
            ]
        ).fillna(0.0)

        result = build_access_distribution(
            df,
            [
                "user_2",
                "user_5",
                "user_6",
                "user_10",
                "user_11",
                "user_20",
                "user_21",
                "user_50",
                "user_51",
                "user_100",
                "user_101",
                "user_200",
                "user_201",
                "user_500",
                "user_501",
                "user_1000",
                "user_1001",
            ],
        )

        bucket_counts = {bucket["label"]: bucket["count"] for bucket in result["bins"]}
        self.assertEqual(bucket_counts["2-5"], 2)
        self.assertEqual(bucket_counts["6-10"], 2)
        self.assertEqual(bucket_counts["11-20"], 2)
        self.assertEqual(bucket_counts["21-50"], 2)
        self.assertEqual(bucket_counts["51-100"], 2)
        self.assertEqual(bucket_counts["101-200"], 2)
        self.assertEqual(bucket_counts["201-500"], 2)
        self.assertEqual(bucket_counts["501-1000"], 2)
        self.assertEqual(bucket_counts["1001+"], 1)


if __name__ == "__main__":
    unittest.main()
