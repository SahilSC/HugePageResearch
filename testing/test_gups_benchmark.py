# ruff: noqa: E402
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from kernmlops_benchmark.benchmark import GenericBenchmarkConfig
from kernmlops_benchmark.gups import GUPSBenchmark, GUPSBenchmarkConfig


class GUPSBenchmarkTest(unittest.TestCase):
    def test_run_passes_seeded_split_artifacts_to_binary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            benchmark_dir = Path(tmpdir) / "benchmarks"
            run_dir = Path(tmpdir) / "run"
            run_dir.mkdir()
            (benchmark_dir / "gups").mkdir(parents=True)
            (benchmark_dir / "gups" / "gups").write_text("", encoding="utf-8")

            benchmark = GUPSBenchmark(
                generic_config=GenericBenchmarkConfig(benchmark_dir=str(benchmark_dir)),
                config=GUPSBenchmarkConfig(
                    table_size_gib=16,
                    repeats=4,
                    updates_multiplier=4,
                    threads=1,
                    stream_seed=77,
                    page_summary_out="page_summary.csv",
                    split_schedule="schedule.csv",
                    split_events_out="split_events.csv",
                ),
            )

            with (
                mock.patch("kernmlops_benchmark.gups.demote", return_value=mock.sentinel.demote),
                mock.patch("kernmlops_benchmark.gups.subprocess.Popen") as popen_mock,
            ):
                benchmark.run(run_dir=run_dir)

            command = popen_mock.call_args.args[0]
            self.assertEqual(command[0], str(benchmark_dir / "gups" / "gups"))
            self.assertIn("--stream-seed", command)
            self.assertIn("77", command)
            self.assertIn(str(run_dir / "page_summary.csv"), command)
            self.assertIn(str(run_dir / "schedule.csv"), command)
            self.assertIn(str(run_dir / "split_events.csv"), command)
            self.assertEqual(
                popen_mock.call_args.kwargs["env"]["OMP_NUM_THREADS"],
                "1",
            )


if __name__ == "__main__":
    unittest.main()
