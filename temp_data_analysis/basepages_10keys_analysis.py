"""Analyze the runtime-only 10-key rerun with the base-pages baseline.

This script reads
``data/test3_basepages_results_10keys_12rows.parquet`` and writes a small set
of temp-only artifacts for that rerun:

- ``temp_data_analysis/basepages_10keys_runtime_impact.png``
- ``temp_data_analysis/basepages_10keys_metrics.json``
- ``temp_data_analysis/basepages_10keys_rows.json``
- ``temp_data_analysis/basepages_10keys_summary.md``

The all-zero breakpoint row is treated as a ``base_pages`` baseline because
replay now restarts Redis with ``disable-thp yes`` for that row instead of
issuing a split syscall for every tracked key.
"""

from __future__ import annotations

import importlib.util
import json
import re
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = REPO_ROOT / "data" / "test3_basepages_results_10keys_12rows.parquet"
TRACE_PATH = REPO_ROOT / "data" / "redis_traces" / "monitor_run.log"
OUTPUT_DIR = REPO_ROOT / "temp_data_analysis"
GRAPH_PATH = OUTPUT_DIR / "basepages_10keys_runtime_impact.png"
METRICS_PATH = OUTPUT_DIR / "basepages_10keys_metrics.json"
ROWS_PATH = OUTPUT_DIR / "basepages_10keys_rows.json"
SUMMARY_PATH = OUTPUT_DIR / "basepages_10keys_summary.md"
DASHBOARD_PATH = OUTPUT_DIR / "basepages_10keys_dashboard.html"
GENERATE_BREAKPOINTS_PATH = (
    REPO_ROOT / "python" / "kernmlops" / "replay" / "generate_breakpoints.py"
)


