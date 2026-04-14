"""Analyze only the end-only 10-key replay rerun and render one graph.

This script reads the new rerun artifact
``data/test2_endpoll_results_10keys_12rows.parquet`` and writes:

- ``temp_data_analysis/endpoll_10keys_runtime_impact.png``
- ``temp_data_analysis/endpoll_10keys_metrics.json``
- ``temp_data_analysis/endpoll_10keys_summary.md``

The graph is intentionally scoped to this one rerun. It does not compare
against older result parquet files.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = REPO_ROOT / "data" / "test2_endpoll_results_10keys_12rows.parquet"
TRACE_PATH = REPO_ROOT / "data" / "redis_traces" / "monitor_run.log"
OUTPUT_DIR = REPO_ROOT / "temp_data_analysis"
GRAPH_PATH = OUTPUT_DIR / "endpoll_10keys_runtime_impact.png"
METRICS_PATH = OUTPUT_DIR / "endpoll_10keys_metrics.json"
ROWS_PATH = OUTPUT_DIR / "endpoll_10keys_rows.json"
SUMMARY_PATH = OUTPUT_DIR / "endpoll_10keys_summary.md"
GENERATE_BREAKPOINTS_PATH = (
    REPO_ROOT / "python" / "kernmlops" / "replay" / "generate_breakpoints.py"
)


def _load_access_counts() -> dict[str, int]:
    """Return per-key access counts for the captured replay trace.

    The rerun parquet does not store access counts, so the script recovers
    hotness from the matching ``monitor_run.log`` used to create the
    breakpoint matrix for this same rerun.
    """

    spec = importlib.util.spec_from_file_location(
        "generate_breakpoints",
        GENERATE_BREAKPOINTS_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load generate_breakpoints.py for access counts")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_log(TRACE_PATH)


def _classify_rows(df: pl.DataFrame) -> pl.DataFrame:
    """Attach row labels, access counts, and mean metrics for this rerun.

    Returns:
        A DataFrame with one row per breakpoint combination and extra columns
        that make plotting and summary generation easier.
    """

    access_counts = _load_access_counts()
    runtime_cols = [c for c in df.columns if c.startswith("runtime_s_")]
    load_cols = [c for c in df.columns if c.startswith("dtlb_loads_")]
    miss_cols = [c for c in df.columns if c.startswith("dtlb_misses_")]
    bp_cols = [
        c
        for c in df.columns
        if c not in runtime_cols and c not in load_cols and c not in miss_cols
    ]

    row_types: list[str] = []
    keys: list[str | None] = []
    for row in df.select(bp_cols).iter_rows(named=True):
        zeros = [key for key, value in row.items() if value == 0]
        if len(zeros) == len(bp_cols):
            row_types.append("all_break")
            keys.append(None)
        elif len(zeros) == 0:
            row_types.append("no_break")
            keys.append(None)
        elif len(zeros) == 1:
            row_types.append("split_only")
            keys.append(zeros[0])
        else:
            row_types.append("other")
            keys.append(None)

    classified = df.with_columns(
        pl.Series("row_type", row_types),
        pl.Series("key", keys),
        df.select(runtime_cols).mean_horizontal().alias("runtime_mean"),
        df.select(load_cols).mean_horizontal().alias("dtlb_loads_mean"),
        df.select(miss_cols).mean_horizontal().alias("dtlb_misses_mean"),
    )

    no_break_runtime = classified.filter(pl.col("row_type") == "no_break")[
        "runtime_mean"
    ][0]

    split_only = (
        classified.filter(pl.col("row_type") == "split_only")
        .with_columns(
            pl.col("key")
            .map_elements(lambda key: access_counts.get(key, 0), return_dtype=pl.Int64)
            .alias("access_count"),
            (pl.col("runtime_mean") - no_break_runtime).alias(
                "runtime_delta_vs_no_break"
            ),
            (pl.col("dtlb_misses_mean") / pl.col("dtlb_loads_mean")).alias(
                "dtlb_miss_rate"
            ),
        )
        .sort("access_count", descending=True)
        .with_row_index(name="hot_rank", offset=1)
        .with_columns(
            pl.format("hot#{}", pl.col("hot_rank")).alias("short_label"),
        )
    )

    baselines = classified.filter(pl.col("row_type") != "split_only").with_columns(
        pl.lit(None).cast(pl.Int64).alias("access_count"),
        pl.when(pl.col("row_type") == "all_break")
        .then(pl.col("runtime_mean") - no_break_runtime)
        .otherwise(0.0)
        .alias("runtime_delta_vs_no_break"),
        pl.lit(None).cast(pl.Float64).alias("dtlb_miss_rate"),
        pl.when(pl.col("row_type") == "all_break")
        .then(pl.lit("all_break"))
        .otherwise(pl.lit("no_break"))
        .alias("short_label"),
        pl.lit(None).cast(pl.Int64).alias("hot_rank"),
    )

    return pl.concat([baselines, split_only], how="diagonal_relaxed")


def _write_metrics(analysis_df: pl.DataFrame) -> dict[str, object]:
    """Write a compact JSON metrics summary for the new rerun only."""

    no_break = analysis_df.filter(pl.col("row_type") == "no_break")
    all_break = analysis_df.filter(pl.col("row_type") == "all_break")
    split_only = analysis_df.filter(pl.col("row_type") == "split_only")

    metrics = {
        "results_path": str(RESULTS_PATH),
        "rows": int(analysis_df.height),
        "all_break_runtime_s": float(all_break["runtime_mean"][0]),
        "no_break_runtime_s": float(no_break["runtime_mean"][0]),
        "all_break_pct_slower_than_no_break": float(
            (all_break["runtime_mean"][0] / no_break["runtime_mean"][0] - 1) * 100
        ),
        "split_rows": int(split_only.height),
        "split_rows_slower_than_no_break": int(
            split_only.filter(pl.col("runtime_delta_vs_no_break") > 0).height
        ),
        "split_rows_faster_than_no_break": int(
            split_only.filter(pl.col("runtime_delta_vs_no_break") < 0).height
        ),
        "split_mean_delta_s": float(split_only["runtime_delta_vs_no_break"].mean()),
        "split_median_delta_s": float(
            split_only["runtime_delta_vs_no_break"].median()
        ),
        "corr_access_vs_runtime_delta": float(
            split_only.select(pl.corr("access_count", "runtime_delta_vs_no_break")).item()
        ),
        "corr_runtime_delta_vs_dtlb_misses": float(
            split_only.select(pl.corr("runtime_delta_vs_no_break", "dtlb_misses_mean")).item()
        ),
    }

    METRICS_PATH.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return metrics


def _write_rows(analysis_df: pl.DataFrame) -> None:
    """Write row-level JSON that the HTML dashboard can load directly."""

    row_columns = [
        "row_type",
        "key",
        "short_label",
        "hot_rank",
        "access_count",
        "runtime_mean",
        "runtime_delta_vs_no_break",
        "dtlb_loads_mean",
        "dtlb_misses_mean",
        "dtlb_miss_rate",
    ]
    records = analysis_df.select(row_columns).sort(
        ["row_type", "hot_rank"],
        descending=[False, False],
        nulls_last=True,
    ).to_dicts()
    ROWS_PATH.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def _write_summary(analysis_df: pl.DataFrame, metrics: dict[str, object]) -> None:
    """Write a short markdown summary for this rerun only."""

    split_only = analysis_df.filter(pl.col("row_type") == "split_only").sort(
        "access_count", descending=True
    )
    slowest = split_only.sort("runtime_delta_vs_no_break", descending=True).head(3)
    hottest = split_only.head(3)

    lines = [
        "# End-Only 10-Key Rerun Summary",
        "",
        f"Source parquet: `{RESULTS_PATH.relative_to(REPO_ROOT)}`",
        "",
        "## Important Takeaways",
        "",
        f"- `all_break` averaged `{metrics['all_break_runtime_s']:.3f}s` and `no_break` averaged `{metrics['no_break_runtime_s']:.3f}s`.",
        f"- `all_break` was `{metrics['all_break_pct_slower_than_no_break']:.1f}%` slower than `no_break`.",
        f"- `{metrics['split_rows_slower_than_no_break']}/10` split-only rows were slower than `no_break`.",
        f"- Mean split-only slowdown versus `no_break`: `{metrics['split_mean_delta_s']:+.3f}s`.",
        f"- Correlation between access count and runtime delta: `{metrics['corr_access_vs_runtime_delta']:+.3f}`.",
        "",
        "## Interpretation",
        "",
        "- This rerun still says that splitting everything is bad.",
        "- It does not say that the hottest key is always the worst key.",
        "- In this rerun, the hottest tested key was slightly faster than `no_break`, while several medium-hot keys were the worst rows.",
        "",
        "## Hottest Tested Keys",
        "",
    ]

    for row in hottest.iter_rows(named=True):
        lines.append(
            f"- `{row['short_label']}` = `{row['key']}` with `{row['access_count']}` accesses, "
            f"runtime delta `{row['runtime_delta_vs_no_break']:+.3f}s`."
        )

    lines.extend(
        [
            "",
            "## Slowest Rows In This Rerun",
            "",
        ]
    )

    for row in slowest.iter_rows(named=True):
        lines.append(
            f"- `{row['short_label']}` = `{row['key']}` with `{row['access_count']}` accesses, "
            f"runtime delta `{row['runtime_delta_vs_no_break']:+.3f}s`."
        )

    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _render_graph(analysis_df: pl.DataFrame, metrics: dict[str, object]) -> None:
    """Render one figure that only explores the new rerun."""

    split_only = analysis_df.filter(pl.col("row_type") == "split_only").sort(
        "runtime_delta_vs_no_break", descending=True
    )
    baselines = analysis_df.filter(pl.col("row_type") != "split_only").sort(
        "runtime_delta_vs_no_break", descending=True
    )
    bar_df = pl.concat([baselines, split_only], how="diagonal_relaxed")

    labels = bar_df["short_label"].to_list()
    deltas = bar_df["runtime_delta_vs_no_break"].to_list()
    colors = []
    for row in bar_df.iter_rows(named=True):
        if row["row_type"] == "all_break":
            colors.append("#c94f4f")
        elif row["row_type"] == "no_break":
            colors.append("#7f8c8d")
        elif row["runtime_delta_vs_no_break"] > 0:
            colors.append("#d89b31")
        else:
            colors.append("#2f7d6b")

    split_scatter = analysis_df.filter(pl.col("row_type") == "split_only").sort(
        "access_count", descending=True
    )

    fig, (ax_bar, ax_scatter) = plt.subplots(
        2,
        1,
        figsize=(13, 10),
        gridspec_kw={"height_ratios": [1.35, 1.0]},
    )

    y_positions = list(range(len(labels)))
    ax_bar.barh(y_positions, deltas, color=colors, edgecolor="black", alpha=0.9)
    ax_bar.axvline(0, color="black", linewidth=1.0)
    ax_bar.set_yticks(y_positions)
    ax_bar.set_yticklabels(labels)
    ax_bar.invert_yaxis()
    ax_bar.set_xlabel("Runtime Delta Vs no_break (seconds)")
    ax_bar.set_title(
        "End-Only 10-Key Rerun: Runtime Delta Vs no_break\n"
        f"all_break is {metrics['all_break_pct_slower_than_no_break']:.1f}% slower than no_break"
    )
    ax_bar.grid(axis="x", linestyle="--", alpha=0.3)

    misses = split_scatter["dtlb_misses_mean"].to_list()
    scatter = ax_scatter.scatter(
        split_scatter["access_count"].to_list(),
        split_scatter["runtime_delta_vs_no_break"].to_list(),
        c=misses,
        cmap="YlOrRd",
        s=160,
        edgecolors="black",
        linewidths=0.8,
        alpha=0.9,
    )
    ax_scatter.axhline(0, color="black", linewidth=1.0)
    ax_scatter.set_xlabel("Access Count In Captured Trace")
    ax_scatter.set_ylabel("Runtime Delta Vs no_break (seconds)")
    ax_scatter.set_title(
        "Hotness Vs Runtime Impact In This Rerun\n"
        f"corr(access_count, runtime_delta) = {metrics['corr_access_vs_runtime_delta']:+.2f}"
    )
    ax_scatter.grid(True, linestyle="--", alpha=0.3)

    for row in split_scatter.iter_rows(named=True):
        ax_scatter.annotate(
            row["short_label"],
            (row["access_count"], row["runtime_delta_vs_no_break"]),
            textcoords="offset points",
            xytext=(6, 6),
            fontsize=9,
        )

    cbar = fig.colorbar(scatter, ax=ax_scatter)
    cbar.set_label("Mean dTLB Misses")

    fig.text(
        0.01,
        0.01,
        "Labels: hot#N follow hotness rank within this rerun only. "
        "Data source: data/test2_endpoll_results_10keys_12rows.parquet",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(GRAPH_PATH, dpi=200)
    plt.close(fig)


def main() -> None:
    """Generate the graph and summary artifacts for the new rerun."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    analysis_df = _classify_rows(pl.read_parquet(RESULTS_PATH))
    metrics = _write_metrics(analysis_df)
    _write_rows(analysis_df)
    _write_summary(analysis_df, metrics)
    _render_graph(analysis_df, metrics)


if __name__ == "__main__":
    main()
