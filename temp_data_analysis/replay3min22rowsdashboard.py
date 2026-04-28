"""Render a runtime-only HTML dashboard for the 3-minute 22-row replay.

This script reads ``data/replay_3min_results_22rows.parquet`` and writes:

- ``temp_data_analysis/replay_3min_22rows_runtime_dashboard.html``
- ``temp_data_analysis/replay_3min_22rows_runtime_metrics.json``
- ``temp_data_analysis/replay_3min_22rows_runtime_rows.json``
- ``temp_data_analysis/replay_3min_22rows_runtime_summary.md``

The output intentionally excludes dTLB metrics. It is the runtime-only companion
to the paired dashboard that also includes hardware counters.
"""

from __future__ import annotations

import importlib.util
import json
import re
import statistics
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = REPO_ROOT / "data" / "replay_3min_results_22rows.parquet"
TRACE_PATH = REPO_ROOT / "data" / "redis_traces" / "replay_3min_22rows_monitor_run.log"
OUTPUT_DIR = REPO_ROOT / "temp_data_analysis"
DASHBOARD_PATH = OUTPUT_DIR / "replay_3min_22rows_runtime_dashboard.html"
METRICS_PATH = OUTPUT_DIR / "replay_3min_22rows_runtime_metrics.json"
ROWS_PATH = OUTPUT_DIR / "replay_3min_22rows_runtime_rows.json"
SUMMARY_PATH = OUTPUT_DIR / "replay_3min_22rows_runtime_summary.md"
LEGACY_DASHBOARD_PATHS = [OUTPUT_DIR / "replay3min22rowsdashboard.html"]
LEGACY_METRICS_PATHS = [OUTPUT_DIR / "replay3min22rowsdashboard_metrics.json"]
LEGACY_ROWS_PATHS = [OUTPUT_DIR / "replay3min22rowsdashboard_rows.json"]
LEGACY_SUMMARY_PATHS = [OUTPUT_DIR / "replay3min22rowsdashboard_summary.md"]
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
        Mapping from Redis key to access count reconstructed from the accepted
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


