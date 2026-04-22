"""Run a deterministic GUPS breakpoint matrix under controlled THP modes."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import statistics
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    _PACKAGE_ROOT = Path(__file__).resolve().parents[1]
    _PACKAGE_ROOT_STR = str(_PACKAGE_ROOT)
    if _PACKAGE_ROOT_STR not in sys.path:
        sys.path.insert(0, _PACKAGE_ROOT_STR)

import polars as pl
from kernmlops_benchmark.benchmark import GenericBenchmarkConfig
from replay.hardware_collectors import HardwareCollector
from replay.replay_trace import (
    _load_counter_config,
    _require_root_for_counter_collection,
)
from replay.system_tuning import _set_thp_enabled_mode, setup_system, teardown_system

logger = logging.getLogger(__name__)


def _resolve_gups_binary(benchmark_dir: Path | None) -> Path:
    """Resolve the installed repo-managed GUPS benchmark binary."""
    root = (
        benchmark_dir
        if benchmark_dir is not None
        else GenericBenchmarkConfig().get_benchmark_dir()
    )
    binary_path = root / "gups" / "gups"
    if not binary_path.is_file():
        raise RuntimeError(
            f"GUPS benchmark binary not found at {binary_path}. "
            "Run scripts/setup-benchmarks/setup-gups.sh first."
        )
    return binary_path


def _build_size_args(
    *,
    table_size_gib: int | None,
    table_size_mib: int | None,
) -> list[str]:
    """Return the exact size flag pair for the GUPS binary."""
    if (table_size_gib is None) == (table_size_mib is None):
        raise RuntimeError(
            "Specify exactly one of --table-size-gib or --table-size-mib."
        )
    if table_size_gib is not None:
        return ["--table-size-gib", str(table_size_gib)]
    return ["--table-size-mib", str(table_size_mib)]


def _page_columns(row: dict[str, object]) -> list[str]:
    """Return all breakpoint matrix columns that encode selected pages."""
    return sorted(column for column in row if column.startswith("hp_"))


def _write_split_schedule(
    row: dict[str, object],
    *,
    repeats: int,
    schedule_path: Path,
) -> Path | None:
    """Write one per-run split schedule CSV for a split-only breakpoint row."""
    target_page_index = int(row.get("target_page_index", -1))
    if target_page_index < 0:
        return None

    row_kind = str(row.get("row_kind", ""))
    if row_kind != "split_only":
        return None

    target_break_after = int(row.get("target_break_after_page_accesses", 0))
    label = str(row.get("row_label", f"page {target_page_index}"))
    schedule_path.parent.mkdir(parents=True, exist_ok=True)
    schedule_path.write_text(
        "".join(
            [
                "repeat_index,page_index,break_after_page_accesses,label\n",
                *[
                    f"{repeat_index},{target_page_index},{target_break_after},{label}\n"
                    for repeat_index in range(repeats)
                ],
            ]
        ),
        encoding="utf-8",
    )
    return schedule_path


def _load_gups_results(results_path: Path, expected_repeats: int) -> list[dict[str, object]]:
    """Load one GUPS JSONL result file and validate the repeat count."""
    if not results_path.is_file():
        raise RuntimeError(f"GUPS results file not found: {results_path}")

    rows = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != expected_repeats:
        raise RuntimeError(
            f"Expected {expected_repeats} GUPS repeats in {results_path}, got {len(rows)}."
        )
    failed_repeats = [row["repeat_index"] for row in rows if not row["verification_passed"]]
    if failed_repeats:
        failed_text = ", ".join(str(index) for index in failed_repeats)
        raise RuntimeError(
            f"GUPS verification failed for repeats [{failed_text}] in {results_path}."
        )
    return rows


def _summarize_gups_results(rows: list[dict[str, object]]) -> dict[str, int | float]:
    """Aggregate one GUPS invocation's per-repeat JSONL rows."""
    runtime_values = [float(row["runtime_s"]) for row in rows]
    gups_values = [float(row["gups"]) for row in rows]
    return {
        "repeat_count": len(rows),
        "runtime_s_mean": statistics.mean(runtime_values),
        "runtime_s_total": sum(runtime_values),
        "gups_mean": statistics.mean(gups_values),
        "gups_min": min(gups_values),
        "gups_max": max(gups_values),
        "split_events": sum(int(row["split_events"]) for row in rows),
        "split_successes": sum(int(row["split_successes"]) for row in rows),
        "split_failures": sum(int(row["split_failures"]) for row in rows),
        "split_syscall_attempts": sum(int(row["split_syscall_attempts"]) for row in rows),
        "split_max_attempts": max(int(row["split_max_attempts"]) for row in rows),
        "split_total_wall_ms": sum(float(row["split_total_wall_ms"]) for row in rows),
        "split_max_wall_ms": max(float(row["split_max_wall_ms"]) for row in rows),
    }


