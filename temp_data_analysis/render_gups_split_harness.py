from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import polars as pl


@dataclass(frozen=True)
class RowSummary:
    """Aggregate metrics for one GUPS breakpoint row.

    Attributes:
        row_label: Human-readable row label from the breakpoint matrix.
        display_label: Chart label with split-success counts when relevant.
        row_kind: Baseline or split-only row kind.
        page_group: Multi-page group label such as ``hot`` or ``random``.
        target_page_count: Number of pages selected by this row.
        split_success_total: Total successful splits across all runs.
        split_expected_successes: Expected successes for pre-split multi-page
            and chunk rows, computed as ``target_page_count * runs``.
        split_max_attempts_max: Maximum retry count seen across all runs.
        runtime_mean: Mean per-run runtime across matrix reruns.
        runtime_std: Sample standard deviation of per-run runtime.
        gups_mean: Mean per-run GUP/s across matrix reruns.
        gups_std: Sample standard deviation of per-run GUP/s.
        dtlb_loads_mean: Mean dTLB loads across reruns.
        dtlb_loads_std: Sample std dev of dTLB loads across reruns.
        dtlb_misses_mean: Mean dTLB misses across reruns.
        dtlb_misses_std: Sample std dev of dTLB misses across reruns.
        include_in_main_charts: Whether this row belongs in the main comparison
            graphs. Split-only rows with zero successful splits are omitted.
    """

    row_label: str
    display_label: str
    row_kind: str
    page_group: str
    target_page_count: int
    split_success_total: int
    split_expected_successes: int
    split_max_attempts_max: int
    runtime_mean: float
    runtime_std: float
    runtime_pct_vs_base_pages_mean: float
    runtime_pct_vs_base_pages_std: float
    speedup_pct_vs_base_pages_mean: float
    speedup_pct_vs_base_pages_std: float
    gups_mean: float
    gups_std: float
    dtlb_loads_mean: float | None
    dtlb_loads_std: float | None
    dtlb_misses_mean: float | None
    dtlb_misses_std: float | None
    include_in_main_charts: bool


def _metric_columns(df: pl.DataFrame, prefix: str) -> list[str]:
    return sorted(
        (
            column
            for column in df.columns
            if column.startswith(f"{prefix}_")
            and column[len(prefix) + 1 :].isdigit()
        ),
        key=lambda column: int(column[len(prefix) + 1 :]),
    )