def _classify_rows(df: pl.DataFrame) -> list[dict[str, object]]:
    """Attach labels and runtime stats to each runtime-only replay row.

    Args:
        df: Runtime-only replay parquet loaded into memory.

    Returns:
        One record per replay row. Example output:
            {
                "row_type": "hot_split_only",
                "short_label": "hot#4",
                "runtime_mean": 172.4,
                "runtime_samples": [171.8, 173.1, 172.3],
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

    runtime_cols = [column for column in df.columns if column.startswith("runtime_s_")]
    breakpoint_cols = [column for column in df.columns if column not in runtime_cols]

    records: list[dict[str, object]] = []
    for row in df.iter_rows(named=True):
        breakpoints = {key: int(row[key]) for key in breakpoint_cols}
        zero_keys = [key for key, value in breakpoints.items() if value == 0]
        runtime_samples = [float(row[column]) for column in runtime_cols]

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

        records.append(
            {
                "row_type": row_type,
                "key": key,
                "access_count": access_counts.get(key, 0) if key is not None else None,
                "runtime_samples": runtime_samples,
                "runtime_mean": sum(runtime_samples) / len(runtime_samples),
                "runtime_std": (
                    statistics.stdev(runtime_samples)
                    if len(runtime_samples) > 1
                    else 0.0
                ),
            }
        )

    no_break_runtime = next(
        float(record["runtime_mean"])
        for record in records
        if record["row_type"] == "no_break"
    )

    hot_rows = sorted(
        (record for record in records if record["row_type"] == "hot_split_only"),
        key=lambda record: (-int(record["access_count"]), str(record["key"])),
    )
    random_rows = sorted(
        (record for record in records if record["row_type"] == "random_split_only"),
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
        record = next(record for record in records if record["row_type"] == row_type)
        record["hot_rank"] = None
        record["random_rank"] = None
        record["short_label"] = row_type
        ordered_records.append(record)

    ordered_records.extend(hot_rows)
    ordered_records.extend(random_rows)

    for record in ordered_records:
        record["runtime_delta_vs_no_break"] = (
            float(record["runtime_mean"]) - no_break_runtime
        )
        record["runtime_pct_vs_no_break"] = (
            float(record["runtime_mean"]) / no_break_runtime - 1
        ) * 100

    return ordered_records


def _write_metrics(records: list[dict[str, object]]) -> dict[str, object]:
    """Write compact runtime-only metrics for the dashboard cards."""

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
        "results_path": str(RESULTS_PATH),
        "rows": len(records),
        "hot_rows": len(hot_rows),
        "random_rows": len(random_rows),
        "base_pages_runtime_s": float(base_pages["runtime_mean"]),
        "no_break_runtime_s": float(no_break["runtime_mean"]),
        "base_pages_pct_vs_no_break": float(base_pages["runtime_pct_vs_no_break"]),
        "hot_split_mean_runtime_pct": sum(
            float(record["runtime_pct_vs_no_break"]) for record in hot_rows
        )
        / len(hot_rows),
        "random_split_mean_runtime_pct": sum(
            float(record["runtime_pct_vs_no_break"]) for record in random_rows
        )
        / len(random_rows),
        "runtime_min_s": min(all_runtime_samples),
        "runtime_max_s": max(all_runtime_samples),
        "split_rows_slower_than_no_break": sum(
            float(record["runtime_delta_vs_no_break"]) > 0
            for record in hot_rows + random_rows
        ),
    }

    metrics_text = json.dumps(metrics, indent=2) + "\n"
    _write_text_with_aliases(METRICS_PATH, LEGACY_METRICS_PATHS, metrics_text)
    return metrics


def _write_rows(records: list[dict[str, object]]) -> None:
    """Write row-level JSON consumed by the runtime-only dashboard."""

    rows_text = json.dumps(records, indent=2) + "\n"
    _write_text_with_aliases(ROWS_PATH, LEGACY_ROWS_PATHS, rows_text)


def _replace_inline_json_script(html: str, script_id: str, payload: object) -> str:
    """Replace one embedded JSON block inside the dashboard HTML."""

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
    """Return the standalone runtime-only HTML dashboard template."""

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Replay 3-Minute 22-Row Runtime Dashboard</title>
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
  .page { max-width: 1440px; margin: 0 auto; }
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
    td { border: none; padding: 6px 0; }
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
    <h1>Runtime-only view of the longer replay pass</h1>
    <p>
      This dashboard reads only <code>data/replay_3min_results_22rows.parquet</code>.
      The first row is the THP-disabled <code>base_pages</code> baseline, the
      second row is intact-THP <code>no_break</code>, the next 17 rows split the
      hottest keys one at a time, and the final 3 rows split randomly chosen
      non-hot keys one at a time.
    </p>
    <div class="notice">
      This file intentionally excludes all dTLB metrics. It follows the same
      dashboard format as the paired hardware page, but every number here comes
      only from the runtime-only round. The clearer companion filename is
      <code>replay_3min_22rows_tlb_dashboard.html</code>.
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
      <div class="label">Runtime band</div>
      <div class="value" id="runtimeBandValue">--</div>
      <div class="detail" id="runtimeBandDetail">--</div>
    </article>
  </section>

  <section class="grid">
    <article class="card">
      <h2>Runtime-only delta vs no_break</h2>
      <p class="desc">
        This chart uses the runtime-only round to show which rows were slower or
        faster than intact THP.
      </p>
      <div class="chart-wrap tall">
        <canvas id="runtimeChart"></canvas>
      </div>
    </article>

    <article class="card">
      <h2>Runtime-only detail</h2>
      <p class="desc">
        This stacked card now follows the same bar-chart layout as the TLB
        page. The top chart keeps the four high-level runtime groups visible,
        while the bottom chart shows how concentrated the accepted-trace access
        counts were across the 20 split rows.
      </p>
      <div class="chart-wrap">
        <canvas id="groupRuntimeChart"></canvas>
      </div>
      <div class="chart-wrap" style="margin-top: 18px;">
        <canvas id="splitAccessChart"></canvas>
      </div>
    </article>

    <article class="card wide">
      <h2>Important insights</h2>
      <p class="desc">
        These callouts summarize the runtime-only patterns from the longer rerun.
      </p>
      <div class="story-grid" id="storyGrid"></div>
    </article>

    <article class="card wide">
      <h2>All 22 rows</h2>
      <p class="desc">
        The table below keeps the full row set visible: baselines, the 17 hot
        split-only rows, and the 3 random split-only rows.
      </p>
      <div style="overflow-x:auto;">
        <table>
          <thead>
            <tr>
              <th>Label</th>
              <th>Row type</th>
              <th>Key</th>
              <th>Accesses</th>
              <th>Mean Runtime</th>
              <th>Delta Vs no_break</th>
              <th>Samples</th>
            </tr>
          </thead>
          <tbody id="rowsTable"></tbody>
        </table>
      </div>
    </article>
  </section>
</div>

<script id="replay3minRuntimeMetricsData" type="application/json">
{}
</script>
<script id="replay3minRuntimeRowsData" type="application/json">
[]
</script>
<script>
const metrics = JSON.parse(document.getElementById('replay3minRuntimeMetricsData').textContent);
const rows = JSON.parse(document.getElementById('replay3minRuntimeRowsData').textContent);

function formatSeconds(value) { return `${value.toFixed(3)}s`; }
function formatPercent(value) { return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`; }
function chipClassForDelta(value) { return value > 0 ? 'red' : 'teal'; }
function formatRange(minValue, maxValue) { return `${minValue.toFixed(1)}s-${maxValue.toFixed(1)}s`; }

const hotRows = rows.filter((row) => row.row_type === 'hot_split_only');
const randomRows = rows.filter((row) => row.row_type === 'random_split_only');
const splitRows = rows.filter((row) =>
  row.row_type === 'hot_split_only' || row.row_type === 'random_split_only'
);
const hotRuntimeMean = hotRows.reduce((sum, row) => sum + row.runtime_mean, 0) / hotRows.length;
const randomRuntimeMean = randomRows.reduce((sum, row) => sum + row.runtime_mean, 0) / randomRows.length;

document.getElementById('basePagesRuntimeValue').textContent = formatSeconds(metrics.base_pages_runtime_s);
document.getElementById('basePagesRuntimeDetail').innerHTML =
  `<span class="chip ${chipClassForDelta(metrics.base_pages_pct_vs_no_break)}">${formatPercent(metrics.base_pages_pct_vs_no_break)}</span> versus <code>no_break</code>`;
document.getElementById('hotSplitValue').textContent = formatPercent(metrics.hot_split_mean_runtime_pct);
document.getElementById('hotSplitDetail').textContent =
  `Mean runtime delta across ${metrics.hot_rows} hottest split-only rows.`;
document.getElementById('randomSplitValue').textContent = formatPercent(metrics.random_split_mean_runtime_pct);
document.getElementById('randomSplitDetail').textContent =
  `Mean runtime delta across ${metrics.random_rows} random split-only rows.`;
document.getElementById('runtimeBandValue').textContent = formatRange(metrics.runtime_min_s, metrics.runtime_max_s);
document.getElementById('runtimeBandDetail').textContent =
  `Across 66 timed runs from the runtime-only pass.`;

function rowColor(row) {
  if (row.row_type === 'base_pages') return 'rgba(239, 107, 115, 0.75)';
  if (row.row_type === 'no_break') return 'rgba(151, 164, 176, 0.80)';
  if (row.row_type === 'random_split_only') return 'rgba(88, 197, 169, 0.78)';
  return 'rgba(240, 181, 97, 0.78)';
}

new Chart(document.getElementById('runtimeChart'), {
  type: 'bar',
  data: {
    labels: rows.map((row) => row.short_label),
    datasets: [{
      label: 'Runtime delta vs no_break (%)',
      data: rows.map((row) => row.runtime_pct_vs_no_break),
      backgroundColor: rows.map(rowColor),
      borderColor: 'rgba(255,255,255,0.18)',
      borderWidth: 1.2,
      borderRadius: 6,
    }]
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
          callback: (value) => `${value}%`,
        },
        grid: { color: 'rgba(255,255,255,0.08)' }
      }
    }
  }
});

new Chart(document.getElementById('groupRuntimeChart'), {
  type: 'bar',
  data: {
    labels: ['base_pages', 'no_break', 'hot split mean', 'random split mean'],
    datasets: [{
      label: 'Mean runtime (s)',
      data: [
        metrics.base_pages_runtime_s,
        metrics.no_break_runtime_s,
        hotRuntimeMean,
        randomRuntimeMean,
      ],
      backgroundColor: [
        'rgba(239, 107, 115, 0.72)',
        'rgba(151, 164, 176, 0.80)',
        'rgba(240, 181, 97, 0.78)',
        'rgba(88, 197, 169, 0.78)',
      ],
      borderColor: 'rgba(255,255,255,0.16)',
      borderWidth: 1.1,
      borderRadius: 6,
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    scales: {
      x: {
        ticks: { color: '#dce9ff', maxRotation: 0, minRotation: 0 },
        grid: { display: false }
      },
      y: {
        grid: { color: 'rgba(255,255,255,0.08)' },
        ticks: {
          color: '#dce9ff',
          callback: (value) => `${value}s`,
        }
      }
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (ctx) => `${ctx.raw.toFixed(3)}s`
        }
      }
    }
  }
});

new Chart(document.getElementById('splitAccessChart'), {
  type: 'bar',
  data: {
    labels: splitRows.map((row) => row.short_label),
    datasets: [{
      label: 'Accepted-trace access count',
      data: splitRows.map((row) => row.access_count),
      backgroundColor: splitRows.map(rowColor),
      borderColor: 'rgba(255,255,255,0.16)',
      borderWidth: 1.1,
      borderRadius: 6,
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (ctx) => `${ctx.parsed.y.toLocaleString()} accepted-trace accesses`
        }
      }
    },
    scales: {
      x: {
        ticks: { color: '#dce9ff', maxRotation: 70, minRotation: 45 },
        grid: { display: false }
      },
      y: {
        title: { display: true, text: 'Access count in accepted trace', color: '#dce9ff' },
        grid: { color: 'rgba(255,255,255,0.08)' },
        ticks: {
          color: '#dce9ff',
          callback: (value) => Number(value).toLocaleString(),
        }
      }
    }
  }
});

const storyCards = [
  {
    title: 'base_pages gap',
    body: `base_pages is ${formatPercent(metrics.base_pages_pct_vs_no_break)} versus no_break in the runtime-only round.`
  },
  {
    title: 'Hot split average',
    body: `The 17 hottest split-only rows average ${formatPercent(metrics.hot_split_mean_runtime_pct)} versus no_break.`
  },
  {
    title: 'Random split average',
    body: `The 3 random split-only rows average ${formatPercent(metrics.random_split_mean_runtime_pct)} versus no_break.`
  },
  {
    title: 'Runtime band',
    body: `Timed runs ranged from ${formatSeconds(metrics.runtime_min_s)} to ${formatSeconds(metrics.runtime_max_s)}.`
  }
];

document.getElementById('storyGrid').innerHTML = storyCards.map((story) => `
  <div class="story">
    <strong>${story.title}</strong>
    <p>${story.body}</p>
  </div>
`).join('');

document.getElementById('rowsTable').innerHTML = rows.map((row) => {
  const typeText = row.row_type === 'hot_split_only'
    ? 'hot split'
    : row.row_type === 'random_split_only'
      ? 'random split'
      : row.row_type;
  const accessText = row.access_count == null ? '—' : row.access_count.toLocaleString();

  return `
  <tr>
    <td data-label="Label"><span class="chip ${row.row_type === 'base_pages' ? 'red' : row.row_type === 'random_split_only' ? 'teal' : 'gold'}">${row.short_label}</span></td>
    <td data-label="Row type">${typeText}</td>
    <td data-label="Key" class="mono">${row.key ?? '—'}</td>
    <td data-label="Accesses">${accessText}</td>
    <td data-label="Mean Runtime">${formatSeconds(row.runtime_mean)}</td>
    <td data-label="Delta Vs no_break">${formatPercent(row.runtime_pct_vs_no_break)}</td>
    <td data-label="Samples" class="mono">${row.runtime_samples.map(formatSeconds).join(', ')}</td>
  </tr>
`;
}).join('');
</script>
</body>
</html>
"""


