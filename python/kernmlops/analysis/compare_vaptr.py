"""Compare virtual addresses of Redis keys across two collection runs.

Usage:
    python compare_vaptr.py <run_a_dir> <run_b_dir> [--percentage PCT]

Example:
    python compare_vaptr.py data/curated/redis/run1/ data/curated/redis/run2/ --percentage 50
"""

import argparse
from pathlib import Path

import polars as pl


def load_vaptr(run_dir: Path) -> pl.DataFrame:
    files = list(run_dir.rglob("vaptr*.parquet"))
    if not files:
        raise FileNotFoundError(f"No vaptr parquet files found in {run_dir}")
    return pl.read_parquet(files[0])


def load_system_info(run_dir: Path) -> tuple[float, float]:
    files = list(run_dir.rglob("system_info.end.parquet"))
    if not files:
        raise FileNotFoundError(f"No system_info.end.parquet in {run_dir}")
    df = pl.read_parquet(files[0])
    uptime_sec = float(df["uptime_sec"][0])
    collection_time_sec = float(df["collection_time_sec"][0])
    return uptime_sec, collection_time_sec


def snapshot_at_percentage(
    df: pl.DataFrame, uptime_sec: float, collection_time_sec: float, pct: float
) -> pl.DataFrame:
    """Get the closest vaptr snapshot to a given percentage of the benchmark timeline."""
    target_us = int((uptime_sec + collection_time_sec * pct / 100.0) * 1e6)
    df = df.with_columns(
        (pl.col("ts_uptime_us") - target_us).abs().alias("_dist")
    )
    closest_ts = df.sort("_dist")["ts_uptime_us"][0]
    return df.filter(pl.col("ts_uptime_us") == closest_ts).drop("_dist")


def compare(run_a_dir: Path, run_b_dir: Path, percentage: float):
    df_a = load_vaptr(run_a_dir)
    df_b = load_vaptr(run_b_dir)

    uptime_a, coll_a = load_system_info(run_a_dir)
    uptime_b, coll_b = load_system_info(run_b_dir)

    snap_a = snapshot_at_percentage(df_a, uptime_a, coll_a, percentage)
    snap_b = snapshot_at_percentage(df_b, uptime_b, coll_b, percentage)

    # Deduplicate to latest sample per key
    snap_a = snap_a.sort("ts_uptime_us", descending=True).unique(subset=["key"], keep="first")
    snap_b = snap_b.sort("ts_uptime_us", descending=True).unique(subset=["key"], keep="first")

    merged = snap_a.select(["key", "address", "page_addr"]).rename(
        {"address": "addr_a", "page_addr": "page_a"}
    ).join(
        snap_b.select(["key", "address", "page_addr"]).rename(
            {"address": "addr_b", "page_addr": "page_b"}
        ),
        on="key",
        how="inner",
    ).with_columns(
        (pl.col("page_a") == pl.col("page_b")).alias("same_page"),
        (pl.col("addr_a") == pl.col("addr_b")).alias("same_addr"),
    ).sort("key")

    print(f"\n{'key':<30} {'Run A':<20} {'Run B':<20} {'Same Page?':<12} {'Same Addr?'}")
    print("-" * 95)

    same_page_count = 0
    same_addr_count = 0
    total = len(merged)

    for row in merged.iter_rows(named=True):
        same_page = "YES" if row["same_page"] else "NO"
        same_addr = "YES" if row["same_addr"] else "NO"
        if row["same_page"]:
            same_page_count += 1
        if row["same_addr"]:
            same_addr_count += 1
        print(
            f"{row['key']:<30} {row['addr_a']:<20} {row['addr_b']:<20} {same_page:<12} {same_addr}"
        )

    print("-" * 95)
    print(f"Summary at {percentage}% of benchmark timeline:")
    print(f"  Same page:    {same_page_count}/{total} keys")
    print(f"  Same address: {same_addr_count}/{total} keys")


def main():
    parser = argparse.ArgumentParser(description="Compare Redis key virtual addresses across runs")
    parser.add_argument("run_a", type=Path, help="Path to first run's data directory")
    parser.add_argument("run_b", type=Path, help="Path to second run's data directory")
    parser.add_argument(
        "--percentage", type=float, default=50.0,
        help="Percentage of benchmark timeline to sample at (default: 50)",
    )
    args = parser.parse_args()
    compare(args.run_a, args.run_b, args.percentage)


if __name__ == "__main__":
    main()
