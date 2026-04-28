from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import polars as pl


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "temp_data_analysis" / "replay_live_log_dashboard.py"
SPEC = importlib.util.spec_from_file_location("replay_live_log_dashboard", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load replay_live_log_dashboard.py for testing")
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ReplayLiveLogDashboardTest(unittest.TestCase):
    def test_build_live_dashboard_infers_partial_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            trace_path = tmp_path / "monitor_run.log"
            breakpoints_path = tmp_path / "breakpoints.parquet"
            session_log_path = tmp_path / "full_session.log"
            output_html = tmp_path / "dashboard.html"

            trace_path.write_text(
                "\n".join(
                    [
                        '1700000000.100000 [0 127.0.0.1:40000] "HGET" "userA" "field0"',
                        '1700000000.200000 [0 127.0.0.1:40000] "HGET" "userA" "field0"',
                        '1700000000.300000 [0 127.0.0.1:40000] "HGET" "userA" "field0"',
                        '1700000000.400000 [0 127.0.0.1:40000] "HGET" "userB" "field0"',
                        '1700000000.500000 [0 127.0.0.1:40000] "HGET" "userB" "field0"',
                        '1700000000.600000 [0 127.0.0.1:40000] "HGET" "userC" "field0"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            pl.DataFrame(
                [
                    {"userA": 0, "userB": 0, "userC": 0},
                    {"userA": 4, "userB": 3, "userC": 2},
                    {"userA": 0, "userB": 3, "userC": 2},
                    {"userA": 4, "userB": 0, "userC": 2},
                    {"userA": 4, "userB": 3, "userC": 0},
                ]
            ).write_parquet(breakpoints_path)

            session_log_path.write_text(
                "\n".join(
                    [
                        "INFO: Setting up system configuration ...",
                        "INFO: System configuration applied.",
                        "INFO: [1/5 run 1/3] Restoring snapshot (THP never) ...",
                        "INFO: [1/5 run 1/3] Timing run trace ...",
                        "INFO: [1/5 run 1/3] Done — 100 commands in 10.000s",
                        "INFO: [2/5 run 1/3] Restoring snapshot (THP always) ...",
                        "INFO: [2/5 run 1/3] Timing run trace ...",
                        "INFO: [2/5 run 1/3] Done — 100 commands in 9.000s",
                        "INFO: [3/5 run 1/3] Restoring snapshot (THP always) ...",
                        "INFO: [3/5 run 1/3] Timing run trace ...",
                        "INFO: [3/5 run 1/3] Done — 100 commands in 8.000s",
                        "INFO: [4/5 run 1/3] Restoring snapshot (THP always) ...",
                        "INFO: [4/5 run 1/3] Timing run trace ...",
                        "INFO: break_page: retrying split_thp(pid=999, vaddr=0x7ffc53db8009) for key 'userB' after attempt 1/2 failed: errno=2 (No such file or directory)",
                        "WARNING: line 19: break_page failed for key 'userB' at access 0 after 2 attempts: pid=999 vaddr=0x7ffc53db8009 errno=2 (No such file or directory)",
                        "INFO: [4/5 run 1/3] Done — 100 commands in 11.000s",
                        "INFO: [5/5 run 1/3] Restoring snapshot (THP always) ...",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            parsed_log = MODULE._parse_session_log(session_log_path)
            rows = MODULE._classify_rows(
                breakpoints_path,
                trace_path,
                hot_keys=2,
                random_rows=1,
                runs_per_row=3,
                parsed_log=parsed_log,
            )
            metrics = MODULE._build_metrics(
                experiment_name="exp2 live",
                workload_label="80% read / 20% delete",
                session_log_path=session_log_path,
                breakpoints_path=breakpoints_path,
                trace_path=trace_path,
                report_path=None,
                rows=rows,
                parsed_log=parsed_log,
                hot_keys=2,
                random_rows=1,
                runs_per_row=3,
            )

            row_by_label = {row["short_label"]: row for row in rows}
            self.assertEqual(metrics["completed_runs"], 4)
            self.assertEqual(metrics["current_row_label"], "row 5/5 run 1/3")
            self.assertAlmostEqual(metrics["base_pages_pct_vs_no_break"], (10.0 / 9.0 - 1.0) * 100.0)
            self.assertAlmostEqual(
                row_by_label["hot#2 (0/3)"]["runtime_std_pct_vs_no_break"],
                (0.0 / 9.0) * 100.0,
            )
            self.assertEqual(row_by_label["hot#2 (0/3)"]["inferred_split_failures"], 1)
            self.assertEqual(row_by_label["hot#2 (0/3)"]["observed_max_attempts"], 2)
            self.assertEqual(row_by_label["rand#1 (0/3)"]["status"], "in_progress")

            MODULE.build_live_dashboard(
                session_log_path=session_log_path,
                breakpoints_path=breakpoints_path,
                trace_path=trace_path,
                output_html=output_html,
                experiment_name="exp2 live",
                workload_label="80% read / 20% delete",
                hot_keys=2,
                random_rows=1,
                runs_per_row=3,
                report_path=None,
            )
            html = output_html.read_text(encoding="utf-8")
            self.assertIn("Log-Only Live View", html)
            self.assertIn("hot#2 (0/3)", html)
            self.assertIn(str(session_log_path), html)
            self.assertIn("Percent Change Vs no_break", html)
            self.assertIn("Full Run Times So Far", html)
            self.assertIn("barColorForRow", html)
            self.assertIn("Run Times", html)
            self.assertIn("formatRuntimeSamples", html)


if __name__ == "__main__":
    unittest.main()