def _run_gups_once(
    *,
    binary_path: Path,
    size_args: list[str],
    run_dir: Path,
    repeats: int,
    updates_multiplier: int,
    stream_seed: int,
    split_schedule_path: Path | None,
    collectors: tuple[str, ...],
    hw_collector: HardwareCollector,
) -> tuple[dict[str, int | float], dict[str, int], str, Path, Path | None]:
    """Run one seeded GUPS invocation and return aggregated metrics."""
    results_path = run_dir / "gups_results.jsonl"
    stdout_path = run_dir / "gups_stdout.log"
    split_events_path = (
        run_dir / "split_events.csv" if split_schedule_path is not None else None
    )
    command = [
        str(binary_path),
        "--results",
        str(results_path),
        *size_args,
        "--repeats",
        str(repeats),
        "--updates-multiplier",
        str(updates_multiplier),
        "--threads",
        "1",
        "--stream-seed",
        str(stream_seed),
    ]
    if split_schedule_path is not None:
        command.extend(["--split-schedule", str(split_schedule_path)])
    if split_events_path is not None:
        command.extend(["--split-events-out", str(split_events_path)])

    run_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "1"
    counters_started = False
    with stdout_path.open("w", encoding="utf-8") as stdout_file:
        process = subprocess.Popen(
            command,
            stdout=stdout_file,
            stderr=stdout_file,
            env=env,
        )
        try:
            if collectors:
                hw_collector.start_counters(list(collectors), process.pid)
                counters_started = True
            return_code = process.wait()
        finally:
            counter_totals = (
                hw_collector.stop_counters(list(collectors))
                if collectors and counters_started
                else {}
            )

    if return_code != 0:
        raise RuntimeError(
            f"GUPS exited with status {return_code}; see {stdout_path} for details."
        )

    rows = _load_gups_results(results_path, expected_repeats=repeats)
    return (
        _summarize_gups_results(rows),
        counter_totals,
        shlex.join(command),
        results_path,
        split_events_path,
    )


