from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

VMA_KEY_COLS = ["start_addr", "end_addr", "perms", "offset", "dev", "inode", "pathname"]


def load_proc_maps(run_dir: Path) -> pl.DataFrame:
    paths = sorted(run_dir.glob("proc_maps.*.parquet"))
    if not paths:
        print(f"ERROR: No proc_maps parquet files in {run_dir}", file=sys.stderr)
        sys.exit(1)
    return pl.concat([pl.read_parquet(p) for p in paths], how="diagonal_relaxed")


def load_benchmark_timing(run_dir: Path) -> tuple[int, int]:
    """Return (uptime_sec, collection_time_sec) from system_info parquet."""
    paths = sorted(run_dir.glob("system_info.*.parquet"))
    if not paths:
        print(f"ERROR: No system_info parquet files in {run_dir}", file=sys.stderr)
        sys.exit(1)
    df = pl.read_parquet(paths[0])
    return int(df["uptime_sec"][0]), int(df["collection_time_sec"][0])


def snapshot_at_pct(
    df: pl.DataFrame,
    uptime_sec: int,
    collection_time_sec: int,
    pct: float,
) -> tuple[pl.DataFrame, float]:
    """Find the snapshot closest to pct% of the benchmark.

    Returns (vma_dataframe, actual_seconds_into_benchmark).
    """
    start_us = uptime_sec * 1_000_000
    target_us = start_us + int(collection_time_sec * 1_000_000 * pct / 100.0)

    timestamps = df["ts_uptime_us"].unique().sort()
    # find closest timestamp
    diffs = (timestamps - target_us).abs()
    closest_ts = int(timestamps[diffs.arg_min()])

    snap = (
        df.filter(pl.col("ts_uptime_us") == closest_ts)
        .select(VMA_KEY_COLS + ["size_kb"])
        .sort("start_addr")
    )
    actual_sec = (closest_ts - start_us) / 1_000_000.0
    return snap, actual_sec


def format_addr(addr: int) -> str:
    return f"0x{addr:012x}"


def print_vma_table(df: pl.DataFrame) -> None:
    if df.is_empty():
        print("(none)")
        return
    header = f"{'start_addr':<16} {'end_addr':<16} {'size_kb':>8}  {'perms':<5}  {'offset':<16} {'dev':<6} {'inode':<10} {'pathname'}"
    print(header)
    for row in df.iter_rows(named=True):
        start = format_addr(int(row["start_addr"]))
        end = format_addr(int(row["end_addr"]))
        size = int(row["size_kb"])
        perms = str(row["perms"])
        offset = format_addr(int(row["offset"]))
        dev = str(row["dev"])
        inode = int(row["inode"])
        pathname = str(row["pathname"]) if row["pathname"] else "(anonymous)"
        print(f"{start:<16} {end:<16} {size:>8}  {perms:<5}  {offset:<16} {dev:<6} {inode:<10} {pathname}")


def compare_at_pct(
    df_a: pl.DataFrame,
    info_a: tuple[int, int],
    df_b: pl.DataFrame,
    info_b: tuple[int, int],
    pct: float,
    label_a: str,
    label_b: str,
) -> None:
    snap_a, time_a = snapshot_at_pct(df_a, info_a[0], info_a[1], pct)
    snap_b, time_b = snapshot_at_pct(df_b, info_b[0], info_b[1], pct)

    print(f"\n{'='*100}")
    print(f"VMA Comparison at {pct:.1f}% (Run A: t={time_a:.1f}s/{info_a[1]}s, Run B: t={time_b:.1f}s/{info_b[1]}s)")
    print(f"{'='*100}")
    print(f"Run A: {snap_a.height} VMAs | Run B: {snap_b.height} VMAs")

    common = snap_a.join(snap_b, on=VMA_KEY_COLS, how="semi").sort("pathname", "start_addr")
    only_a = snap_a.join(snap_b, on=VMA_KEY_COLS, how="anti").sort("pathname", "start_addr")
    only_b = snap_b.join(snap_a, on=VMA_KEY_COLS, how="anti").sort("pathname", "start_addr")

    print(f"\nCOMMON VMAs ({common.height}) — sorted by pathname:")
    print_vma_table(common)

    print(f"\nDIFF: VMAs only in Run A ({only_a.height}):")
    print_vma_table(only_a)

    print(f"\nDIFF: VMAs only in Run B ({only_b.height}):")
    print_vma_table(only_b)

    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare VMA layouts (proc_maps) across two redis collection runs.",
    )
    parser.add_argument("run_a", type=Path, help="Path to first run directory")
    parser.add_argument("run_b", type=Path, help="Path to second run directory")
    parser.add_argument(
        "--percentages",
        type=float,
        nargs="+",
        default=[100.0],
        help="Benchmark time percentages to compare at (default: 100)",
    )
    args = parser.parse_args()

    df_a = load_proc_maps(args.run_a)
    df_b = load_proc_maps(args.run_b)
    info_a = load_benchmark_timing(args.run_a)
    info_b = load_benchmark_timing(args.run_b)
    label_a = args.run_a.name
    label_b = args.run_b.name

    for pct in args.percentages:
        compare_at_pct(df_a, info_a, df_b, info_b, pct, label_a, label_b)


if __name__ == "__main__":
    main()
