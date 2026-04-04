#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
from matplotlib.colors import ListedColormap


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot cumulative vaptr access-bit samples over time."
    )
    parser.add_argument(
        "--run-root",
        default="data/curated/redis",
        type=Path,
        help="Root directory containing Redis collection directories",
    )
    parser.add_argument(
        "--prefix",
        default="vaptr-access-bit-docker",
        help="Collection directory prefix to select the latest run",
    )
    parser.add_argument(
        "--output",
        default="figures/vaptr_access_over_time.png",
        type=Path,
        help="Output figure path",
    )
    return parser.parse_args()


def latest_run_dir(run_root: Path, prefix: str) -> Path:
    candidates = sorted(path for path in run_root.glob(f"{prefix}*") if path.is_dir())
    if not candidates:
        raise FileNotFoundError(f"no collection directories found for prefix {prefix!r}")
    return candidates[-1]


def load_vaptr(run_dir: Path) -> pl.DataFrame:
    vaptr_path = run_dir / "vaptr.end.parquet"
    if not vaptr_path.is_file():
        raise FileNotFoundError(f"missing {vaptr_path}")

    df = pl.read_parquet(vaptr_path).sort(["ts_uptime_us", "key"])
    available = (
        df.filter(pl.col("available"))
        .with_columns(
            pl.when(pl.col("access_bit").is_null())
            .then(0)
            .otherwise(pl.col("access_bit").cast(pl.Int64()))
            .cum_sum()
            .over("key")
            .alias("cumulative_accesses")
        )
        .with_columns(
            ((pl.col("ts_uptime_us") - pl.col("ts_uptime_us").min()) / 1_000_000)
            .alias("elapsed_s")
        )
    )
    if available.is_empty():
        raise ValueError(f"{vaptr_path} has no available vaptr rows")
    return available


def rank_keys(df: pl.DataFrame) -> pl.DataFrame:
    return (
        df.group_by("key")
        .agg(pl.col("cumulative_accesses").max().alias("final_accesses"))
        .sort(["final_accesses", "key"], descending=[True, False])
        .with_row_index(name="rank", offset=1)
    )


def plot(df: pl.DataFrame, ranks: pl.DataFrame, output: Path) -> None:
    plot_df = df.join(ranks, on="key", how="inner").sort(["rank", "ts_uptime_us"])
    ranked_keys = ranks.select(["rank", "key", "final_accesses"]).iter_rows(named=True)

    fig, (ax_line, ax_heatmap) = plt.subplots(
        2,
        1,
        figsize=(10, 8),
        height_ratios=[2.4, 1.2],
        constrained_layout=True,
    )

    for row in ranked_keys:
        key = row["key"]
        rank = row["rank"]
        final_accesses = row["final_accesses"]
        key_df = plot_df.filter(pl.col("key") == key)
        ax_line.plot(
            key_df["elapsed_s"].to_list(),
            key_df["cumulative_accesses"].to_list(),
            linewidth=1.2,
            label=f"r{rank}: {key[-6:]} ({final_accesses})",
        )

    ax_line.set_title("VAPTR Sampled Pages: Cumulative access_bit=true by final rank")
    ax_line.set_xlabel("Elapsed time (s)")
    ax_line.set_ylabel("Cumulative positive samples")
    ax_line.legend(ncol=2, fontsize=7, frameon=False)
    ax_line.grid(alpha=0.2)

    sample_index_df = (
        plot_df.select("ts_uptime_us")
        .unique()
        .sort("ts_uptime_us")
        .with_row_index(name="sample_index")
    )
    heatmap_df = (
        plot_df.join(sample_index_df, on="ts_uptime_us", how="inner")
        .select(
            "rank",
            "sample_index",
            pl.when(pl.col("access_bit").is_null())
            .then(-1)
            .otherwise(pl.col("access_bit").cast(pl.Int8()))
            .alias("access_state"),
        )
        .pivot(
            index="rank",
            on="sample_index",
            values="access_state",
            aggregate_function="first",
            sort_columns=True,
        )
        .sort("rank")
    )

    sample_columns = [col for col in heatmap_df.columns if col != "rank"]
    matrix = heatmap_df.select(sample_columns).to_numpy()
    cmap = ListedColormap(["#d9d9d9", "#f7f7f7", "#2166ac"])
    ax_heatmap.imshow(matrix + 1, aspect="auto", interpolation="nearest", cmap=cmap)
    ax_heatmap.set_title("Per-sample access_bit state by final rank")
    ax_heatmap.set_xlabel("Sample index")
    ax_heatmap.set_ylabel("Final rank")
    ax_heatmap.set_yticks(np.arange(len(heatmap_df)))
    ax_heatmap.set_yticklabels(heatmap_df["rank"].to_list())

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=200)
    print(f"figure={output}")
    print(
        ranks.select("rank", "key", "final_accesses")
        .sort("rank")
        .to_pandas()
        .to_string(index=False)
    )


def main() -> int:
    args = parse_args()
    run_dir = latest_run_dir(args.run_root, args.prefix)
    df = load_vaptr(run_dir)
    ranks = rank_keys(df)
    plot(df, ranks, args.output)

    unique_ts = df.select("ts_uptime_us").unique().sort("ts_uptime_us")
    intervals = unique_ts.select(pl.col("ts_uptime_us").diff().alias("d")).drop_nulls()
    if not intervals.is_empty():
        print(f"run_dir={run_dir}")
        print(f"unique_samples={len(unique_ts)}")
        print(f"mean_interval_ms={intervals['d'].mean() / 1000:.3f}")
        print(f"median_interval_ms={intervals['d'].median() / 1000:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
