#!/usr/bin/env python3
"""Extract JSON data files from parquet experiment results for the HTML dashboard."""

import json
import pathlib
import numpy as np
import pandas as pd

OUTPUT_DIR = pathlib.Path(__file__).parent
DATA_DIR = pathlib.Path(__file__).parent.parent / "data"

TEST1_PATH = DATA_DIR / "test1_hotkey_results_32rows.parquet"
TEST2_PATH = DATA_DIR / "test2_dtlb_results_32rows.parquet"
ALL_BROKEN_ROW_IDX = 0
NO_SPLIT_ROW_IDX = 1
ACCESS_COUNT_BUCKETS: list[tuple[int, int | None, str]] = [
    (2, 5, "2-5"),
    (6, 10, "6-10"),
    (11, 20, "11-20"),
    (21, 50, "21-50"),
    (51, 100, "51-100"),
    (101, 200, "101-200"),
    (201, 500, "201-500"),
    (501, 1000, "501-1000"),
    (1001, None, "1001+"),
]


def user_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith("user")]


def spared_key_for_row(df: pd.DataFrame, row_idx: int, ucols: list[str]) -> str | None:
    """Return the user key with access count == 0 in this row (the spared key)."""
    if row_idx < 2:
        return None
    row = df.loc[row_idx, ucols]
    zeros = row[row == 0]
    if len(zeros) == 1:
        return zeros.index[0]
    return None


def row_label(row_idx: int, spared: str | None) -> str:
    if row_idx == 0:
        return "all-broken"
    if row_idx == 1:
        return "baseline"
    if spared:
        return f"split-{short_key_label(spared)}"
    return f"Row {row_idx}"


def row_kind(row_idx: int) -> str:
    """Return the dashboard-friendly meaning of an experiment row."""
    if row_idx == 0:
        return "all-broken"
    if row_idx == 1:
        return "baseline"
    return "spared-key"


def short_key_label(key: str, keep: int = 8) -> str:
    """Return a compact key label that still distinguishes neighboring keys."""
    if len(key) <= keep + 5:
        return key
    return f"{key[:4]}...{key[-keep:]}"


def build_runtime_comparison(df1: pd.DataFrame, df2: pd.DataFrame, ucols1: list[str], ucols2: list[str]):
    rows = []
    for idx in range(len(df1)):
        spared = spared_key_for_row(df1, idx, ucols1)
        t1_runtimes = [df1.at[idx, f"runtime_s_{t}"] for t in (1, 2, 3)]
        t2_runtimes = [df2.at[idx, f"runtime_s_{t}"] for t in (1, 2, 3)]
        rows.append({
            "row": idx,
            "label": row_label(idx, spared),
            "kind": row_kind(idx),
            "spared_key": spared,
            "test1_runtimes": t1_runtimes,
            "test1_mean": float(np.mean(t1_runtimes)),
            "test1_std": float(np.std(t1_runtimes, ddof=1)),
            "test2_runtimes": t2_runtimes,
            "test2_mean": float(np.mean(t2_runtimes)),
            "test2_std": float(np.std(t2_runtimes, ddof=1)),
        })
    return rows


def build_dtlb_comparison(df2: pd.DataFrame, ucols2: list[str]):
    rows = []
    for idx in range(len(df2)):
        spared = spared_key_for_row(df2, idx, ucols2)
        loads = [df2.at[idx, f"dtlb_loads_{t}"] for t in (1, 2, 3)]
        misses = [df2.at[idx, f"dtlb_misses_{t}"] for t in (1, 2, 3)]
        mean_loads = float(np.mean(loads))
        mean_misses = float(np.mean(misses))
        miss_rate = mean_misses / mean_loads if mean_loads > 0 else 0.0
        rows.append({
            "row": idx,
            "label": row_label(idx, spared),
            "kind": row_kind(idx),
            "spared_key": spared,
            "dtlb_loads": loads,
            "dtlb_misses": misses,
            "mean_loads": mean_loads,
            "mean_misses": mean_misses,
            "miss_rate": miss_rate,
        })
    return rows


