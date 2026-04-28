"""Analyze the 3-minute 22-row dTLB replay and render TLB companion artifacts.

This script reads ``data/replay_3min_dtlb_results_22rows.parquet`` and writes:

- ``temp_data_analysis/replay_3min_22rows_tlb_overview.png``
- ``temp_data_analysis/replay_3min_22rows_tlb_metrics.json``
- ``temp_data_analysis/replay_3min_22rows_tlb_rows.json``
- ``temp_data_analysis/replay_3min_22rows_tlb_summary.md``
- ``temp_data_analysis/replay_3min_22rows_tlb_dashboard.html``

The dashboard is intentionally self-contained: every runtime number and every
TLB counter on the page comes from the same dTLB-instrumented parquet.
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
DTLB_RESULTS_PATH = REPO_ROOT / "data" / "replay_3min_dtlb_results_22rows.parquet"
TRACE_PATH = REPO_ROOT / "data" / "redis_traces" / "replay_3min_22rows_monitor_run.log"
OUTPUT_DIR = REPO_ROOT / "temp_data_analysis"
GRAPH_PATH = OUTPUT_DIR / "replay_3min_22rows_tlb_overview.png"
METRICS_PATH = OUTPUT_DIR / "replay_3min_22rows_tlb_metrics.json"
ROWS_PATH = OUTPUT_DIR / "replay_3min_22rows_tlb_rows.json"
SUMMARY_PATH = OUTPUT_DIR / "replay_3min_22rows_tlb_summary.md"
DASHBOARD_PATH = OUTPUT_DIR / "replay_3min_22rows_tlb_dashboard.html"
LEGACY_GRAPH_PATHS = [OUTPUT_DIR / "replay_3min_22rows_runtime_dtlb.png"]
LEGACY_METRICS_PATHS = [OUTPUT_DIR / "replay_3min_22rows_metrics.json"]
LEGACY_ROWS_PATHS = [OUTPUT_DIR / "replay_3min_22rows_rows.json"]
LEGACY_SUMMARY_PATHS = [OUTPUT_DIR / "replay_3min_22rows_summary.md"]
LEGACY_DASHBOARD_PATHS = [OUTPUT_DIR / "replay_3min_22rows_dtlb_dashboard.html"]
GENERATE_BREAKPOINTS_PATH = (
    REPO_ROOT / "python" / "kernmlops" / "replay" / "generate_breakpoints.py"
)
HOT_KEY_ROWS = 17


def _write_text_with_aliases(
    primary_path: Path,
    alias_paths: list[Path],
    text: str,
) -> None:
    """Write one text payload to the canonical path and compatibility aliases."""

    primary_path.write_text(text, encoding="utf-8")
    for alias_path in alias_paths:
        alias_path.write_text(text, encoding="utf-8")


def _load_access_counts() -> dict[str, int]:
    """Return per-key access counts for the accepted 3-minute replay trace.

    Returns:
        A ``{key: access_count}`` mapping reconstructed from the accepted
        monitor log.

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


def _runtime_mean(row: dict[str, object], runtime_cols: list[str]) -> float:
    """Return the arithmetic mean for one set of runtime samples."""

    values = [float(row[column]) for column in runtime_cols]
    return sum(values) / len(values)


def _runtime_std(values: list[float]) -> float:
    """Return the sample standard deviation for one list of values."""

    return statistics.stdev(values) if len(values) > 1 else 0.0