def _write_dashboard(metrics: dict[str, object], records: list[dict[str, object]]) -> None:
    """Write the runtime-only HTML dashboard with embedded JSON preview data."""

    html = _build_dashboard_html()
    html = _replace_inline_json_script(
        html,
        "replay3minRuntimeMetricsData",
        metrics,
    )
    html = _replace_inline_json_script(
        html,
        "replay3minRuntimeRowsData",
        records,
    )
    _write_text_with_aliases(DASHBOARD_PATH, LEGACY_DASHBOARD_PATHS, html)


def _write_summary(records: list[dict[str, object]], metrics: dict[str, object]) -> None:
    """Write a short markdown summary for the runtime-only-only dashboard."""

    hot_rows = [record for record in records if record["row_type"] == "hot_split_only"]
    random_rows = [record for record in records if record["row_type"] == "random_split_only"]
    slowest = sorted(
        hot_rows + random_rows,
        key=lambda record: float(record["runtime_delta_vs_no_break"]),
        reverse=True,
    )[:5]

    lines = [
        "# Replay 3-Minute 22-Row Runtime-Only Summary",
        "",
        f"Source parquet: `{RESULTS_PATH.relative_to(REPO_ROOT)}`",
        "",
        "## Important Takeaways",
        "",
        f"- `base_pages` averaged `{metrics['base_pages_runtime_s']:.3f}s` and `no_break` averaged `{metrics['no_break_runtime_s']:.3f}s`.",
        f"- `base_pages` was `{metrics['base_pages_pct_vs_no_break']:+.2f}%` versus `no_break`.",
        f"- The `17` hot split-only rows averaged `{metrics['hot_split_mean_runtime_pct']:+.2f}%` versus `no_break`.",
        f"- The `3` random split-only rows averaged `{metrics['random_split_mean_runtime_pct']:+.2f}%` versus `no_break`.",
        f"- Runtime-only timed runs ranged from `{metrics['runtime_min_s']:.3f}s` to `{metrics['runtime_max_s']:.3f}s`.",
        "",
        "## Slowest Runtime Rows",
        "",
    ]

    for record in slowest:
        lines.append(
            f"- `{record['short_label']}` = `{record['key']}` with `{record['access_count']}` accesses, "
            f"runtime delta `{record['runtime_pct_vs_no_break']:+.2f}%`."
        )

    summary_text = "\n".join(lines) + "\n"
    _write_text_with_aliases(SUMMARY_PATH, LEGACY_SUMMARY_PATHS, summary_text)


def main() -> None:
    """Generate the runtime-only dashboard and supporting summary files."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    records = _classify_rows(pl.read_parquet(RESULTS_PATH))
    metrics = _write_metrics(records)
    _write_rows(records)
    _write_dashboard(metrics, records)
    _write_summary(records, metrics)


if __name__ == "__main__":
    main()