def _load_access_counts() -> dict[str, int]:
    """Return per-key access counts for the replay trace used by this rerun.

    Returns:
        A ``{key: access_count}`` mapping reconstructed from
        ``data/redis_traces/monitor_run.log``.

    Raises:
        RuntimeError: If ``generate_breakpoints.py`` cannot be imported.
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


def _classify_rows(df: pl.DataFrame) -> list[dict[str, object]]:
    """Return row records for the new runtime-only rerun.

    Args:
        df: The results parquet loaded into memory.

    Returns:
        One dictionary per breakpoint combination. Example output:
            {
                "row_type": "base_pages",
                "short_label": "base_pages",
                "runtime_mean": 21.4,
                "runtime_std": 0.3,
            }
    """

    access_counts = _load_access_counts()
    runtime_cols = [column for column in df.columns if column.startswith("runtime_s_")]
    breakpoint_cols = [column for column in df.columns if column not in runtime_cols]
    records: list[dict[str, object]] = []

    for row in df.iter_rows(named=True):
        breakpoints = {key: row[key] for key in breakpoint_cols}
        zeros = [key for key, value in breakpoints.items() if value == 0]
        runtime_samples = [float(row[column]) for column in runtime_cols]

        if len(zeros) == len(breakpoints) and breakpoints:
            row_type = "base_pages"
            key = None
        elif len(zeros) == 0:
            row_type = "no_break"
            key = None
        elif len(zeros) == 1:
            row_type = "split_only"
            key = zeros[0]
        else:
            row_type = "other"
            key = None

        records.append(
            {
                "row_type": row_type,
                "key": key,
                "runtime_mean": sum(runtime_samples) / len(runtime_samples),
                "runtime_std": (
                    statistics.stdev(runtime_samples)
                    if len(runtime_samples) > 1
                    else 0.0
                ),
                "runtime_samples": runtime_samples,
                "access_count": access_counts.get(key, 0) if key is not None else None,
            }
        )

    no_break_runtime = next(
        record["runtime_mean"] for record in records if record["row_type"] == "no_break"
    )

    split_only = sorted(
        (record for record in records if record["row_type"] == "split_only"),
        key=lambda record: (-int(record["access_count"]), str(record["key"])),
    )
    for hot_rank, record in enumerate(split_only, start=1):
        record["hot_rank"] = hot_rank
        record["short_label"] = f"hot#{hot_rank}"
        record["runtime_delta_vs_no_break"] = (
            float(record["runtime_mean"]) - no_break_runtime
        )
        record["runtime_pct_vs_no_break"] = (
            float(record["runtime_mean"]) / no_break_runtime - 1
        ) * 100

    ordered_records: list[dict[str, object]] = []
    for row_type in ("base_pages", "no_break"):
        record = next(record for record in records if record["row_type"] == row_type)
        record["hot_rank"] = None
        record["short_label"] = row_type
        record["runtime_delta_vs_no_break"] = (
            float(record["runtime_mean"]) - no_break_runtime
        )
        record["runtime_pct_vs_no_break"] = (
            float(record["runtime_mean"]) / no_break_runtime - 1
        ) * 100
        ordered_records.append(record)

    ordered_records.extend(split_only)
    return ordered_records


def _write_metrics(records: list[dict[str, object]]) -> dict[str, object]:
    """Write a compact JSON summary for the new base-pages rerun."""

    base_pages = next(record for record in records if record["row_type"] == "base_pages")
    no_break = next(record for record in records if record["row_type"] == "no_break")
    split_only = [record for record in records if record["row_type"] == "split_only"]

    split_deltas = [float(record["runtime_delta_vs_no_break"]) for record in split_only]
    split_pct = [float(record["runtime_pct_vs_no_break"]) for record in split_only]
    corr_value = (
        pl.DataFrame(
            {
                "access_count": [int(record["access_count"]) for record in split_only],
                "runtime_pct_vs_no_break": split_pct,
            }
        )
        .select(pl.corr("access_count", "runtime_pct_vs_no_break"))
        .item()
    )

    metrics = {
        "results_path": str(RESULTS_PATH),
        "rows": len(records),
        "base_pages_runtime_s": float(base_pages["runtime_mean"]),
        "no_break_runtime_s": float(no_break["runtime_mean"]),
        "base_pages_pct_vs_no_break": float(base_pages["runtime_pct_vs_no_break"]),
        "split_rows": len(split_only),
        "split_rows_slower_than_no_break": sum(delta > 0 for delta in split_deltas),
        "split_rows_faster_than_no_break": sum(delta < 0 for delta in split_deltas),
        "split_mean_delta_s": sum(split_deltas) / len(split_deltas),
        "split_mean_delta_pct": sum(split_pct) / len(split_pct),
        "corr_access_vs_runtime_pct": float(corr_value),
    }

    METRICS_PATH.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return metrics


def _write_rows(records: list[dict[str, object]]) -> None:
    """Write row-level JSON for the HTML dashboard."""

    ROWS_PATH.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def _replace_inline_json_script(
    html: str,
    script_id: str,
    payload: object,
) -> str:
    """Replace one embedded JSON block inside the dashboard HTML.

    Args:
        html: Full dashboard HTML source that already contains the target
            ``<script type="application/json">`` block.
        script_id: Exact DOM id for the inline JSON script tag.
        payload: JSON-serializable object that should be embedded. Example
            output:
            {"rows": 12}

    Returns:
        Updated HTML source with the pretty-printed JSON payload embedded
        inside the matching script tag.

    Raises:
        RuntimeError: If the expected inline JSON script tag is missing.
    """

    pattern = re.compile(
        rf'(<script id="{re.escape(script_id)}" type="application/json">\n)'
        rf".*?"
        rf"(\n</script>)",
        re.DOTALL,
    )
    payload_text = json.dumps(payload, indent=2)

    def replace(match: re.Match[str]) -> str:
        return f"{match.group(1)}{payload_text}{match.group(2)}"

    updated_html, replacements = pattern.subn(replace, html, count=1)
    if replacements != 1:
        raise RuntimeError(
            f"Could not find inline JSON block '{script_id}' in {DASHBOARD_PATH}"
        )
    return updated_html


def _write_dashboard_preview(
    metrics: dict[str, object],
    records: list[dict[str, object]],
) -> None:
    """Embed dashboard data directly into the HTML for file-preview use.

    Args:
        metrics: Compact metrics object used by the stat cards. Example output:
            {"rows": 12, "no_break_runtime_s": 21.0}
        records: Row records used by the charts and details table. Example
            output:
            [{"row_type": "base_pages", "short_label": "base_pages"}]

    Raises:
        RuntimeError: If the dashboard HTML is missing the expected inline
            preview-data placeholders.
    """

    html = DASHBOARD_PATH.read_text(encoding="utf-8")
    html = _replace_inline_json_script(html, "basepagesMetricsData", metrics)
    html = _replace_inline_json_script(html, "basepagesRowsData", records)
    DASHBOARD_PATH.write_text(html, encoding="utf-8")


def _write_summary(records: list[dict[str, object]], metrics: dict[str, object]) -> None:
    """Write a short markdown summary for the new rerun."""

    split_only = [record for record in records if record["row_type"] == "split_only"]
    slowest = sorted(
        split_only,
        key=lambda record: float(record["runtime_delta_vs_no_break"]),
        reverse=True,
    )[:3]
    hottest = split_only[:3]

    lines = [
        "# Base-Pages 10-Key Rerun Summary",
        "",
        f"Source parquet: `{RESULTS_PATH.relative_to(REPO_ROOT)}`",
        "",
        "## Important Takeaways",
        "",
        f"- `base_pages` averaged `{metrics['base_pages_runtime_s']:.3f}s` and `no_break` averaged `{metrics['no_break_runtime_s']:.3f}s`.",
        f"- `base_pages` was `{metrics['base_pages_pct_vs_no_break']:+.1f}%` versus `no_break`.",
        f"- `{metrics['split_rows_slower_than_no_break']}/10` split-only rows were slower than `no_break`.",
        f"- Mean split-only delta versus `no_break`: `{metrics['split_mean_delta_s']:+.3f}s` (`{metrics['split_mean_delta_pct']:+.2f}%`).",
        f"- Correlation between access count and runtime delta percent: `{metrics['corr_access_vs_runtime_pct']:+.3f}`.",
        "",
        "## Interpretation",
        "",
        "- This rerun isolates the cost of running without THP from the old split-thp syscall storm.",
        "- `base_pages` should now be read as a no-THP Redis startup baseline, not as \"split every page before access 0.\"",
        "- The split-only rows still test whether breaking one hot key hurts relative to the intact THP baseline.",
        "",
        "## Hottest Tested Keys",
        "",
    ]

    for record in hottest:
        lines.append(
            f"- `{record['short_label']}` = `{record['key']}` with `{record['access_count']}` accesses, "
            f"runtime delta `{record['runtime_delta_vs_no_break']:+.3f}s` "
            f"(`{record['runtime_pct_vs_no_break']:+.2f}%`)."
        )

    lines.extend(["", "## Slowest Rows In This Rerun", ""])

    for record in slowest:
        lines.append(
            f"- `{record['short_label']}` = `{record['key']}` with `{record['access_count']}` accesses, "
            f"runtime delta `{record['runtime_delta_vs_no_break']:+.3f}s` "
            f"(`{record['runtime_pct_vs_no_break']:+.2f}%`)."
        )

    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _render_graph(records: list[dict[str, object]], metrics: dict[str, object]) -> None:
    """Render one PNG that only explores the new base-pages rerun."""

    ordered_rows = [
        next(record for record in records if record["row_type"] == "base_pages"),
        next(record for record in records if record["row_type"] == "no_break"),
    ] + sorted(
        (record for record in records if record["row_type"] == "split_only"),
        key=lambda record: float(record["runtime_pct_vs_no_break"]),
        reverse=True,
    )

    labels = [str(record["short_label"]) for record in ordered_rows]
    pct_deltas = [float(record["runtime_pct_vs_no_break"]) for record in ordered_rows]
    colors = []
    for record in ordered_rows:
        if record["row_type"] == "base_pages":
            colors.append("#c94f4f")
        elif record["row_type"] == "no_break":
            colors.append("#7f8c8d")
        elif float(record["runtime_pct_vs_no_break"]) > 0:
            colors.append("#d89b31")
        else:
            colors.append("#2f7d6b")

    split_only = [record for record in records if record["row_type"] == "split_only"]

    fig, (ax_bar, ax_scatter) = plt.subplots(
        2,
        1,
        figsize=(13, 10),
        gridspec_kw={"height_ratios": [1.35, 1.0]},
    )

    y_positions = list(range(len(labels)))
    ax_bar.barh(y_positions, pct_deltas, color=colors, edgecolor="black", alpha=0.9)
    ax_bar.axvline(0, color="black", linewidth=1.0)
    ax_bar.set_yticks(y_positions)
    ax_bar.set_yticklabels(labels)
    ax_bar.invert_yaxis()
    ax_bar.set_xlabel("Runtime Delta Vs no_break (%)")
    ax_bar.set_title(
        "Base-Pages 10-Key Rerun: Runtime Delta Vs no_break\n"
        f"base_pages is {metrics['base_pages_pct_vs_no_break']:+.1f}% versus no_break"
    )
    ax_bar.grid(axis="x", linestyle="--", alpha=0.3)

    ax_scatter.scatter(
        [int(record["access_count"]) for record in split_only],
        [float(record["runtime_pct_vs_no_break"]) for record in split_only],
        c="#79a8ff",
        s=150,
        edgecolors="black",
        linewidths=0.8,
        alpha=0.9,
    )
    ax_scatter.axhline(0, color="black", linewidth=1.0)
    ax_scatter.set_xlabel("Access Count In Captured Trace")
    ax_scatter.set_ylabel("Runtime Delta Vs no_break (%)")
    ax_scatter.set_title(
        "Hotness Vs Runtime Impact In This Rerun\n"
        f"corr(access_count, runtime_delta_pct) = {metrics['corr_access_vs_runtime_pct']:+.2f}"
    )
    ax_scatter.grid(True, linestyle="--", alpha=0.3)

    for record in split_only:
        ax_scatter.annotate(
            str(record["short_label"]),
            (int(record["access_count"]), float(record["runtime_pct_vs_no_break"])),
            textcoords="offset points",
            xytext=(6, 6),
            fontsize=9,
        )

    fig.text(
        0.01,
        0.01,
        "Labels: hot#N follow hotness rank within this rerun only. "
        "The base_pages row uses Redis disable-thp=yes instead of replay-time split syscalls.",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(GRAPH_PATH, dpi=200)
    plt.close(fig)


def main() -> None:
    """Generate the graph and summary artifacts for the new rerun."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    records = _classify_rows(pl.read_parquet(RESULTS_PATH))
    metrics = _write_metrics(records)
    _write_rows(records)
    _write_dashboard_preview(metrics, records)
    _write_summary(records, metrics)
    _render_graph(records, metrics)


if __name__ == "__main__":
    main()