def _classify_rows(dtlb_df: pl.DataFrame) -> list[dict[str, object]]:
    """Attach labels and stats to each dTLB replay row.

    Args:
        dtlb_df: dTLB replay parquet loaded into memory.

    Returns:
        One record per replay row. Example output:
            {
                "row_type": "hot_split_only",
                "short_label": "hot#4",
                "runtime_mean": 172.4,
                "dtlb_loads_mean": 1.2e8,
            }

    """

    access_counts = _load_access_counts()
    ordered_keys = [
        key
        for key, _count in sorted(
            access_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]
    hot_key_set = set(ordered_keys[:HOT_KEY_ROWS])

    runtime_cols = [column for column in dtlb_df.columns if column.startswith("runtime_s_")]
    dtlb_load_cols = [column for column in dtlb_df.columns if column.startswith("dtlb_loads_")]
    dtlb_miss_cols = [column for column in dtlb_df.columns if column.startswith("dtlb_misses_")]
    breakpoint_cols = [
        column
        for column in dtlb_df.columns
        if column not in runtime_cols
        and column not in dtlb_load_cols
        and column not in dtlb_miss_cols
    ]

    raw_records: list[dict[str, object]] = []
    for dtlb_row in dtlb_df.iter_rows(named=True):
        breakpoints = {key: int(dtlb_row[key]) for key in breakpoint_cols}
        zero_keys = [key for key, value in breakpoints.items() if value == 0]

        runtime_samples = [float(dtlb_row[column]) for column in runtime_cols]
        dtlb_load_samples = [float(dtlb_row[column]) for column in dtlb_load_cols]
        dtlb_miss_samples = [float(dtlb_row[column]) for column in dtlb_miss_cols]

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
                "row_type": row_type,
                "key": key,
                "access_count": access_counts.get(key, 0) if key is not None else None,
                "runtime_samples": runtime_samples,
                "runtime_mean": _runtime_mean(dtlb_row, runtime_cols),
                "runtime_std": _runtime_std(runtime_samples),
                "dtlb_loads_samples": dtlb_load_samples,
                "dtlb_loads_mean": sum(dtlb_load_samples) / len(dtlb_load_samples),
                "dtlb_loads_std": _runtime_std(dtlb_load_samples),
                "dtlb_misses_samples": dtlb_miss_samples,
                "dtlb_misses_mean": sum(dtlb_miss_samples) / len(dtlb_miss_samples),
                "dtlb_misses_std": _runtime_std(dtlb_miss_samples),
            }
        )

    runtime_no_break = next(
        float(record["runtime_mean"])
        for record in raw_records
        if record["row_type"] == "no_break"
    )
    dtlb_no_break_loads = next(
        float(record["dtlb_loads_mean"])
        for record in raw_records
        if record["row_type"] == "no_break"
    )
    dtlb_no_break_misses = next(
        float(record["dtlb_misses_mean"])
        for record in raw_records
        if record["row_type"] == "no_break"
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
        record["hot_rank"] = rank
        record["random_rank"] = None
        record["short_label"] = f"hot#{rank}"
    for rank, record in enumerate(random_rows, start=1):
        record["hot_rank"] = None
        record["random_rank"] = rank
        record["short_label"] = f"rand#{rank}"

    ordered_records: list[dict[str, object]] = []
    for row_type in ("base_pages", "no_break"):
        record = next(record for record in raw_records if record["row_type"] == row_type)
        record["hot_rank"] = None
        record["random_rank"] = None
        record["short_label"] = row_type
        ordered_records.append(record)

    ordered_records.extend(hot_rows)
    ordered_records.extend(random_rows)

    for record in ordered_records:
        record["runtime_delta_vs_no_break"] = float(record["runtime_mean"]) - runtime_no_break
        record["runtime_pct_vs_no_break"] = (
            float(record["runtime_mean"]) / runtime_no_break - 1
        ) * 100
        record["dtlb_loads_delta_vs_no_break"] = (
            float(record["dtlb_loads_mean"]) - dtlb_no_break_loads
        )
        record["dtlb_loads_pct_vs_no_break"] = (
            float(record["dtlb_loads_mean"]) / dtlb_no_break_loads - 1
        ) * 100
        record["dtlb_misses_delta_vs_no_break"] = (
            float(record["dtlb_misses_mean"]) - dtlb_no_break_misses
        )
        record["dtlb_misses_pct_vs_no_break"] = (
            float(record["dtlb_misses_mean"]) / dtlb_no_break_misses - 1
        ) * 100

    return ordered_records


def _write_metrics(records: list[dict[str, object]]) -> dict[str, object]:
    """Write a compact JSON summary for the 22-row dTLB experiment."""

    base_pages = next(record for record in records if record["row_type"] == "base_pages")
    no_break = next(record for record in records if record["row_type"] == "no_break")
    hot_rows = [record for record in records if record["row_type"] == "hot_split_only"]
    random_rows = [record for record in records if record["row_type"] == "random_split_only"]

    all_runtime_samples = [
        float(sample)
        for record in records
        for sample in list(record["runtime_samples"])
    ]
    metrics = {
        "results_path": str(DTLB_RESULTS_PATH),
        "rows": len(records),
        "hot_rows": len(hot_rows),
        "random_rows": len(random_rows),
        "base_pages_runtime_s": float(base_pages["runtime_mean"]),
        "no_break_runtime_s": float(no_break["runtime_mean"]),
        "base_pages_pct_vs_no_break": float(base_pages["runtime_pct_vs_no_break"]),
        "base_pages_dtlb_loads": float(base_pages["dtlb_loads_mean"]),
        "no_break_dtlb_loads": float(no_break["dtlb_loads_mean"]),
        "base_pages_dtlb_loads_pct_vs_no_break": float(
            base_pages["dtlb_loads_pct_vs_no_break"]
        ),
        "base_pages_dtlb_misses": float(base_pages["dtlb_misses_mean"]),
        "no_break_dtlb_misses": float(no_break["dtlb_misses_mean"]),
        "base_pages_dtlb_misses_pct_vs_no_break": float(
            base_pages["dtlb_misses_pct_vs_no_break"]
        ),
        "hot_split_mean_runtime_pct": sum(
            float(record["runtime_pct_vs_no_break"]) for record in hot_rows
        )
        / len(hot_rows),
        "random_split_mean_runtime_pct": sum(
            float(record["runtime_pct_vs_no_break"]) for record in random_rows
        )
        / len(random_rows),
        "hot_split_mean_loads_pct": sum(
            float(record["dtlb_loads_pct_vs_no_break"]) for record in hot_rows
        )
        / len(hot_rows),
        "random_split_mean_loads_pct": sum(
            float(record["dtlb_loads_pct_vs_no_break"]) for record in random_rows
        )
        / len(random_rows),
        "hot_split_mean_misses_pct": sum(
            float(record["dtlb_misses_pct_vs_no_break"]) for record in hot_rows
        )
        / len(hot_rows),
        "random_split_mean_misses_pct": sum(
            float(record["dtlb_misses_pct_vs_no_break"]) for record in random_rows
        )
        / len(random_rows),
        "runtime_min_s": min(all_runtime_samples),
        "runtime_max_s": max(all_runtime_samples),
    }

    metrics_text = json.dumps(metrics, indent=2) + "\n"
    _write_text_with_aliases(METRICS_PATH, LEGACY_METRICS_PATHS, metrics_text)
    return metrics


def _write_rows(records: list[dict[str, object]]) -> None:
    """Write row-level JSON consumed by the dashboard."""

    rows_text = json.dumps(records, indent=2) + "\n"
    _write_text_with_aliases(ROWS_PATH, LEGACY_ROWS_PATHS, rows_text)


def _replace_inline_json_script(html: str, script_id: str, payload: object) -> str:
    """Replace one embedded JSON block inside the dashboard HTML.

    Args:
        html: Dashboard HTML source containing the preview-data placeholder.
        script_id: DOM id for the inline JSON block to replace.
        payload: JSON-serializable object to embed.

    Returns:
        Updated HTML with pretty-printed inline JSON.

    Raises:
        RuntimeError: If the expected inline JSON block is missing.
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
            f"Could not find inline JSON block '{script_id}' in dashboard template"
        )
    return updated_html


def _build_dashboard_html() -> str:
    """Return the standalone HTML dashboard template for the TLB run."""

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Replay 3-Minute 22-Row Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #08131d;
    --bg-accent: #102536;
    --card: rgba(9, 22, 33, 0.88);
    --border: rgba(145, 196, 255, 0.16);
    --text: #eef7ff;
    --muted: #a7b8cc;
    --blue: #75aefc;
    --teal: #58c5a9;
    --gold: #f0b561;
    --red: #ef6b73;
    --shadow: 0 22px 56px rgba(0, 0, 0, 0.28);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    color: var(--text);
    font-family: "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    background:
      radial-gradient(circle at top left, rgba(117, 174, 252, 0.16), transparent 28%),
      radial-gradient(circle at top right, rgba(88, 197, 169, 0.14), transparent 26%),
      linear-gradient(180deg, #0b1622 0%, #09111b 55%, #070d15 100%);
    padding: 28px 20px 40px;
  }
  .page {
    max-width: 1440px;
    margin: 0 auto;
  }
  header { margin-bottom: 24px; }
  .eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 7px 12px;
    border-radius: 999px;
    background: rgba(117, 174, 252, 0.12);
    border: 1px solid rgba(117, 174, 252, 0.18);
    color: #d4e6ff;
    font-size: 0.82rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  header h1 {
    margin: 14px 0 10px;
    font-size: clamp(2rem, 4vw, 3.2rem);
    line-height: 1.05;
    letter-spacing: -0.03em;
  }
  header p {
    margin: 0;
    max-width: 980px;
    color: var(--muted);
    font-size: 1rem;
    line-height: 1.65;
  }
  .notice {
    margin-top: 14px;
    display: inline-block;
    padding: 10px 12px;
    border-radius: 12px;
    background: rgba(240, 181, 97, 0.10);
    border: 1px solid rgba(240, 181, 97, 0.17);
    color: #ffe7c2;
    font-size: 0.88rem;
  }
  .stats {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 16px;
    margin-bottom: 18px;
  }
  .stat {
    background: linear-gradient(180deg, rgba(16, 37, 54, 0.95), rgba(9, 22, 33, 0.95));
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 18px;
    box-shadow: var(--shadow);
  }
  .stat .label {
    color: var(--muted);
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .stat .value {
    margin-top: 10px;
    font-size: clamp(1.6rem, 3vw, 2.4rem);
    line-height: 1;
    font-weight: 700;
    letter-spacing: -0.04em;
  }
  .stat .detail {
    margin-top: 10px;
    color: var(--muted);
    font-size: 0.92rem;
    line-height: 1.45;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(12, minmax(0, 1fr));
    gap: 18px;
  }
  .card {
    grid-column: span 6;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 22px;
    padding: 20px 20px 18px;
    backdrop-filter: blur(16px);
    box-shadow: var(--shadow);
  }
  .card.wide { grid-column: 1 / -1; }
  .card h2 {
    margin: 0 0 8px;
    font-size: 1.18rem;
    line-height: 1.25;
  }
  .card .desc {
    margin: 0 0 16px;
    color: var(--muted);
    font-size: 0.93rem;
    line-height: 1.58;
  }
  .chart-wrap {
    position: relative;
    height: 360px;
  }
  .chart-wrap.tall { height: 460px; }
  canvas {
    width: 100% !important;
    height: 100% !important;
  }
  .story-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
    margin-top: 4px;
  }
  .story {
    background: rgba(16, 37, 54, 0.72);
    border: 1px solid rgba(145, 196, 255, 0.12);
    border-radius: 16px;
    padding: 14px 16px;
  }
  .story strong {
    display: block;
    margin-bottom: 6px;
    color: #ffffff;
    font-size: 0.96rem;
  }
  .story p {
    margin: 0;
    color: var(--muted);
    font-size: 0.9rem;
    line-height: 1.5;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.92rem;
  }
  th, td {
    padding: 12px 10px;
    border-bottom: 1px solid rgba(145, 196, 255, 0.10);
    vertical-align: top;
    text-align: left;
  }
  th {
    color: #d8e6ff;
    font-size: 0.84rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  td { color: var(--muted); }
  tbody tr:hover { background: rgba(117, 174, 252, 0.05); }
  .mono {
    font-family: "SFMono-Regular", ui-monospace, "Cascadia Code", Consolas, monospace;
    font-size: 0.84rem;
    color: #dfe9ff;
    word-break: break-all;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.02em;
  }
  .chip.red { color: #ffdbe0; background: rgba(239, 107, 115, 0.14); }
  .chip.teal { color: #d4fff1; background: rgba(88, 197, 169, 0.14); }
  .chip.gold { color: #ffeccc; background: rgba(240, 181, 97, 0.14); }
  @media (max-width: 1100px) {
    .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .card { grid-column: 1 / -1; }
  }
  @media (max-width: 720px) {
    body { padding: 20px 14px 32px; }
    .stats { grid-template-columns: 1fr; }
    .story-grid { grid-template-columns: 1fr; }
    .chart-wrap, .chart-wrap.tall { height: 320px; }
    table, thead, tbody, tr, td, th { display: block; }
    thead { display: none; }
    tr {
      padding: 14px 0;
      border-bottom: 1px solid rgba(145, 196, 255, 0.10);
    }
    td {
      border: none;
      padding: 6px 0;
    }
    td::before {
      content: attr(data-label);
      display: block;
      color: #d8e6ff;
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 4px;
    }
  }
</style>
</head>
<body>
<div class="page">
  <header>
    <div class="eyebrow">Redis Replay · 3-Minute Target · 22 Rows</div>
    <h1>TLB-instrumented view of the longer replay pass</h1>
    <p>
      This dashboard pairs the runtime-only replay results from
      <code>data/replay_3min_results_22rows.parquet</code> with the
      dTLB-instrumented replay results from
      <code>data/replay_3min_dtlb_results_22rows.parquet</code>. The first row
      is the THP-disabled <code>base_pages</code> baseline, the second row is
      intact-THP <code>no_break</code>, the next 17 rows split the hottest keys
      one at a time, and the final 3 rows split randomly chosen non-hot keys
      one at a time.
    </p>
    <div class="notice">
      Every chart, card, and table value on this page comes from the same
      dTLB-instrumented parquet. Runtime values here are the instrumented-run
      runtimes, so they will differ from the separate runtime dashboard.
    </div>
  </header>

  <section class="stats">
    <article class="stat">
      <div class="label">base_pages runtime</div>
      <div class="value" id="basePagesRuntimeValue">--</div>
      <div class="detail" id="basePagesRuntimeDetail">--</div>
    </article>
    <article class="stat">
      <div class="label">Hot split mean</div>
      <div class="value" id="hotSplitValue">--</div>
      <div class="detail" id="hotSplitDetail">--</div>
    </article>
    <article class="stat">
      <div class="label">Random split mean</div>
      <div class="value" id="randomSplitValue">--</div>
      <div class="detail" id="randomSplitDetail">--</div>
    </article>
    <article class="stat">
      <div class="label">base_pages dTLB misses</div>
      <div class="value" id="basePagesMissValue">--</div>
      <div class="detail" id="basePagesMissDetail">--</div>
    </article>
  </section>

  <section class="grid">
    <article class="card">
      <h2>Runtime delta vs no_break</h2>
      <p class="desc">
        This chart uses the dTLB-instrumented runtime columns to show which rows
        were slower or faster than intact THP in the same hardware run.
      </p>
      <div class="chart-wrap tall">
        <canvas id="runtimeChart"></canvas>
      </div>
    </article>

    <article class="card">
      <h2>dTLB delta vs no_break</h2>
      <p class="desc">
        The hardware round only differs by the dTLB counters, so these bars show
        how loads and misses move relative to the matched <code>no_break</code>
        row.
      </p>
      <div class="chart-wrap">
        <canvas id="dtlbLoadsChart"></canvas>
      </div>
      <div class="chart-wrap" style="margin-top: 18px;">
        <canvas id="dtlbMissesChart"></canvas>
      </div>
    </article>

    <article class="card wide">
      <h2>Important insights</h2>
      <p class="desc">
        These callouts summarize the runtime and dTLB patterns from the single
        dTLB-instrumented rerun.
      </p>
      <div class="story-grid" id="storyGrid"></div>
    </article>

    <article class="card wide">
      <h2>All 22 rows</h2>
      <p class="desc">
        The table below keeps the full row set visible: baselines, the 17 hot
        split-only rows, and the 3 random split-only rows.
      </p>
      <table>
        <thead>
          <tr>
            <th>Label</th>
            <th>Row type</th>
            <th>Key</th>
            <th>Accesses</th>
            <th>Runtime mean</th>
            <th>Runtime delta</th>
            <th>dTLB loads mean</th>
            <th>dTLB misses mean</th>
          </tr>
        </thead>
        <tbody id="rowsTable"></tbody>
      </table>
    </article>
  </section>
</div>

<script id="replay3minMetricsData" type="application/json">
{}
</script>
<script id="replay3minRowsData" type="application/json">
[]
</script>
<script>
const metrics = JSON.parse(document.getElementById('replay3minMetricsData').textContent);
const rows = JSON.parse(document.getElementById('replay3minRowsData').textContent);

function formatSeconds(value) {
  return `${value.toFixed(3)}s`;
}

function formatPercent(value) {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

function formatMillions(value) {
  return `${(value / 1_000_000).toFixed(2)}M`;
}

function rowColor(row) {
  if (row.row_type === 'base_pages') return 'rgba(239, 107, 115, 0.75)';
  if (row.row_type === 'no_break') return 'rgba(151, 164, 176, 0.80)';
  if (row.row_type === 'hot_split_only') return 'rgba(240, 181, 97, 0.78)';
  return 'rgba(88, 197, 169, 0.78)';
}

document.getElementById('basePagesRuntimeValue').textContent = formatSeconds(metrics.base_pages_runtime_s);
document.getElementById('basePagesRuntimeDetail').innerHTML =
  `<span class="chip ${metrics.base_pages_pct_vs_no_break > 0 ? 'red' : 'teal'}">${formatPercent(metrics.base_pages_pct_vs_no_break)}</span> versus <code>no_break</code>`;

document.getElementById('hotSplitValue').textContent = formatPercent(metrics.hot_split_mean_runtime_pct);
document.getElementById('hotSplitDetail').textContent =
  `Mean runtime delta across ${metrics.hot_rows} hottest split-only rows.`;

document.getElementById('randomSplitValue').textContent = formatPercent(metrics.random_split_mean_runtime_pct);
document.getElementById('randomSplitDetail').textContent =
  `Mean runtime delta across ${metrics.random_rows} random split-only rows.`;

document.getElementById('basePagesMissValue').textContent = formatMillions(metrics.base_pages_dtlb_misses);
document.getElementById('basePagesMissDetail').innerHTML =
  `<span class="chip ${metrics.base_pages_dtlb_misses_pct_vs_no_break > 0 ? 'red' : 'teal'}">${formatPercent(metrics.base_pages_dtlb_misses_pct_vs_no_break)}</span> versus <code>no_break</code>`;

const runtimeLabels = rows.map((row) => row.short_label);
const runtimeDeltas = rows.map((row) => row.runtime_pct_vs_no_break);
const loadDeltas = rows.map((row) => row.dtlb_loads_pct_vs_no_break);
const missDeltas = rows.map((row) => row.dtlb_misses_pct_vs_no_break);

new Chart(document.getElementById('runtimeChart'), {
  type: 'bar',
  data: {
      labels: runtimeLabels,
      datasets: [{
        label: 'Runtime delta vs no_break (%)',
      data: runtimeDeltas,
      backgroundColor: rows.map(rowColor),
      borderColor: 'rgba(255,255,255,0.18)',
      borderWidth: 1.2,
      borderRadius: 6,
    }],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (ctx) => `${formatPercent(ctx.parsed.y)}`
        }
      }
    },
    scales: {
      x: {
        ticks: { color: '#dce9ff', maxRotation: 70, minRotation: 45 },
        grid: { display: false }
      },
      y: {
        ticks: {
          color: '#dce9ff',
          callback: (value) => `${value}%`
        },
        grid: { color: 'rgba(255,255,255,0.08)' }
      }
    }
  }
});

function dtlbChart(canvasId, label, values, color) {
  return new Chart(document.getElementById(canvasId), {
    type: 'bar',
    data: {
      labels: runtimeLabels,
      datasets: [{
        label,
        data: values,
        backgroundColor: color,
        borderColor: 'rgba(255,255,255,0.16)',
        borderWidth: 1.1,
        borderRadius: 6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => `${formatPercent(ctx.parsed.y)}`
          }
        }
      },
      scales: {
        x: {
          ticks: { color: '#dce9ff', maxRotation: 70, minRotation: 45 },
          grid: { display: false }
        },
        y: {
          ticks: {
            color: '#dce9ff',
            callback: (value) => `${value}%`
          },
          grid: { color: 'rgba(255,255,255,0.08)' }
        }
      }
    }
  });
}

dtlbChart('dtlbLoadsChart', 'dTLB loads delta vs no_break (%)', loadDeltas, 'rgba(117, 174, 252, 0.76)');
dtlbChart('dtlbMissesChart', 'dTLB misses delta vs no_break (%)', missDeltas, 'rgba(88, 197, 169, 0.76)');

const stories = [
  {
    title: 'base_pages runtime gap',
    body: `base_pages is ${formatPercent(metrics.base_pages_pct_vs_no_break)} versus no_break in the dTLB-instrumented run.`
  },
  {
    title: 'Hot vs random split rows',
    body: `The 17 hot rows averaged ${formatPercent(metrics.hot_split_mean_runtime_pct)}, while the 3 random rows averaged ${formatPercent(metrics.random_split_mean_runtime_pct)} versus no_break.`
  },
  {
    title: 'dTLB load shift',
    body: `base_pages loads are ${formatPercent(metrics.base_pages_dtlb_loads_pct_vs_no_break)} versus no_break in the hardware round.`
  },
  {
    title: 'dTLB miss shift',
    body: `base_pages misses are ${formatPercent(metrics.base_pages_dtlb_misses_pct_vs_no_break)} versus no_break in the hardware round.`
  }
];

const storyGrid = document.getElementById('storyGrid');
for (const story of stories) {
  const article = document.createElement('article');
  article.className = 'story';
  article.innerHTML = `<strong>${story.title}</strong><p>${story.body}</p>`;
  storyGrid.appendChild(article);
}

const rowsTable = document.getElementById('rowsTable');
for (const row of rows) {
  const tr = document.createElement('tr');
  const keyText = row.key ?? '—';
  const accessText = row.access_count == null ? '—' : row.access_count.toLocaleString();
  const typeText = row.row_type === 'hot_split_only'
    ? 'hot split'
    : row.row_type === 'random_split_only'
      ? 'random split'
      : row.row_type;

  tr.innerHTML = `
    <td data-label="Label"><span class="chip ${row.row_type === 'base_pages' ? 'red' : row.row_type === 'random_split_only' ? 'teal' : 'gold'}">${row.short_label}</span></td>
    <td data-label="Row type">${typeText}</td>
    <td data-label="Key" class="mono">${keyText}</td>
    <td data-label="Accesses">${accessText}</td>
    <td data-label="Runtime mean">${formatSeconds(row.runtime_mean)}</td>
    <td data-label="Runtime delta">${formatPercent(row.runtime_pct_vs_no_break)}</td>
    <td data-label="dTLB loads mean">${formatMillions(row.dtlb_loads_mean)}</td>
    <td data-label="dTLB misses mean">${formatMillions(row.dtlb_misses_mean)}</td>
  `;
  rowsTable.appendChild(tr);
}
</script>
</body>
</html>
"""


def _write_dashboard_preview(
    metrics: dict[str, object],
    records: list[dict[str, object]],
) -> None:
    """Write the standalone HTML dashboard with embedded preview data."""

    html = _build_dashboard_html()
    html = _replace_inline_json_script(html, "replay3minMetricsData", metrics)
    html = _replace_inline_json_script(html, "replay3minRowsData", records)
    _write_text_with_aliases(DASHBOARD_PATH, LEGACY_DASHBOARD_PATHS, html)


def _write_summary(records: list[dict[str, object]], metrics: dict[str, object]) -> None:
    """Write a short markdown summary for the dTLB-instrumented longer replay."""

    hot_rows = [record for record in records if record["row_type"] == "hot_split_only"]
    random_rows = [record for record in records if record["row_type"] == "random_split_only"]
    slowest_runtime_rows = sorted(
        (record for record in hot_rows + random_rows),
        key=lambda record: float(record["runtime_pct_vs_no_break"]),
        reverse=True,
    )[:5]
    highest_miss_rows = sorted(
        (record for record in hot_rows + random_rows),
        key=lambda record: float(record["dtlb_misses_pct_vs_no_break"]),
        reverse=True,
    )[:5]

    lines = [
        "# Replay 3-Minute 22-Row TLB Summary",
        "",
        f"Source parquet: `{DTLB_RESULTS_PATH.relative_to(REPO_ROOT)}`",
        "",
        "## Important Takeaways",
        "",
        f"- `base_pages` averaged `{metrics['base_pages_runtime_s']:.3f}s` and `no_break` averaged `{metrics['no_break_runtime_s']:.3f}s` in the dTLB-instrumented run.",
        f"- `base_pages` was `{metrics['base_pages_pct_vs_no_break']:+.2f}%` versus `no_break` in the dTLB-instrumented run.",
        f"- The 17 hot split-only rows averaged `{metrics['hot_split_mean_runtime_pct']:+.2f}%` versus `no_break`.",
        f"- The 3 random split-only rows averaged `{metrics['random_split_mean_runtime_pct']:+.2f}%` versus `no_break`.",
        f"- `base_pages` dTLB loads were `{metrics['base_pages_dtlb_loads_pct_vs_no_break']:+.2f}%` versus `no_break` in the same run.",
        f"- `base_pages` dTLB misses were `{metrics['base_pages_dtlb_misses_pct_vs_no_break']:+.2f}%` versus `no_break` in the same run.",
        f"- Timed runs in this parquet ranged from `{metrics['runtime_min_s']:.3f}s` to `{metrics['runtime_max_s']:.3f}s`.",
        "",
        "## Slowest Runtime Rows",
        "",
    ]

    for record in slowest_runtime_rows:
        lines.append(
            f"- `{record['short_label']}` = `{record['key']}` with `{record['access_count']}` accesses, "
            f"runtime delta `{record['runtime_pct_vs_no_break']:+.2f}%`, "
            f"dTLB miss delta `{record['dtlb_misses_pct_vs_no_break']:+.2f}%`."
        )

    lines.extend(["", "## Highest dTLB-Miss Rows", ""])

    for record in highest_miss_rows:
        lines.append(
            f"- `{record['short_label']}` = `{record['key']}` with `{record['access_count']}` accesses, "
            f"dTLB miss delta `{record['dtlb_misses_pct_vs_no_break']:+.2f}%`, "
            f"runtime delta `{record['runtime_pct_vs_no_break']:+.2f}%`."
        )

    summary_text = "\n".join(lines) + "\n"
    _write_text_with_aliases(SUMMARY_PATH, LEGACY_SUMMARY_PATHS, summary_text)


def _render_graph(records: list[dict[str, object]], metrics: dict[str, object]) -> None:
    """Render one PNG that visualizes runtime and dTLB deltas for all 22 rows."""

    labels = [str(record["short_label"]) for record in records]
    runtime_pct = [float(record["runtime_pct_vs_no_break"]) for record in records]
    load_pct = [float(record["dtlb_loads_pct_vs_no_break"]) for record in records]
    miss_pct = [float(record["dtlb_misses_pct_vs_no_break"]) for record in records]
    colors = []
    for record in records:
        if record["row_type"] == "base_pages":
            colors.append("#c94f4f")
        elif record["row_type"] == "no_break":
            colors.append("#7f8c8d")
        elif record["row_type"] == "hot_split_only":
            colors.append("#d89b31")
        else:
            colors.append("#2f7d6b")

    x_positions = list(range(len(labels)))
    fig, axes = plt.subplots(3, 1, figsize=(16, 18), sharex=True)

    for ax, values, title, ylabel in [
        (
            axes[0],
            runtime_pct,
            "Runtime delta vs no_break",
            "Runtime delta (%)",
        ),
        (
            axes[1],
            load_pct,
            "dTLB loads delta vs no_break",
            "Load delta (%)",
        ),
        (
            axes[2],
            miss_pct,
            "dTLB misses delta vs no_break",
            "Miss delta (%)",
        ),
    ]:
        ax.bar(x_positions, values, color=colors, edgecolor="black", alpha=0.9)
        ax.axhline(0, color="black", linewidth=1.0)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(axis="y", linestyle="--", alpha=0.3)

    axes[2].set_xticks(x_positions)
    axes[2].set_xticklabels(labels, rotation=55, ha="right")
    fig.suptitle(
        "Replay 3-Minute 22-Row Summary\n"
        f"base_pages runtime delta = {metrics['base_pages_pct_vs_no_break']:+.2f}%, "
        f"base_pages dTLB miss delta = {metrics['base_pages_dtlb_misses_pct_vs_no_break']:+.2f}%"
    )
    fig.tight_layout(rect=(0, 0.02, 1, 0.97))
    fig.savefig(GRAPH_PATH, dpi=200)
    for legacy_path in LEGACY_GRAPH_PATHS:
        fig.savefig(legacy_path, dpi=200)
    plt.close(fig)


def main() -> None:
    """Generate the graph, JSON, summary, and dashboard for the dTLB rerun."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dtlb_df = pl.read_parquet(DTLB_RESULTS_PATH)
    records = _classify_rows(dtlb_df)
    metrics = _write_metrics(records)
    _write_rows(records)
    _write_dashboard_preview(metrics, records)
    _write_summary(records, metrics)
    _render_graph(records, metrics)


if __name__ == "__main__":
    main()