def run_benchmark(
    *,
    breakpoints_df: pl.DataFrame,
    output: Path,
    artifacts_dir: Path,
    benchmark_dir: Path | None,
    table_size_gib: int | None,
    table_size_mib: int | None,
    repeats: int,
    updates_multiplier: int,
    stream_seed: int,
    runs: int,
    collectors: tuple[str, ...],
) -> Path:
    """Run the deterministic GUPS breakpoint matrix and write one result parquet."""
    binary_path = _resolve_gups_binary(benchmark_dir)
    size_args = _build_size_args(
        table_size_gib=table_size_gib,
        table_size_mib=table_size_mib,
    )
    hw_collector = HardwareCollector()
    commands_log_path = artifacts_dir / "run_commands.log"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []

    system_config = setup_system()
    try:
        for row_index, row in enumerate(breakpoints_df.iter_rows(named=True)):
            row_result: dict[str, object] = dict(row)
            row_kind = str(row.get("row_kind", ""))
            thp_mode = "never" if row_index == 0 or row_kind == "base_pages" else "always"
            logger.info(
                "[%d/%d] Running %s (THP %s)",
                row_index + 1,
                breakpoints_df.height,
                row.get("row_label", f"row {row_index}"),
                thp_mode,
            )
            row_result["row_index"] = row_index
            row_result["thp_mode"] = thp_mode

            for run_number in range(1, runs + 1):
                run_dir = artifacts_dir / f"row_{row_index:03d}" / f"run_{run_number:02d}"
                schedule_path = _write_split_schedule(
                    row,
                    repeats=repeats,
                    schedule_path=run_dir / "split_schedule.csv",
                )
                _set_thp_enabled_mode(thp_mode)
                summary, counter_totals, command_text, results_path, split_events_path = _run_gups_once(
                    binary_path=binary_path,
                    size_args=size_args,
                    run_dir=run_dir,
                    repeats=repeats,
                    updates_multiplier=updates_multiplier,
                    stream_seed=stream_seed,
                    split_schedule_path=schedule_path if thp_mode == "always" else None,
                    collectors=collectors,
                    hw_collector=hw_collector,
                )

                with commands_log_path.open("a", encoding="utf-8") as command_log:
                    command_log.write(
                        f"row={row_index} run={run_number} label={row.get('row_label', '')}\n"
                    )
                    command_log.write(f"{command_text}\n\n")

                row_result[f"runtime_s_{run_number}"] = summary["runtime_s_mean"]
                row_result[f"runtime_total_s_{run_number}"] = summary["runtime_s_total"]
                row_result[f"gups_{run_number}"] = summary["gups_mean"]
                row_result[f"gups_min_{run_number}"] = summary["gups_min"]
                row_result[f"gups_max_{run_number}"] = summary["gups_max"]
                row_result[f"repeat_count_{run_number}"] = summary["repeat_count"]
                row_result[f"split_events_{run_number}"] = summary["split_events"]
                row_result[f"split_successes_{run_number}"] = summary["split_successes"]
                row_result[f"split_failures_{run_number}"] = summary["split_failures"]
                row_result[f"split_syscall_attempts_{run_number}"] = summary[
                    "split_syscall_attempts"
                ]
                row_result[f"split_max_attempts_{run_number}"] = summary[
                    "split_max_attempts"
                ]
                row_result[f"split_total_wall_ms_{run_number}"] = summary[
                    "split_total_wall_ms"
                ]
                row_result[f"split_max_wall_ms_{run_number}"] = summary[
                    "split_max_wall_ms"
                ]
                row_result[f"run_dir_{run_number}"] = str(run_dir)
                row_result[f"stdout_path_{run_number}"] = str(run_dir / "gups_stdout.log")
                row_result[f"gups_results_path_{run_number}"] = str(results_path)
                row_result[f"split_schedule_path_{run_number}"] = (
                    str(schedule_path) if schedule_path is not None and thp_mode == "always" else ""
                )
                row_result[f"split_events_path_{run_number}"] = (
                    str(split_events_path) if split_events_path is not None else ""
                )
                row_result[f"command_{run_number}"] = command_text
                for counter_name, total in counter_totals.items():
                    row_result[f"{counter_name}_{run_number}"] = total

            results.append(row_result)
            output.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame(results).write_parquet(output)
    finally:
        teardown_system(system_config)
        hw_collector.close()

    metadata = {
        "output": str(output),
        "artifacts_dir": str(artifacts_dir),
        "breakpoints_path": "",
        "benchmark_dir": str(benchmark_dir) if benchmark_dir is not None else "",
        "binary_path": str(binary_path),
        "table_size_gib": table_size_gib,
        "table_size_mib": table_size_mib,
        "repeats": repeats,
        "updates_multiplier": updates_multiplier,
        "stream_seed": stream_seed,
        "runs": runs,
        "collectors": list(collectors),
        "commands_log_path": str(commands_log_path),
    }
    metadata_path = output.with_suffix(".metadata.json")
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a deterministic GUPS breakpoint matrix under base_pages, no_break, "
            "and split-only rows, then write one results parquet."
        )
    )
    parser.add_argument(
        "--breakpoints",
        type=Path,
        required=True,
        help="Breakpoint Parquet file from generate_gups_breakpoints.py.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output Parquet file for matrix results.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        required=True,
        help="Directory for per-row/per-run stdout, schedules, and JSONL files.",
    )
    parser.add_argument(
        "--benchmark-dir",
        type=Path,
        default=None,
        help="Benchmark root containing gups/gups. Defaults to GenericBenchmarkConfig().get_benchmark_dir().",
    )
    parser.add_argument("--table-size-gib", type=int, default=None)
    parser.add_argument("--table-size-mib", type=int, default=None)
    parser.add_argument("--repeats", type=int, default=4)
    parser.add_argument("--updates-multiplier", type=int, default=4)
    parser.add_argument("--stream-seed", type=int, default=1)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--collector-config",
        type=Path,
        default=None,
        help="Optional replay counter config YAML.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        format="%(levelname)s: %(message)s",
        level=logging.INFO if args.verbose else logging.ERROR,
    )

    if not args.breakpoints.is_file():
        parser.error(f"Breakpoints file not found: {args.breakpoints}")
    if args.repeats <= 0:
        parser.error("--repeats must be positive.")
    if args.runs <= 0:
        parser.error("--runs must be positive.")

    try:
        collector_names = _load_counter_config(args.collector_config)
    except RuntimeError as exc:
        parser.error(str(exc))

    if collector_names:
        try:
            _require_root_for_counter_collection(collector_names)
        except PermissionError as exc:
            parser.error(str(exc))

    breakpoints_df = pl.read_parquet(args.breakpoints)
    metadata_path = run_benchmark(
        breakpoints_df=breakpoints_df,
        output=args.output,
        artifacts_dir=args.artifacts_dir,
        benchmark_dir=args.benchmark_dir,
        table_size_gib=args.table_size_gib,
        table_size_mib=args.table_size_mib,
        repeats=args.repeats,
        updates_multiplier=args.updates_multiplier,
        stream_seed=args.stream_seed,
        runs=args.runs,
        collectors=collector_names,
    )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["breakpoints_path"] = str(args.breakpoints)
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
