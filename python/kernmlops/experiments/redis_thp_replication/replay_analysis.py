"""Shared replay-result analysis helpers for parts B and C."""

from __future__ import annotations

import importlib.util
import statistics
from pathlib import Path
from typing import Any

import polars as pl


REPO_ROOT = Path(__file__).resolve().parents[4]
GENERATE_BREAKPOINTS_PATH = (
    REPO_ROOT / "python" / "kernmlops" / "replay" / "generate_breakpoints.py"
)


def load_access_counts(trace_path: Path) -> dict[str, int]:
    """Return per-key access counts reconstructed from one monitor log.

    Args:
        trace_path: Replay monitor log used to build the breakpoint matrix.

    Returns:
        Mapping from key to access count. Example output:
            {"user3798598596772897818": 549}

    Raises:
        RuntimeError: If ``generate_breakpoints.py`` cannot be imported.
    """

    spec = importlib.util.spec_from_file_location(
        "generate_breakpoints",
        GENERATE_BREAKPOINTS_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not import generate_breakpoints.py for replay analysis.")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_log(trace_path)


def sample_mean(values: list[float]) -> float:
    """Return the arithmetic mean for one non-empty list of samples."""

    if not values:
        raise RuntimeError("Expected at least one sample when computing a mean.")
    return sum(values) / len(values)


def sample_std(values: list[float]) -> float:
    """Return the sample standard deviation, or zero for one sample."""

    return statistics.stdev(values) if len(values) > 1 else 0.0


def slice_runs(samples: list[Any], run_numbers: list[int]) -> list[Any]:
    """Return the selected run samples using one-based run numbers."""

    return [samples[run_number - 1] for run_number in run_numbers]


def summarize_numeric_runs(samples: list[int | float], run_numbers: list[int]) -> dict[str, float]:
    """Return mean and std for one numeric sample list over selected runs."""

    chosen = [float(value) for value in slice_runs(samples, run_numbers)]
    return {"mean": sample_mean(chosen), "std": sample_std(chosen)}


def _sorted_run_columns(df: pl.DataFrame, prefix: str) -> list[str]:
    """Return replay result columns sorted by their numeric run suffix."""

    return sorted(
        (column for column in df.columns if column.startswith(prefix)),
        key=lambda column: int(column.removeprefix(prefix)),
    )


def classify_breakpoint_rows(
    df: pl.DataFrame,
    *,
    trace_path: Path,
    hot_key_rows: int,
) -> list[dict[str, Any]]:
    """Attach stable labels and per-run samples to replay result rows.

    Args:
        df: Replay results dataframe.
        trace_path: Monitor log used to classify hot and random rows.
        hot_key_rows: Number of hottest split-only rows in the matrix.

    Returns:
        Ordered row records. Example shape:
            {
                "row_index": 2,
                "row_type": "hot_split_only",
                "label": "hot#1",
                "runtime_samples": [118.2, 119.5, ...],
            }
    """

    access_counts = load_access_counts(trace_path)
    ordered_keys = [
        key
        for key, _count in sorted(
            access_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]
    hot_key_set = set(ordered_keys[:hot_key_rows])

    runtime_cols = _sorted_run_columns(df, "runtime_s_")
    split_success_cols = _sorted_run_columns(df, "split_successes_")
    split_failure_cols = _sorted_run_columns(df, "split_failures_")
    split_event_cols = _sorted_run_columns(df, "split_events_")
    split_total_wall_cols = _sorted_run_columns(df, "split_total_wall_ms_")
    split_max_wall_cols = _sorted_run_columns(df, "split_max_wall_ms_")
    split_queue_lag_mean_cols = _sorted_run_columns(df, "split_queue_lag_ms_mean_")
    split_queue_lag_max_cols = _sorted_run_columns(df, "split_queue_lag_ms_max_")
    breakpoint_cols = [column for column in df.columns if column.startswith("user")]

    break_dispatch_cols = _sorted_run_columns(df, "break_dispatch_")
    pin_mode_cols = _sorted_run_columns(df, "pin_mode_")
    affinity_cols = _sorted_run_columns(df, "redis_affinity_")

    raw_records: list[dict[str, Any]] = []
    for row in df.iter_rows(named=True):
        breakpoints = {key: int(row[key]) for key in breakpoint_cols}
        zero_keys = [key for key, value in breakpoints.items() if value == 0]

        if len(zero_keys) == len(breakpoints) and breakpoints:
            row_type = "base_pages"
            key = None
        elif len(zero_keys) == 0:
            row_type = "no_break"
            key = None
        elif len(zero_keys) == 1:
            key = zero_keys[0]
            row_type = "hot_split_only" if key in hot_key_set else "random_split_only"
        else:
            key = None
            row_type = "other"

        raw_records.append(
            {
                "row_index": int(row.get("row_index", len(raw_records))),
                "thp_mode": str(row.get("thp_mode", "unknown")),
                "row_type": row_type,
                "key": key,
                "access_count": access_counts.get(key, 0) if key is not None else None,
                "runtime_samples": [float(row[column]) for column in runtime_cols],
                "split_successes_samples": [int(row[column]) for column in split_success_cols],
                "split_failures_samples": [int(row[column]) for column in split_failure_cols],
                "split_events_samples": [int(row[column]) for column in split_event_cols],
                "split_total_wall_ms_samples": [
                    float(row[column]) for column in split_total_wall_cols
                ],
                "split_max_wall_ms_samples": [
                    float(row[column]) for column in split_max_wall_cols
                ],
                "split_queue_lag_ms_mean_samples": [
                    float(row[column]) for column in split_queue_lag_mean_cols
                ],
                "split_queue_lag_ms_max_samples": [
                    float(row[column]) for column in split_queue_lag_max_cols
                ],
                "break_dispatch_samples": [
                    str(row[column]) for column in break_dispatch_cols
                ] if break_dispatch_cols else [],
                "pin_mode_samples": [
                    str(row[column]) for column in pin_mode_cols
                ] if pin_mode_cols else [],
                "redis_affinity_samples": [
                    str(row[column]) for column in affinity_cols
                ] if affinity_cols else [],
            }
        )

    hot_rows = sorted(
        (record for record in raw_records if record["row_type"] == "hot_split_only"),
        key=lambda record: (-int(record["access_count"]), str(record["key"])),
    )
    random_rows = sorted(
        (record for record in raw_records if record["row_type"] == "random_split_only"),
        key=lambda record: (-int(record["access_count"]), str(record["key"])),
    )

    for rank, record in enumerate(hot_rows, start=1):
        record["label"] = f"hot#{rank}"
    for rank, record in enumerate(random_rows, start=1):
        record["label"] = f"rand#{rank}"

    ordered_records: list[dict[str, Any]] = []
    for row_type in ("base_pages", "no_break"):
        record = next(item for item in raw_records if item["row_type"] == row_type)
        record["label"] = row_type
        ordered_records.append(record)
    ordered_records.extend(hot_rows)
    ordered_records.extend(random_rows)
    for record in ordered_records:
        if "label" not in record:
            record["label"] = f"row{record['row_index']}"
    return ordered_records
