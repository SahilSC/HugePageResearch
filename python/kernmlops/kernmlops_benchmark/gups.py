import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from data_schema import GraphEngine, demote
from kernmlops_benchmark.benchmark import Benchmark, GenericBenchmarkConfig
from kernmlops_benchmark.errors import (
    BenchmarkNotInCollectionData,
    BenchmarkNotRunningError,
    BenchmarkRunningError,
)
from kernmlops_config import ConfigBase


@dataclass(frozen=True)
class GUPSBenchmarkConfig(ConfigBase):
    """Configuration for the repo-managed single-node GUPS benchmark.

    Attributes:
        table_size_gib: Anonymous table size in GiB for the normal harness.
        repeats: Number of timed update passes to run.
        updates_multiplier: HPCC-style updates per table word.
        threads: OpenMP thread count passed to the benchmark. The deterministic
            split harness uses ``1``.
        stream_seed: One-based seeded offset into the HPCC RandomAccess stream.
        page_summary_out: Optional CSV path for deterministic per-2-MiB page
            access summaries.
        split_schedule: Optional CSV path describing in-benchmark page splits.
        split_events_out: Optional CSV path for split syscall outcomes.
        pre_split_pages: Optional CSV path listing table pages to split before
            each timed update loop starts.
        pre_split_events_out: Optional CSV path for pre-timed split outcomes.
    """

    table_size_gib: int = 16
    repeats: int = 4
    updates_multiplier: int = 4
    threads: int = 0
    stream_seed: int = 1
    page_summary_out: str = ""
    split_schedule: str = ""
    split_events_out: str = ""
    pre_split_pages: str = ""
    pre_split_events_out: str = ""


class GUPSBenchmark(Benchmark):
    @classmethod
    def name(cls) -> str:
        return "gups"

    @classmethod
    def default_config(cls) -> ConfigBase:
        return GUPSBenchmarkConfig()

    @classmethod
    def from_config(cls, config: ConfigBase) -> "Benchmark":
        generic_config = cast(GenericBenchmarkConfig, getattr(config, "generic"))
        gups_config = cast(GUPSBenchmarkConfig, getattr(config, cls.name()))
        return GUPSBenchmark(generic_config=generic_config, config=gups_config)

    def __init__(
        self, *, generic_config: GenericBenchmarkConfig, config: GUPSBenchmarkConfig
    ):
        self.generic_config = generic_config
        self.config = config
        self.benchmark_dir = self.generic_config.get_benchmark_dir() / self.name()
        self.process: subprocess.Popen | None = None
        self._log_file = None

    def _binary_path(self) -> Path:
        return self.benchmark_dir / "gups"

    @staticmethod
    def _resolve_optional_run_path(
        raw_path: str,
        *,
        run_dir: Path | None,
    ) -> Path | None:
        """Resolve an optional benchmark output/input path for one run.

        Example output:
            Path("/tmp/run/split_events.csv")

        Args:
            raw_path: Configured path string. Empty strings disable the path.
            run_dir: Optional benchmark run directory supplied by ``collect``.

        Returns:
            ``None`` when *raw_path* is empty, else a concrete ``Path``.
        """
        if not raw_path:
            return None
        candidate = Path(raw_path)
        if candidate.is_absolute() or run_dir is None:
            return candidate
        return run_dir / candidate

    def _close_log_file(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def is_configured(self) -> bool:
        return self.benchmark_dir.is_dir() and self._binary_path().is_file()

    def setup(self) -> None:
        if self.process is not None:
            raise BenchmarkRunningError()
        self.generic_config.generic_setup()

    def run(
        self,
        *,
        run_dir: Path | None = None,
        config_text: str | None = None,
    ) -> None:
        del config_text
        if self.process is not None:
            raise BenchmarkRunningError()

        results_path = (
            (run_dir / "gups_results.jsonl")
            if run_dir is not None
            else Path("gups_results.jsonl")
        )
        stdout_path = (
            (run_dir / "gups_stdout.log")
            if run_dir is not None
            else Path("gups_stdout.log")
        )
        page_summary_out = self._resolve_optional_run_path(
            self.config.page_summary_out,
            run_dir=run_dir,
        )
        split_schedule = self._resolve_optional_run_path(
            self.config.split_schedule,
            run_dir=run_dir,
        )
        split_events_out = self._resolve_optional_run_path(
            self.config.split_events_out,
            run_dir=run_dir,
        )
        pre_split_pages = self._resolve_optional_run_path(
            self.config.pre_split_pages,
            run_dir=run_dir,
        )
        pre_split_events_out = self._resolve_optional_run_path(
            self.config.pre_split_events_out,
            run_dir=run_dir,
        )
        env = os.environ.copy()
        if self.config.threads > 0:
            env["OMP_NUM_THREADS"] = str(self.config.threads)

        command = [
            str(self._binary_path()),
            "--results",
            str(results_path),
            "--table-size-gib",
            str(self.config.table_size_gib),
            "--repeats",
            str(self.config.repeats),
            "--updates-multiplier",
            str(self.config.updates_multiplier),
            "--threads",
            str(self.config.threads),
            "--stream-seed",
            str(self.config.stream_seed),
        ]
        if page_summary_out is not None:
            command.extend(["--page-summary-out", str(page_summary_out)])
        if split_schedule is not None:
            command.extend(["--split-schedule", str(split_schedule)])
        if split_events_out is not None:
            command.extend(["--split-events-out", str(split_events_out)])
        if pre_split_pages is not None:
            command.extend(["--pre-split-pages", str(pre_split_pages)])
        if pre_split_events_out is not None:
            command.extend(["--pre-split-events-out", str(pre_split_events_out)])

        self._log_file = stdout_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            command,
            preexec_fn=demote(),
            stdout=self._log_file,
            stderr=self._log_file,
            env=env,
        )

    def poll(self) -> int | None:
        if self.process is None:
            raise BenchmarkNotRunningError()
        return_code = self.process.poll()
        if return_code is not None:
            self._close_log_file()
        return return_code

    def wait(self) -> None:
        if self.process is None:
            raise BenchmarkNotRunningError()
        self.process.wait()
        self._close_log_file()

    def kill(self) -> None:
        if self.process is None:
            raise BenchmarkNotRunningError()
        self.process.terminate()
        self._close_log_file()

    @classmethod
    def plot_events(cls, graph_engine: GraphEngine) -> None:
        if graph_engine.collection_data.benchmark != cls.name():
            raise BenchmarkNotInCollectionData()
        # Dedicated GUPS analysis lives in temp_data_analysis/render_gups_always_vs_never.py