def build_hotkey_analysis(df1: pd.DataFrame, ucols: list[str]):
    """For rows 2-31, report which key was spared, its access count in the
    baseline row (row 1), and the runtime delta vs that no-split baseline."""
    no_split_runtimes = [df1.at[NO_SPLIT_ROW_IDX, f"runtime_s_{t}"] for t in (1, 2, 3)]
    no_split_mean = float(np.mean(no_split_runtimes))

    # Access counts from row 1 (baseline / no-break) for every user key
    row1_counts = df1.loc[NO_SPLIT_ROW_IDX, ucols]

    entries = []
    for idx in range(2, len(df1)):
        spared = spared_key_for_row(df1, idx, ucols)
        if spared is None:
            continue
        access_count = int(row1_counts[spared])
        trial_runtimes = [df1.at[idx, f"runtime_s_{t}"] for t in (1, 2, 3)]
        mean_rt = float(np.mean(trial_runtimes))
        runtime_delta_seconds = no_split_mean - mean_rt
        entries.append({
            "row": idx,
            "label": row_label(idx, spared),
            "spared_key": spared,
            "short_key": short_key_label(spared),
            "access_count_in_no_split": access_count,
            "mean_runtime": mean_rt,
            "runtime_delta_vs_no_split_seconds": runtime_delta_seconds,
            "runtime_improvement_pct_vs_no_split": (
                (runtime_delta_seconds / no_split_mean) * 100
                if no_split_mean
                else 0.0
            ),
        })
    return {
        "reference_row": NO_SPLIT_ROW_IDX,
        "no_split_mean_runtime": no_split_mean,
        "entries": entries,
    }


def build_access_distribution(df1: pd.DataFrame, ucols: list[str]):
    """Bucket row-1 key accesses into cold-to-hot ranges for the dashboard.

    The raw access distribution is extremely skewed: a linear 50-bin histogram
    collapses almost all keys into the first bar, which hides the useful shape.
    These buckets keep the cold-key mass visible while still separating the
    hottest tail.
    """
    counts = df1.loc[1, ucols].values.astype(float)
    bins = []
    for start, end, label in ACCESS_COUNT_BUCKETS:
        if end is None:
            bucket_count = int(np.sum(counts >= start))
        else:
            bucket_count = int(np.sum((counts >= start) & (counts <= end)))
        bins.append({
            "label": label,
            "bin_start": start,
            "bin_end": end,
            "count": bucket_count,
        })
    return {
        "total_keys": len(ucols),
        "min": float(counts.min()),
        "max": float(counts.max()),
        "mean": float(counts.mean()),
        "median": float(np.median(counts)),
        "bucket_strategy": "custom_access_ranges",
        "bins": bins,
    }


def build_top_keys(df1: pd.DataFrame, ucols: list[str], n: int = 30):
    """Top N most-accessed keys (from row 1) with their spared-row runtime."""
    row1_counts = df1.loc[NO_SPLIT_ROW_IDX, ucols].sort_values(ascending=False)
    top = row1_counts.head(n)

    # Build a map: spared_key -> row index
    spared_to_row = {}
    for idx in range(2, len(df1)):
        sk = spared_key_for_row(df1, idx, ucols)
        if sk:
            spared_to_row[sk] = idx

    no_split_mean = float(
        np.mean([df1.at[NO_SPLIT_ROW_IDX, f"runtime_s_{t}"] for t in (1, 2, 3)])
    )

    entries = []
    for key, access_count in top.items():
        row_idx = spared_to_row.get(key)
        if row_idx is not None:
            rts = [df1.at[row_idx, f"runtime_s_{t}"] for t in (1, 2, 3)]
            mean_rt = float(np.mean(rts))
        else:
            mean_rt = None
        runtime_delta_seconds = (no_split_mean - mean_rt) if mean_rt is not None else None
        entries.append({
            "key": key,
            "short_key": short_key_label(key),
            "access_count": int(access_count),
            "spared_row": row_idx,
            "spared_mean_runtime": mean_rt,
            "runtime_delta_vs_no_split_seconds": runtime_delta_seconds,
            "runtime_improvement_pct_vs_no_split": (
                (runtime_delta_seconds / no_split_mean) * 100
                if runtime_delta_seconds is not None and no_split_mean
                else None
            ),
        })
    return {
        "reference_row": NO_SPLIT_ROW_IDX,
        "no_split_mean_runtime": no_split_mean,
        "entries": entries,
    }


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def write_json(data, filename: str):
    path = OUTPUT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, separators=(",", ":"), cls=NumpyEncoder)
    print(f"  wrote {path}  ({path.stat().st_size:,} bytes)")


def main():
    print("Reading parquet files...")
    df1 = pd.read_parquet(TEST1_PATH)
    df2 = pd.read_parquet(TEST2_PATH)
    ucols1 = user_columns(df1)
    ucols2 = user_columns(df2)
    print(f"  test1: {df1.shape}  ({len(ucols1)} user keys)")
    print(f"  test2: {df2.shape}  ({len(ucols2)} user keys)")

    print("\nGenerating JSON files...")
    write_json(build_runtime_comparison(df1, df2, ucols1, ucols2), "runtime_comparison.json")
    write_json(build_dtlb_comparison(df2, ucols2), "dtlb_comparison.json")
    write_json(build_hotkey_analysis(df1, ucols1), "hotkey_analysis.json")
    write_json(build_access_distribution(df1, ucols1), "access_distribution.json")
    write_json(build_top_keys(df1, ucols1), "top_keys.json")
    print("\nDone.")


if __name__ == "__main__":
    main()