def _sample_std(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def _required_row_metric_values(
    row: dict[str, object],
    columns: list[str],
    *,
    metric_name: str,
    row_label: str,
) -> list[float]:
    """Return all values for *columns* or fail fast when one is missing.

    Example output:
        [1.0, 1.2, 1.1]

    Args:
        row: One results-parquet row.
        columns: Required metric columns in run order.
        metric_name: Human-readable metric label for errors.
        row_label: Row label for errors.

    Returns:
        The metric values in the same order as *columns*.

    Raises:
        RuntimeError: If one required metric value is missing.
    """
    values: list[float] = []
    missing_columns: list[str] = []
    for column in columns:
        raw_value = row.get(column)
        if raw_value is None:
            missing_columns.append(column)
            continue
        values.append(float(raw_value))

    if missing_columns:
        missing_text = ", ".join(missing_columns)
        raise RuntimeError(
            f"Row {row_label!r} is missing required {metric_name} columns: "
            f"{missing_text}"
        )
    return values


def _row_metric_values(row: dict[str, object], columns: list[str]) -> list[float]:
    return [float(row[column]) for column in columns if row.get(column) is not None]


def _base_runtime_values(
    df: pl.DataFrame,
    runtime_columns: list[str],
) -> list[float]:
    """Return the required per-run runtime values for the `base_pages` row."""
    base_rows = [
        row
        for row in df.iter_rows(named=True)
        if str(row.get("row_kind", "")) == "base_pages"
    ]
    if not base_rows:
        raise RuntimeError(
            "Results parquet is missing the required 'base_pages' baseline row."
        )
    if len(base_rows) != 1:
        raise RuntimeError(
            "Results parquet must contain exactly one 'base_pages' baseline row."
        )

    values = _required_row_metric_values(
        base_rows[0],
        runtime_columns,
        metric_name="runtime_s",
        row_label="base_pages",
    )
    if any(value <= 0.0 for value in values):
        raise RuntimeError(
            "All base_pages runtime values must be positive for normalization."
        )
    return values


def load_row_summaries(results_path: Path) -> list[RowSummary]:
    """Load one matrix results parquet and build chart/table summaries."""
    df = pl.read_parquet(results_path)
    runtime_columns = _metric_columns(df, "runtime_s")
    gups_columns = _metric_columns(df, "gups")
    dtlb_loads_columns = _metric_columns(df, "dtlb_loads")
    dtlb_misses_columns = _metric_columns(df, "dtlb_misses")
    split_success_columns = _metric_columns(df, "split_successes")
    split_max_attempt_columns = _metric_columns(df, "split_max_attempts")
    if not runtime_columns:
        raise RuntimeError("Results parquet is missing required runtime_s_N columns.")
    if not gups_columns:
        raise RuntimeError("Results parquet is missing required gups_N columns.")
    base_runtime_values = _base_runtime_values(df, runtime_columns)

    summaries: list[RowSummary] = []
    for row in df.iter_rows(named=True):
        row_kind = str(row.get("row_kind", ""))
        row_label = str(row.get("row_label", f"row {row.get('row_index', '?')}"))
        runtime_values = _required_row_metric_values(
            row,
            runtime_columns,
            metric_name="runtime_s",
            row_label=row_label,
        )
        if len(runtime_values) != len(base_runtime_values):
            raise RuntimeError(
                f"Row {row_label!r} does not have the same number of runtime "
                "runs as the base_pages baseline."
            )
        if any(value <= 0.0 for value in runtime_values):
            raise RuntimeError(
                f"Row {row_label!r} must have strictly positive runtime values."
            )
        runtime_pct_values = [
            100.0 * ((runtime_value / base_runtime_value) - 1.0)
            for runtime_value, base_runtime_value in zip(
                runtime_values,
                base_runtime_values,
                strict=True,
            )
        ]
        speedup_pct_values = [
            100.0 * ((base_runtime_value / runtime_value) - 1.0)
            for runtime_value, base_runtime_value in zip(
                runtime_values,
                base_runtime_values,
                strict=True,
            )
        ]
        gups_values = _required_row_metric_values(
            row,
            gups_columns,
            metric_name="gups",
            row_label=row_label,
        )
        dtlb_loads_values = _row_metric_values(row, dtlb_loads_columns)
        dtlb_misses_values = _row_metric_values(row, dtlb_misses_columns)
        split_success_total = sum(int(row[column]) for column in split_success_columns)
        target_page_count = int(row.get("target_page_count") or 0)
        page_group = str(row.get("page_group") or "")
        expected_success_kinds = {"split_multi", "split_chunk"}
        split_expected_successes = (
            target_page_count * len(split_success_columns)
            if row_kind in expected_success_kinds
            else 0
        )
        split_max_attempts_max = max(
            [int(row[column]) for column in split_max_attempt_columns],
            default=0,
        )
        include_in_main_charts = (
            row_kind not in {"split_only", "split_multi", "split_chunk"}
            or split_success_total > 0
        )
        if row_kind in expected_success_kinds:
            display_label = (
                f"({split_success_total}/{split_expected_successes}) {row_label}"
            )
        elif row_kind == "split_only":
            display_label = f"({split_success_total}) {row_label}"
        else:
            display_label = row_label

        summaries.append(
            RowSummary(
                row_label=row_label,
                display_label=display_label,
                row_kind=row_kind,
                page_group=page_group,
                target_page_count=target_page_count,
                split_success_total=split_success_total,
                split_expected_successes=split_expected_successes,
                split_max_attempts_max=split_max_attempts_max,
                runtime_mean=statistics.mean(runtime_values),
                runtime_std=_sample_std(runtime_values),
                runtime_pct_vs_base_pages_mean=statistics.mean(runtime_pct_values),
                runtime_pct_vs_base_pages_std=_sample_std(runtime_pct_values),
                speedup_pct_vs_base_pages_mean=statistics.mean(speedup_pct_values),
                speedup_pct_vs_base_pages_std=_sample_std(speedup_pct_values),
                gups_mean=statistics.mean(gups_values),
                gups_std=_sample_std(gups_values),
                dtlb_loads_mean=(
                    statistics.mean(dtlb_loads_values) if dtlb_loads_values else None
                ),
                dtlb_loads_std=(
                    _sample_std(dtlb_loads_values) if dtlb_loads_values else None
                ),
                dtlb_misses_mean=(
                    statistics.mean(dtlb_misses_values) if dtlb_misses_values else None
                ),
                dtlb_misses_std=(
                    _sample_std(dtlb_misses_values) if dtlb_misses_values else None
                ),
                include_in_main_charts=include_in_main_charts,
            )
        )

    return summaries


def _plot_bar_metric(
    *,
    summaries: list[RowSummary],
    title: str,
    ylabel: str,
    value_getter,
    std_getter,
    output_path: Path,
) -> None:
    included = [summary for summary in summaries if summary.include_in_main_charts]
    labels = [summary.display_label for summary in included]
    values = [value_getter(summary) for summary in included]
    errors = [std_getter(summary) for summary in included]

    figure, axis = plt.subplots(figsize=(max(8, len(labels) * 1.25), 5.5))
    axis.bar(range(len(labels)), values, yerr=errors, capsize=5, color="#5470c6")
    axis.set_title(title)
    axis.set_ylabel(ylabel)
    axis.set_xticks(range(len(labels)))
    axis.set_xticklabels(labels, rotation=25, ha="right")
    axis.grid(axis="y", alpha=0.3)
    figure.tight_layout()
    figure.savefig(output_path, dpi=200)
    plt.close(figure)


def _multi_page_summaries(summaries: list[RowSummary]) -> list[RowSummary]:
    return [
        summary
        for summary in summaries
        if summary.row_kind == "split_multi"
        and summary.target_page_count > 0
        and summary.page_group
        and summary.include_in_main_charts
    ]


def _plot_count_curve_metric(
    *,
    summaries: list[RowSummary],
    title: str,
    ylabel: str,
    value_getter,
    std_getter,
    output_path: Path,
) -> None:
    included = _multi_page_summaries(summaries)
    if not included:
        raise RuntimeError("No successful split_multi rows are available for count curves.")

    figure, axis = plt.subplots(figsize=(8.5, 5.5))
    colors = {"hot": "#c44e52", "random": "#55a868"}
    for page_group in sorted({summary.page_group for summary in included}):
        group_rows = sorted(
            [summary for summary in included if summary.page_group == page_group],
            key=lambda summary: summary.target_page_count,
        )
        counts = [summary.target_page_count for summary in group_rows]
        values = [value_getter(summary) for summary in group_rows]
        errors = [std_getter(summary) for summary in group_rows]
        axis.errorbar(
            counts,
            values,
            yerr=errors,
            marker="o",
            capsize=4,
            linewidth=2,
            label=page_group,
            color=colors.get(page_group),
        )

    axis.set_title(title)
    axis.set_xlabel("Broken pages")
    axis.set_ylabel(ylabel)
    axis.grid(alpha=0.3)
    axis.legend()
    figure.tight_layout()
    figure.savefig(output_path, dpi=200)
    plt.close(figure)


def _render_config_markdown(
    *,
    output_dir: Path,
    results_path: Path,
    metadata_path: Path,
    metadata: dict[str, object],
    summaries: list[RowSummary],
) -> Path:
    included = [summary for summary in summaries if summary.include_in_main_charts]
    omitted = [summary for summary in summaries if not summary.include_in_main_charts]
    config_path = output_dir / "config.md"
    lines = [
        "# GUPS Split Harness Config",
        "",
        "- baseline row: `base_pages`",
        "- baseline meaning: `THP never` with no page breaks",
        "- `no_break` meaning: `THP always` with no page breaks",
        "- split-row meaning: `THP always` with selected page breaks",
        "- runtime percent formula: `100 * ((runtime_s_i / base_pages_runtime_s_i) - 1)`",
        "- speedup percent formula: `100 * ((base_pages_runtime_s_i / runtime_s_i) - 1)`",
        "",
        f"- results parquet: `{results_path}`",
        f"- metadata json: `{metadata_path}`",
        f"- breakpoints parquet: `{metadata.get('breakpoints_path', '')}`",
        f"- artifacts dir: `{metadata.get('artifacts_dir', '')}`",
        f"- benchmark dir: `{metadata.get('benchmark_dir', '')}`",
        f"- gups binary: `{metadata.get('binary_path', '')}`",
        f"- commands log: `{metadata.get('commands_log_path', '')}`",
        f"- table_size_gib: `{metadata.get('table_size_gib', '')}`",
        f"- table_size_mib: `{metadata.get('table_size_mib', '')}`",
        f"- repeats: `{metadata.get('repeats', '')}`",
        f"- updates_multiplier: `{metadata.get('updates_multiplier', '')}`",
        f"- stream_seed: `{metadata.get('stream_seed', '')}`",
        f"- runs: `{metadata.get('runs', '')}`",
        f"- split_mode: `{metadata.get('split_mode', '')}`",
        f"- collectors: `{metadata.get('collectors', [])}`",
        "",
        "## Included Rows",
        "",
    ]
    for summary in included:
        lines.append(
            f"- `{summary.display_label}`: split_success_total={summary.split_success_total}, "
            f"split_expected_successes={summary.split_expected_successes}, "
            f"target_page_count={summary.target_page_count}, "
            f"page_group={summary.page_group}, "
            f"split_max_attempts_max={summary.split_max_attempts_max}, "
            f"runtime_pct_vs_base_pages_mean={summary.runtime_pct_vs_base_pages_mean:.3f}, "
            f"speedup_pct_vs_base_pages_mean={summary.speedup_pct_vs_base_pages_mean:.3f}"
        )
    lines.extend(["", "## Omitted Split-Only Rows", ""])
    if omitted:
        for summary in omitted:
            lines.append(
                f"- `{summary.display_label}`: split_success_total={summary.split_success_total}, "
                f"split_expected_successes={summary.split_expected_successes}, "
                f"target_page_count={summary.target_page_count}, "
                f"page_group={summary.page_group}, "
                f"split_max_attempts_max={summary.split_max_attempts_max}, "
                f"runtime_pct_vs_base_pages_mean={summary.runtime_pct_vs_base_pages_mean:.3f}, "
                f"speedup_pct_vs_base_pages_mean={summary.speedup_pct_vs_base_pages_mean:.3f}"
            )
    else:
        lines.append("- none")
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


def _render_html(
    *,
    output_dir: Path,
    label: str,
    metadata: dict[str, object],
    summaries: list[RowSummary],
    runtime_png: Path,
    runtime_pct_png: Path,
    speedup_pct_png: Path,
    gups_png: Path,
    dtlb_loads_png: Path | None,
    dtlb_misses_png: Path | None,
    count_curve_pngs: list[Path],
) -> Path:
    omitted = [summary for summary in summaries if not summary.include_in_main_charts]
    rows_html = "\n".join(
        [
            (
                "<tr>"
                f"<td>{summary.display_label}</td>"
                f"<td>{summary.runtime_mean:.6f} +/- {summary.runtime_std:.6f}</td>"
                f"<td>{summary.runtime_pct_vs_base_pages_mean:.3f} +/- {summary.runtime_pct_vs_base_pages_std:.3f}</td>"
                f"<td>{summary.speedup_pct_vs_base_pages_mean:.3f} +/- {summary.speedup_pct_vs_base_pages_std:.3f}</td>"
                f"<td>{summary.gups_mean:.6f} +/- {summary.gups_std:.6f}</td>"
                f"<td>{'' if summary.dtlb_loads_mean is None else f'{summary.dtlb_loads_mean:.3f} +/- {summary.dtlb_loads_std:.3f}'}</td>"
                f"<td>{'' if summary.dtlb_misses_mean is None else f'{summary.dtlb_misses_mean:.3f} +/- {summary.dtlb_misses_std:.3f}'}</td>"
                f"<td>{summary.split_success_total}</td>"
                f"<td>{summary.split_expected_successes}</td>"
                f"<td>{summary.split_max_attempts_max}</td>"
                "</tr>"
            )
            for summary in summaries
        ]
    )
    omitted_html = (
        "\n".join(
            [
                f"<li>{summary.display_label} (split_success_total={summary.split_success_total}, split_max_attempts_max={summary.split_max_attempts_max})</li>"
                for summary in omitted
            ]
        )
        if omitted
        else "<li>none</li>"
    )

    images_html = [
        f'<img src="{runtime_png.name}" alt="runtime graph" style="max-width: 100%;">',
        f'<img src="{runtime_pct_png.name}" alt="runtime percent change graph" style="max-width: 100%;">',
        f'<img src="{speedup_pct_png.name}" alt="speedup percent graph" style="max-width: 100%;">',
        f'<img src="{gups_png.name}" alt="gups graph" style="max-width: 100%;">',
    ]
    if dtlb_loads_png is not None:
        images_html.append(
            f'<img src="{dtlb_loads_png.name}" alt="dtlb loads graph" style="max-width: 100%;">'
        )
    if dtlb_misses_png is not None:
        images_html.append(
            f'<img src="{dtlb_misses_png.name}" alt="dtlb misses graph" style="max-width: 100%;">'
        )
    count_curves_html = [
        f'<img src="{path.name}" alt="{path.stem}" style="max-width: 100%;">'
        for path in count_curve_pngs
    ]

    html_path = output_dir / f"gups_split_harness_{label}.html"
    html_path.write_text(
        "\n".join(
            [
                "<!doctype html>",
                "<html><head><meta charset='utf-8'><title>GUPS Split Harness</title></head><body>",
                f"<h1>GUPS Split Harness: {label}</h1>",
                "<h2>Inputs</h2>",
                "<ul>",
                f"<li>Results parquet: {metadata.get('output', '')}</li>",
                f"<li>Breakpoints parquet: {metadata.get('breakpoints_path', '')}</li>",
                f"<li>Commands log: {metadata.get('commands_log_path', '')}</li>",
                f"<li>Artifacts dir: {metadata.get('artifacts_dir', '')}</li>",
                "</ul>",
                "<h2>Row Meanings</h2>",
                "<ul>",
                "<li><code>base_pages</code> means THP never with no page breaks.</li>",
                "<li><code>no_break</code> means THP always with no page breaks.</li>",
                "<li>Split rows mean THP always with selected page breaks.</li>",
                "<li>The normalization baseline is always <code>base_pages</code>.</li>",
                "<li>Runtime percent formula: <code>100 * ((runtime_s_i / base_pages_runtime_s_i) - 1)</code></li>",
                "<li>Speedup percent formula: <code>100 * ((base_pages_runtime_s_i / runtime_s_i) - 1)</code></li>",
                "</ul>",
                "<h2>Main Graphs</h2>",
                *images_html,
                "<h2>Broken Page Count Curves</h2>",
                *(count_curves_html or ["<p>No successful multi-page split rows found.</p>"]),
                "<h2>Row Summary</h2>",
                "<table border='1' cellspacing='0' cellpadding='4'>",
                "<tr><th>Row</th><th>Runtime</th><th>Runtime % vs base_pages</th><th>Speedup % vs base_pages</th><th>GUP/s</th><th>dTLB loads</th><th>dTLB misses</th><th>Split successes</th><th>Expected successes</th><th>Max split attempts</th></tr>",
                rows_html,
                "</table>",
                "<h2>Omitted Split-Only Rows</h2>",
                f"<ul>{omitted_html}</ul>",
                "</body></html>",
            ]
        ),
        encoding="utf-8",
    )
    return html_path


def render_dashboard(
    *,
    results_path: Path,
    metadata_path: Path,
    output_dir: Path,
    label: str,
) -> dict[str, Path]:
    """Render PNGs, config.md, and HTML for one GUPS split-harness result set."""
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    summaries = load_row_summaries(results_path)

    runtime_png = output_dir / "runtime_mean_std.png"
    runtime_pct_png = output_dir / "runtime_pct_vs_base_pages.png"
    speedup_pct_png = output_dir / "speedup_pct_vs_base_pages.png"
    gups_png = output_dir / "gups_mean_std.png"
    _plot_bar_metric(
        summaries=summaries,
        title="GUPS Mean Runtime By Row",
        ylabel="Runtime (s)",
        value_getter=lambda summary: summary.runtime_mean,
        std_getter=lambda summary: summary.runtime_std,
        output_path=runtime_png,
    )
    _plot_bar_metric(
        summaries=summaries,
        title="Runtime Percent Change Vs base_pages",
        ylabel="Runtime % vs base_pages",
        value_getter=lambda summary: summary.runtime_pct_vs_base_pages_mean,
        std_getter=lambda summary: summary.runtime_pct_vs_base_pages_std,
        output_path=runtime_pct_png,
    )
    _plot_bar_metric(
        summaries=summaries,
        title="Speedup Percent Vs base_pages",
        ylabel="Speedup % vs base_pages",
        value_getter=lambda summary: summary.speedup_pct_vs_base_pages_mean,
        std_getter=lambda summary: summary.speedup_pct_vs_base_pages_std,
        output_path=speedup_pct_png,
    )
    _plot_bar_metric(
        summaries=summaries,
        title="GUPS Mean Throughput By Row",
        ylabel="GUP/s",
        value_getter=lambda summary: summary.gups_mean,
        std_getter=lambda summary: summary.gups_std,
        output_path=gups_png,
    )

    dtlb_loads_png: Path | None = None
    if any(summary.dtlb_loads_mean is not None for summary in summaries):
        dtlb_loads_png = output_dir / "dtlb_loads_mean_std.png"
        _plot_bar_metric(
            summaries=summaries,
            title="dTLB Loads By Row",
            ylabel="dTLB loads",
            value_getter=lambda summary: 0.0
            if summary.dtlb_loads_mean is None
            else summary.dtlb_loads_mean,
            std_getter=lambda summary: 0.0
            if summary.dtlb_loads_std is None
            else summary.dtlb_loads_std,
            output_path=dtlb_loads_png,
        )

    dtlb_misses_png: Path | None = None
    if any(summary.dtlb_misses_mean is not None for summary in summaries):
        dtlb_misses_png = output_dir / "dtlb_misses_mean_std.png"
        _plot_bar_metric(
            summaries=summaries,
            title="dTLB Misses By Row",
            ylabel="dTLB misses",
            value_getter=lambda summary: 0.0
            if summary.dtlb_misses_mean is None
            else summary.dtlb_misses_mean,
            std_getter=lambda summary: 0.0
            if summary.dtlb_misses_std is None
            else summary.dtlb_misses_std,
            output_path=dtlb_misses_png,
        )

    count_curve_pngs: list[Path] = []
    if _multi_page_summaries(summaries):
        runtime_by_count_png = output_dir / "runtime_by_broken_page_count.png"
        _plot_count_curve_metric(
            summaries=summaries,
            title="Runtime By Broken Page Count",
            ylabel="Runtime (s)",
            value_getter=lambda summary: summary.runtime_mean,
            std_getter=lambda summary: summary.runtime_std,
            output_path=runtime_by_count_png,
        )
        count_curve_pngs.append(runtime_by_count_png)

        runtime_pct_by_count_png = (
            output_dir / "runtime_pct_vs_base_pages_by_broken_page_count.png"
        )
        _plot_count_curve_metric(
            summaries=summaries,
            title="Runtime Percent Change Vs base_pages By Broken Page Count",
            ylabel="Runtime % vs base_pages",
            value_getter=lambda summary: summary.runtime_pct_vs_base_pages_mean,
            std_getter=lambda summary: summary.runtime_pct_vs_base_pages_std,
            output_path=runtime_pct_by_count_png,
        )
        count_curve_pngs.append(runtime_pct_by_count_png)

        speedup_pct_by_count_png = (
            output_dir / "speedup_pct_vs_base_pages_by_broken_page_count.png"
        )
        _plot_count_curve_metric(
            summaries=summaries,
            title="Speedup Percent Vs base_pages By Broken Page Count",
            ylabel="Speedup % vs base_pages",
            value_getter=lambda summary: summary.speedup_pct_vs_base_pages_mean,
            std_getter=lambda summary: summary.speedup_pct_vs_base_pages_std,
            output_path=speedup_pct_by_count_png,
        )
        count_curve_pngs.append(speedup_pct_by_count_png)

        gups_by_count_png = output_dir / "gups_by_broken_page_count.png"
        _plot_count_curve_metric(
            summaries=summaries,
            title="GUPS By Broken Page Count",
            ylabel="GUP/s",
            value_getter=lambda summary: summary.gups_mean,
            std_getter=lambda summary: summary.gups_std,
            output_path=gups_by_count_png,
        )
        count_curve_pngs.append(gups_by_count_png)

    config_md = _render_config_markdown(
        output_dir=output_dir,
        results_path=results_path,
        metadata_path=metadata_path,
        metadata=metadata,
        summaries=summaries,
    )
    html_path = _render_html(
        output_dir=output_dir,
        label=label,
        metadata=metadata,
        summaries=summaries,
        runtime_png=runtime_png,
        runtime_pct_png=runtime_pct_png,
        speedup_pct_png=speedup_pct_png,
        gups_png=gups_png,
        dtlb_loads_png=dtlb_loads_png,
        dtlb_misses_png=dtlb_misses_png,
        count_curve_pngs=count_curve_pngs,
    )
    return {
        "runtime_png": runtime_png,
        "runtime_pct_png": runtime_pct_png,
        "speedup_pct_png": speedup_pct_png,
        "gups_png": gups_png,
        "dtlb_loads_png": dtlb_loads_png if dtlb_loads_png is not None else Path(),
        "dtlb_misses_png": dtlb_misses_png if dtlb_misses_png is not None else Path(),
        "count_curve_pngs": count_curve_pngs,
        "config_md": config_md,
        "html": html_path,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a deterministic GUPS split-harness dashboard."
    )
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--label", type=str, required=True)
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    render_dashboard(
        results_path=args.results,
        metadata_path=args.metadata,
        output_dir=args.output_dir,
        label=args.label,
    )


if __name__ == "__main__":
    main()
