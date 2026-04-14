import sys
import tempfile
import unittest
import importlib.util
from pathlib import Path
from unittest import mock

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from experiments.redis_thp_replication.common import RawBenchmarkConfig
from experiments.redis_thp_replication.part_a import parse_ycsb_overall_runtime_ms
from experiments.redis_thp_replication import part_b


def _load_runtime_compare_module():
    module_path = ROOT / "temp_data_analysis" / "redis_thp_runtime_compare.py"
    spec = importlib.util.spec_from_file_location("redis_thp_runtime_compare", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _monitor_line(key: str) -> str:
    return f'1712754000.123456 [0 127.0.0.1:6379] "GET" "{key}"\n'


class PartAParseTests(unittest.TestCase):
    def test_parse_ycsb_overall_runtime_ms_finds_all_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "run.log"
            log_path.write_text(
                "\n".join(
                    [
                        "some preface",
                        "[OVERALL], RunTime(ms), 8123",
                        "other line",
                        "[OVERALL], RunTime(ms), 9123",
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(parse_ycsb_overall_runtime_ms(log_path), [8123, 9123])

    def test_parse_phase_runtimes_groups_outer10_phases_and_validates_counts(self) -> None:
        runtime_compare = _load_runtime_compare_module()
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "redis_benchmark.log"
            lines: list[str] = []
            for outer_index in range(10):
                lines.append(f"[12:00:00] Load phase out_i={outer_index} starting")
                lines.append(f"[OVERALL], RunTime(ms), {1000 + outer_index}")
                for repeat_index in range(10):
                    lines.append(
                        f"[12:00:00] Run phase out_i={outer_index} i={repeat_index} starting"
                    )
                    lines.append(f"[OVERALL], RunTime(ms), {2000 + outer_index * 10 + repeat_index}")
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            parsed = runtime_compare.parse_phase_runtimes(
                log_path,
                expected_load_count=10,
                expected_run_count=100,
            )

            self.assertEqual(parsed["load_count"], 10)
            self.assertEqual(parsed["run_count"], 100)
            self.assertAlmostEqual(
                parsed["load_sum_s"],
                sum(1000 + outer_index for outer_index in range(10)) / 1000.0,
            )
            self.assertAlmostEqual(
                parsed["run_sum_s"],
                sum(2000 + outer_index * 10 + repeat_index for outer_index in range(10) for repeat_index in range(10))
                / 1000.0,
            )

            broken_log_path = Path(tmp_dir) / "redis_benchmark_missing_run.log"
            broken_log_path.write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                runtime_compare.parse_phase_runtimes(
                    broken_log_path,
                    expected_load_count=10,
                    expected_run_count=100,
                )


class PartBPreviewTests(unittest.TestCase):
    def test_assert_mixed_dispatch_results_uses_first_half_inline_second_half_threaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            results_path = Path(tmp_dir) / "results.parquet"
            pl.DataFrame(
                [
                    {
                        "row_index": 0,
                        "thp_mode": "never",
                        "runtime_s_1": 1.0,
                        "runtime_s_2": 1.1,
                        "commands_replayed_1": 1,
                        "commands_replayed_2": 1,
                        "split_events_1": 0,
                        "split_events_2": 0,
                        "split_successes_1": 0,
                        "split_successes_2": 0,
                        "split_failures_1": 0,
                        "split_failures_2": 0,
                        "split_syscall_attempts_1": 0,
                        "split_syscall_attempts_2": 0,
                        "split_max_attempts_1": 0,
                        "split_max_attempts_2": 0,
                        "split_total_wall_ms_1": 0.0,
                        "split_total_wall_ms_2": 0.0,
                        "split_max_wall_ms_1": 0.0,
                        "split_max_wall_ms_2": 0.0,
                        "split_queue_lag_ms_mean_1": 0.0,
                        "split_queue_lag_ms_mean_2": 0.0,
                        "split_queue_lag_ms_max_1": 0.0,
                        "split_queue_lag_ms_max_2": 0.0,
                        "break_dispatch_1": "inline",
                        "break_dispatch_2": "threaded",
                    }
                ]
            ).write_parquet(results_path)

            df = part_b._assert_mixed_dispatch_results(
                results_path,
                runs=2,
                expected_rows=1,
            )

            self.assertEqual(df.shape[0], 1)

    def test_render_artifacts_writes_html_with_runtime_and_split_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            trace_path = tmp_path / "monitor.log"
            trace_path.write_text(
                _monitor_line("user_hot")
                + _monitor_line("user_hot")
                + _monitor_line("user_hot")
                + _monitor_line("user_rand"),
                encoding="utf-8",
            )

            def row(
                row_index: int,
                thp_mode: str,
                user_hot: int,
                user_rand: int,
                runtimes: list[float],
            ) -> dict[str, object]:
                payload: dict[str, object] = {
                    "row_index": row_index,
                    "thp_mode": thp_mode,
                    "user_hot": user_hot,
                    "user_rand": user_rand,
                }
                for run_number, runtime in enumerate(runtimes, start=1):
                    payload[f"runtime_s_{run_number}"] = runtime
                    payload[f"split_events_{run_number}"] = 0 if row_index < 2 else 1
                    payload[f"split_successes_{run_number}"] = 0 if row_index < 2 else 1
                    payload[f"split_failures_{run_number}"] = 0
                    payload[f"split_total_wall_ms_{run_number}"] = (
                        0.0 if row_index < 2 else 10.0 + run_number
                    )
                    payload[f"split_max_wall_ms_{run_number}"] = (
                        0.0 if row_index < 2 else 4.0 + run_number
                    )
                    payload[f"split_queue_lag_ms_mean_{run_number}"] = (
                        0.0 if row_index < 2 else 1.0 + run_number
                    )
                    payload[f"split_queue_lag_ms_max_{run_number}"] = (
                        0.0 if row_index < 2 else 2.0 + run_number
                    )
                return payload

            results_df = pl.DataFrame(
                [
                    row(0, "never", 0, 0, [130, 131, 132, 133, 128, 129, 130, 131]),
                    row(1, "always", 4, 2, [120, 121, 122, 123, 119, 120, 121, 122]),
                    row(2, "always", 0, 2, [123, 124, 125, 126, 118, 119, 120, 121]),
                    row(3, "always", 4, 0, [124, 125, 126, 127, 119, 120, 121, 122]),
                ]
            )
            config = RawBenchmarkConfig(
                name="read80_delete20",
                thp_mode="always",
                record_count=4096,
                operation_count=8192,
                outer_repeat=1,
                read_proportion=0.80,
                delete_proportion=0.20,
            )

            html_path = tmp_path / "part_b.html"
            png_path = tmp_path / "part_b.png"
            split_png_path = tmp_path / "part_b_split.png"
            report_path = tmp_path / "part_b.md"
            with mock.patch.object(part_b, "PART_B_HTML_PATH", html_path), mock.patch.object(
                part_b, "PART_B_PNG_PATH", png_path
            ), mock.patch.object(part_b, "PART_B_SPLIT_PNG_PATH", split_png_path), mock.patch.object(
                part_b, "PART_B_REPORT_PATH", report_path
            ):
                summary = part_b._render_artifacts(
                    results_df=results_df,
                    trace_path=trace_path,
                    config=config,
                    config_source="unit-test",
                    verification_artifacts={"runtime_log": "verify.log", "results": "verify.parquet"},
                    full_artifacts={"runtime_log": "full.log", "results": "full.parquet"},
                    started_at_utc="2026-04-10T00:00:00Z",
                    finished_at_utc="2026-04-10T00:10:00Z",
                )

            html = html_path.read_text(encoding="utf-8")
            self.assertTrue(png_path.exists())
            self.assertTrue(split_png_path.exists())
            self.assertIn("Part B: Inline vs Threaded Replay Breaking", html)
            self.assertIn("Runtime Comparison", html)
            self.assertIn("Split Wall Time", html)
            self.assertIn("hot#1", html)
            self.assertIn("hot#2", html)
            self.assertEqual(summary["records"], 4)


if __name__ == "__main__":
    unittest.main()
