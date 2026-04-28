"""Render a dTLB-focused dashboard for the 15000-item read-mix replay test.

This script is intended for the intermediate ``2-2-1`` replay that reuses the
accepted Test 1 read-heavy trace and adds replay-local dTLB counters.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATE_BREAKPOINTS_PATH = (
    REPO_ROOT / "python" / "kernmlops" / "replay" / "generate_breakpoints.py"
)


def _load_access_counts(trace_path: Path) -> dict[str, int]:
    """Return per-key access counts reconstructed from a monitor log."""

    spec = importlib.util.spec_from_file_location(
        "generate_breakpoints",
        GENERATE_BREAKPOINTS_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load generate_breakpoints.py for access counts")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_log(trace_path)


def _stddev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def _classify_rows(
    df: pl.DataFrame,
    trace_path: Path,
    hot_key_rows: int,
) -> list[dict[str, object]]:
    """Attach row labels plus runtime/dTLB stats to each replay row."""

    access_counts = _load_access_counts(trace_path)
    ordered_keys = [
        key
        for key, _count in sorted(
            access_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]
    hot_key_set = set(ordered_keys[:hot_key_rows])

    runtime_cols = [column for column in df.columns if column.startswith("runtime_s_")]
    dtlb_load_cols = [column for column in df.columns if column.startswith("dtlb_loads_")]
    dtlb_miss_cols = [column for column in df.columns if column.startswith("dtlb_misses_")]
    breakpoint_cols = [
        column
        for column in df.columns
        if column not in runtime_cols
        and column not in dtlb_load_cols
        and column not in dtlb_miss_cols
    ]

    raw_records: list[dict[str, object]] = []
    for row in df.iter_rows(named=True):
        breakpoints = {key: int(row[key]) for key in breakpoint_cols}
        zero_keys = [key for key, value in breakpoints.items() if value == 0]

        runtime_samples = [float(row[column]) for column in runtime_cols]
        dtlb_loads_samples = [float(row[column]) for column in dtlb_load_cols]
        dtlb_misses_samples = [float(row[column]) for column in dtlb_miss_cols]

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
                "runtime_mean": sum(runtime_samples) / len(runtime_samples),
                "runtime_std": _stddev(runtime_samples),
                "dtlb_loads_samples": dtlb_loads_samples,
                "dtlb_loads_mean": sum(dtlb_loads_samples) / len(dtlb_loads_samples),
                "dtlb_loads_std": _stddev(dtlb_loads_samples),
                "dtlb_misses_samples": dtlb_misses_samples,
                "dtlb_misses_mean": sum(dtlb_misses_samples) / len(dtlb_misses_samples),
                "dtlb_misses_std": _stddev(dtlb_misses_samples),
            }
        )

    no_break_runtime = next(
        float(record["runtime_mean"])
        for record in raw_records
        if record["row_type"] == "no_break"
    )
    no_break_dtlb_loads = next(
        float(record["dtlb_loads_mean"])
        for record in raw_records
        if record["row_type"] == "no_break"
    )
    no_break_dtlb_misses = next(
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
        record["runtime_delta_vs_no_break"] = (
            float(record["runtime_mean"]) - no_break_runtime
        )
        record["runtime_pct_vs_no_break"] = (
            float(record["runtime_mean"]) / no_break_runtime - 1
        ) * 100
        record["dtlb_loads_delta_vs_no_break"] = (
            float(record["dtlb_loads_mean"]) - no_break_dtlb_loads
        )
        record["dtlb_loads_pct_vs_no_break"] = (
            float(record["dtlb_loads_mean"]) / no_break_dtlb_loads - 1
        ) * 100
        record["dtlb_misses_delta_vs_no_break"] = (
            float(record["dtlb_misses_mean"]) - no_break_dtlb_misses
        )
        record["dtlb_misses_pct_vs_no_break"] = (
            float(record["dtlb_misses_mean"]) / no_break_dtlb_misses - 1
        ) * 100

    return ordered_records


def _replace_inline_json_script(html: str, script_id: str, payload: object) -> str:
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
        raise RuntimeError(f"Could not find inline JSON block '{script_id}'")
    return updated_html


def _build_metrics(
    records: list[dict[str, object]],
    results_path: Path,
    trace_path: Path,
    workload_label: str,
) -> dict[str, object]:
    base_pages = next(record for record in records if record["row_type"] == "base_pages")
    no_break = next(record for record in records if record["row_type"] == "no_break")
    hot_rows = [record for record in records if record["row_type"] == "hot_split_only"]
    random_rows = [record for record in records if record["row_type"] == "random_split_only"]

    all_runtime_samples = [
        float(sample)
        for record in records
        for sample in list(record["runtime_samples"])
    ]
    all_dtlb_miss_samples = [
        float(sample)
        for record in records
        for sample in list(record["dtlb_misses_samples"])
    ]

    return {
        "results_path": str(results_path),
        "trace_path": str(trace_path),
        "workload_label": workload_label,
        "rows": len(records),
        "hot_rows": len(hot_rows),
        "random_rows": len(random_rows),
        "base_pages_runtime_s": float(base_pages["runtime_mean"]),
        "no_break_runtime_s": float(no_break["runtime_mean"]),
        "base_pages_pct_vs_no_break": float(base_pages["runtime_pct_vs_no_break"]),
        "base_pages_dtlb_misses": float(base_pages["dtlb_misses_mean"]),
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
        "hot_split_mean_miss_pct": sum(
            float(record["dtlb_misses_pct_vs_no_break"]) for record in hot_rows
        )
        / len(hot_rows),
        "random_split_mean_miss_pct": sum(
            float(record["dtlb_misses_pct_vs_no_break"]) for record in random_rows
        )
        / len(random_rows),
        "runtime_min_s": min(all_runtime_samples),
        "runtime_max_s": max(all_runtime_samples),
        "dtlb_miss_min": min(all_dtlb_miss_samples),
        "dtlb_miss_max": max(all_dtlb_miss_samples),
        "timed_runs": len(all_runtime_samples),
        "split_rows_slower_than_no_break": sum(
            float(record["runtime_delta_vs_no_break"]) > 0
            for record in hot_rows + random_rows
        ),
    }


def _build_dashboard_html(
    title: str,
    header_title: str,
    header_description: str,
    results_relpath: str,
) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0a1220;
    --bg-accent: #11243d;
    --card: rgba(10, 22, 39, 0.82);
    --border: rgba(135, 178, 255, 0.16);
    --text: #edf4ff;
    --muted: #9eb0c9;
    --red: #e94560;
    --blue: #4f8cff;
    --gold: #f5a623;
    --teal: #53c9b1;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: "Segoe UI", system-ui, sans-serif;
    color: var(--text);
    background:
      radial-gradient(circle at top left, rgba(76, 130, 255, 0.18), transparent 32%),
      radial-gradient(circle at top right, rgba(83, 201, 177, 0.16), transparent 28%),
      linear-gradient(180deg, var(--bg) 0%, #07101b 100%);
  }}
  .page {{
    max-width: 1300px;
    margin: 0 auto;
    padding: 36px 24px 64px;
  }}
  header {{
    margin-bottom: 26px;
  }}
  .eyebrow {{
    display: inline-flex;
    padding: 7px 12px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: rgba(79, 140, 255, 0.08);
    color: #d7e6ff;
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 12px;
  }}
  h1 {{
    margin: 0 0 10px;
    font-size: clamp(2rem, 4vw, 3.2rem);
    line-height: 1.05;
  }}
  header p {{
    max-width: 980px;
    margin: 0;
    color: var(--muted);
    font-size: 1rem;
    line-height: 1.7;
  }}
  code {{
    font-family: "SFMono-Regular", ui-monospace, monospace;
    color: #dff5ff;
  }}
  .notice {{
    margin-top: 16px;
    padding: 14px 16px;
    border-radius: 16px;
    background: rgba(79, 140, 255, 0.08);
    border: 1px solid rgba(79, 140, 255, 0.18);
    color: #dbe8ff;
    line-height: 1.6;
  }}
  .stats {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 14px;
    margin: 22px 0 24px;
  }}
  .stat, .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 22px;
    box-shadow: 0 28px 80px rgba(0, 0, 0, 0.26);
    backdrop-filter: blur(14px);
  }}
  .stat {{
    padding: 18px 18px 16px;
  }}
  .stat .label {{
    color: var(--muted);
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }}
  .stat .value {{
    margin-top: 10px;
    font-size: 1.95rem;
    font-weight: 700;
  }}
  .stat .detail {{
    margin-top: 8px;
    color: #cbdaf3;
    font-size: 0.94rem;
    line-height: 1.5;
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(12, minmax(0, 1fr));
    gap: 16px;
  }}
  .card {{
    padding: 20px;
    grid-column: span 6;
  }}
  .card.full-width {{
    grid-column: 1 / -1;
  }}
  .card h2 {{
    margin: 0 0 10px;
    font-size: 1.15rem;
  }}
  .desc {{
    margin: 0 0 16px;
    color: var(--muted);
    line-height: 1.65;
  }}
  .chart-wrap {{
    position: relative;
    min-height: 320px;
  }}
  .chip {{
    display: inline-flex;
    align-items: center;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 0.02em;
  }}
  .chip.red {{ background: rgba(233, 69, 96, 0.18); color: #ffafbc; }}
  .chip.blue {{ background: rgba(79, 140, 255, 0.18); color: #bad2ff; }}
  .chip.gold {{ background: rgba(245, 166, 35, 0.18); color: #ffd88f; }}
  .chip.teal {{ background: rgba(83, 201, 177, 0.18); color: #bff3e6; }}
  .story-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 14px;
  }}
  .story {{
    padding: 16px;
    border-radius: 18px;
    border: 1px solid rgba(135, 178, 255, 0.12);
    background: rgba(255, 255, 255, 0.02);
  }}
  .story strong {{
    display: block;
    margin-bottom: 8px;
    font-size: 0.96rem;
  }}
  .story p {{
    margin: 0;
    color: var(--muted);
    line-height: 1.55;
  }}
  table.summary {{
    width: 100%;
    border-collapse: collapse;
    overflow: hidden;
    border-radius: 18px;
  }}
  table.summary th,
  table.summary td {{
    padding: 12px 12px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    text-align: left;
    vertical-align: top;
  }}
  table.summary th {{
    color: #d7e6ff;
    font-size: 0.82rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }}
  table.summary td {{
    color: #edf4ff;
    font-size: 0.94rem;
  }}
  .mono {{
    font-family: "SFMono-Regular", ui-monospace, monospace;
    font-size: 0.88rem;
  }}
  @media (max-width: 960px) {{
    .card {{ grid-column: 1 / -1; }}
  }}
  @media (max-width: 720px) {{
    .page {{ padding: 24px 14px 40px; }}
    table.summary,
    table.summary thead,
    table.summary tbody,
    table.summary th,
    table.summary td,
    table.summary tr {{
      display: block;
      width: 100%;
    }}
    table.summary thead {{
      display: none;
    }}
    table.summary tr {{
      margin-bottom: 12px;
      padding: 10px;
      border-radius: 14px;
      background: rgba(255,255,255,0.03);
      border: 1px solid rgba(255,255,255,0.06);
    }}
    table.summary td {{
      border: none;
      padding: 6px 0;
    }}
    table.summary td::before {{
      content: attr(data-label);
      display: block;
      color: #d8e6ff;
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 4px;
    }}
  }}
</style>
</head>
<body>
<div class="page">
<header>
  <div class="eyebrow">Redis Replay · 15000 Items · dTLB Intermediate</div>
  <h1>{header_title}</h1>
  <p>
    {header_description} This dashboard reads
    <code>{results_relpath}</code>. Row 1 is the THP-disabled
    <code>base_pages</code> baseline, row 2 is the intact-THP
    <code>no_break</code> row, the next two rows split the hottest keys one
    at a time, and the final row splits one random non-hot key.
  </p>
  <div class="notice">
    This page is the intermediate dTLB pass between the two read-mix runtime
    tests. The main chart focuses on dTLB misses relative to <code>no_break</code>.
  </div>
</header>

<section class="stats">
  <article class="stat">
    <div class="label">base_pages dTLB misses</div>
    <div class="value" id="basePagesMissValue">--</div>
    <div class="detail" id="basePagesMissDetail">--</div>
  </article>
  <article class="stat">
    <div class="label">Hot split miss mean</div>
    <div class="value" id="hotMissValue">--</div>
    <div class="detail" id="hotMissDetail">--</div>
  </article>
  <article class="stat">
    <div class="label">Random split miss mean</div>
    <div class="value" id="randomMissValue">--</div>
    <div class="detail" id="randomMissDetail">--</div>
  </article>
  <article class="stat">
    <div class="label">Runtime band</div>
    <div class="value" id="runtimeBandValue">--</div>
    <div class="detail" id="runtimeBandDetail">--</div>
  </article>
</section>

<section class="grid">
  <div class="card full-width">
    <h2>Section 1 — dTLB Miss Delta</h2>
    <p class="desc">
      Percentage dTLB miss delta for each row relative to <code>no_break</code>.
      <span class="chip red">Red</span> marks <code>base_pages</code>,
      <span class="chip blue">blue</span> marks <code>no_break</code>,
      <span class="chip gold">gold</span> are the hottest split-only keys, and
      <span class="chip teal">teal</span> is the random split-only key.
    </p>
    <div class="chart-wrap"><canvas id="missChart"></canvas></div>
  </div>

  <div class="card">
    <h2>Section 2 — Runtime Delta</h2>
    <p class="desc">
      Runtime percentage delta relative to <code>no_break</code> from the same
      dTLB-instrumented replay.
    </p>
    <div class="chart-wrap"><canvas id="runtimeChart"></canvas></div>
  </div>

  <div class="card">
    <h2>Section 3 — Miss Change vs Access Count</h2>
    <p class="desc">
      Each dot is one split-only-key row. The X-axis is accepted-trace access
      count and the Y-axis is dTLB miss delta (%) versus <code>no_break</code>.
    </p>
    <div class="chart-wrap"><canvas id="scatterChart"></canvas></div>
  </div>

  <div class="card full-width">
    <h2>Section 4 — Important Insights</h2>
    <p class="desc">
      High-level runtime and dTLB takeaways from this intermediate pass.
    </p>
    <div class="story-grid" id="storyGrid"></div>
  </div>

  <div class="card full-width">
    <h2>Section 5 — Row Summary</h2>
    <p class="desc">
      Full per-row runtime samples together with dTLB loads and misses.
    </p>
    <div id="summaryTableWrap"></div>
  </div>
</section>
</div>

<script id="readmixDtlbMetricsData" type="application/json">
{{}}
</script>
<script id="readmixDtlbRowsData" type="application/json">
[]
</script>
<script>
Chart.defaults.color = '#a0a0b0';
Chart.defaults.borderColor = 'rgba(255,255,255,0.07)';
Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";

const metrics = JSON.parse(document.getElementById('readmixDtlbMetricsData').textContent);
const rows = JSON.parse(document.getElementById('readmixDtlbRowsData').textContent);

function formatSeconds(value) {{
  return `${{value.toFixed(3)}}s`;
}}

function formatPercent(value) {{
  return `${{value >= 0 ? '+' : ''}}${{value.toFixed(2)}}%`;
}}

function formatMillions(value) {{
  return `${{(value / 1_000_000).toFixed(2)}}M`;
}}

function rowColor(row) {{
  if (row.row_type === 'base_pages') return '#e94560';
  if (row.row_type === 'no_break') return '#4f8cff';
  if (row.row_type === 'random_split_only') return '#53c9b1';
  return '#f5a623';
}}

const hotRows = rows.filter((row) => row.row_type === 'hot_split_only');
const randomRows = rows.filter((row) => row.row_type === 'random_split_only');
const splitRows = rows.filter((row) =>
  row.row_type === 'hot_split_only' || row.row_type === 'random_split_only'
);

document.getElementById('basePagesMissValue').textContent =
  formatMillions(metrics.base_pages_dtlb_misses);
document.getElementById('basePagesMissDetail').innerHTML =
  `<span class="chip ${{metrics.base_pages_dtlb_misses_pct_vs_no_break > 0 ? 'red' : 'teal'}}">${{formatPercent(metrics.base_pages_dtlb_misses_pct_vs_no_break)}}</span> versus <code>no_break</code>`;
document.getElementById('hotMissValue').textContent =
  formatPercent(metrics.hot_split_mean_miss_pct);
document.getElementById('hotMissDetail').textContent =
  `Mean miss delta across ${{metrics.hot_rows}} hottest split-only rows.`;
document.getElementById('randomMissValue').textContent =
  formatPercent(metrics.random_split_mean_miss_pct);
document.getElementById('randomMissDetail').textContent =
  `Mean miss delta across ${{metrics.random_rows}} random split-only rows.`;
document.getElementById('runtimeBandValue').textContent =
  `${{metrics.runtime_min_s.toFixed(1)}}s-${{metrics.runtime_max_s.toFixed(1)}}s`;
document.getElementById('runtimeBandDetail').textContent =
  `Across ${{metrics.timed_runs}} timed runs in the dTLB pass.`;

new Chart(document.getElementById('missChart'), {{
  type: 'bar',
  data: {{
    labels: rows.map((row) => row.short_label),
    datasets: [{{
      label: 'dTLB misses delta vs no_break (%)',
      data: rows.map((row) => row.dtlb_misses_pct_vs_no_break),
      backgroundColor: rows.map(rowColor),
      borderColor: 'rgba(255,255,255,0.18)',
      borderWidth: 1.2,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: (ctx) => `${{ctx.dataset.label}}: ${{formatPercent(ctx.raw)}}`,
        }}
      }}
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#e0e0e0' }},
        grid: {{ display: false }}
      }},
      y: {{
        title: {{ display: true, text: 'dTLB misses delta vs no_break (%)', color: '#e0e0e0' }},
        ticks: {{
          color: '#e0e0e0',
          callback: (value) => `${{value}}%`
        }},
        grid: {{ color: 'rgba(255,255,255,0.08)' }}
      }}
    }}
  }}
}});

new Chart(document.getElementById('runtimeChart'), {{
  type: 'bar',
  data: {{
    labels: rows.map((row) => row.short_label),
    datasets: [{{
      label: 'Runtime delta vs no_break (%)',
      data: rows.map((row) => row.runtime_pct_vs_no_break),
      backgroundColor: rows.map(rowColor),
      borderColor: 'rgba(255,255,255,0.18)',
      borderWidth: 1.2,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: (ctx) => `${{ctx.dataset.label}}: ${{formatPercent(ctx.raw)}}`,
        }}
      }}
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#e0e0e0' }},
        grid: {{ display: false }}
      }},
      y: {{
        title: {{ display: true, text: 'Runtime delta vs no_break (%)', color: '#e0e0e0' }},
        ticks: {{
          color: '#e0e0e0',
          callback: (value) => `${{value}}%`
        }},
        grid: {{ color: 'rgba(255,255,255,0.08)' }}
      }}
    }}
  }}
}});

new Chart(document.getElementById('scatterChart'), {{
  type: 'scatter',
  data: {{
    datasets: splitRows.map((row) => ({{
      label: row.short_label,
      data: [{{ x: row.access_count, y: row.dtlb_misses_pct_vs_no_break }}],
      backgroundColor: rowColor(row),
      pointRadius: 6,
      pointHoverRadius: 8,
    }}))
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: (ctx) => `${{ctx.dataset.label}}: ${{ctx.raw.x.toLocaleString()}} accesses, ${{formatPercent(ctx.raw.y)}} miss delta`,
        }}
      }}
    }},
    scales: {{
      x: {{
        title: {{ display: true, text: 'Accepted-trace access count', color: '#e0e0e0' }},
        ticks: {{ color: '#e0e0e0' }},
      }},
      y: {{
        title: {{ display: true, text: 'dTLB misses delta vs no_break (%)', color: '#e0e0e0' }},
        ticks: {{
          color: '#e0e0e0',
          callback: (value) => `${{value}}%`
        }}
      }}
    }}
  }}
}});

const storyCards = [
  {{
    title: 'base_pages miss shift',
    body: `base_pages averaged ${{formatMillions(metrics.base_pages_dtlb_misses)}} dTLB misses and was ${{formatPercent(metrics.base_pages_dtlb_misses_pct_vs_no_break)}} versus no_break.`,
  }},
  {{
    title: 'Hot split miss average',
    body: `The two hot split rows averaged ${{formatPercent(metrics.hot_split_mean_miss_pct)}} versus no_break.`,
  }},
  {{
    title: 'Random split miss average',
    body: `The random split row averaged ${{formatPercent(metrics.random_split_mean_miss_pct)}} versus no_break.`,
  }},
  {{
    title: 'Runtime range',
    body: `Timed runs ranged from ${{formatSeconds(metrics.runtime_min_s)}} to ${{formatSeconds(metrics.runtime_max_s)}} across ${{metrics.timed_runs}} timed runs.`,
  }},
];

document.getElementById('storyGrid').innerHTML = storyCards.map((story) => `
  <div class="story">
    <strong>${{story.title}}</strong>
    <p>${{story.body}}</p>
  </div>
`).join('');

document.getElementById('summaryTableWrap').innerHTML = `
  <table class="summary">
    <thead>
      <tr>
        <th>Label</th>
        <th>Row Type</th>
        <th>Key</th>
        <th>Accesses</th>
        <th>Runtime Δ</th>
        <th>dTLB Miss Δ</th>
        <th>dTLB Loads Mean</th>
        <th>dTLB Misses Mean</th>
        <th>Runtime Samples</th>
      </tr>
    </thead>
    <tbody>
      ${{
        rows.map((row) => `
          <tr>
            <td data-label="Label"><span class="chip ${{
              row.row_type === 'base_pages' ? 'red' :
              row.row_type === 'no_break' ? 'blue' :
              row.row_type === 'random_split_only' ? 'teal' : 'gold'
            }}">${{row.short_label}}</span></td>
            <td data-label="Row Type">${{row.row_type}}</td>
            <td data-label="Key" class="mono">${{row.key ?? '—'}}</td>
            <td data-label="Accesses">${{row.access_count == null ? '—' : row.access_count.toLocaleString()}}</td>
            <td data-label="Runtime Δ">${{formatPercent(row.runtime_pct_vs_no_break)}}</td>
            <td data-label="dTLB Miss Δ">${{formatPercent(row.dtlb_misses_pct_vs_no_break)}}</td>
            <td data-label="dTLB Loads Mean">${{formatMillions(row.dtlb_loads_mean)}}</td>
            <td data-label="dTLB Misses Mean">${{formatMillions(row.dtlb_misses_mean)}}</td>
            <td data-label="Runtime Samples" class="mono">${{row.runtime_samples.map(formatSeconds).join(', ')}}</td>
          </tr>
        `).join('')
      }}
    </tbody>
  </table>
`;
</script>
</body>
</html>
"""


def _write_outputs(
    records: list[dict[str, object]],
    metrics: dict[str, object],
    html_path: Path,
    rows_path: Path,
    metrics_path: Path,
    summary_path: Path,
    title: str,
    header_title: str,
    header_description: str,
    results_path: Path,
) -> None:
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    rows_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

    html = _build_dashboard_html(
        title=title,
        header_title=header_title,
        header_description=header_description,
        results_relpath=str(results_path.relative_to(REPO_ROOT)),
    )
    html = _replace_inline_json_script(html, "readmixDtlbMetricsData", metrics)
    html = _replace_inline_json_script(html, "readmixDtlbRowsData", records)
    html_path.write_text(html, encoding="utf-8")

    lines = [
        f"# {header_title}",
        "",
        f"Source parquet: `{results_path.relative_to(REPO_ROOT)}`",
        "",
        "## Important Takeaways",
        "",
        f"- `base_pages` runtime was `{metrics['base_pages_runtime_s']:.3f}s` and `no_break` runtime was `{metrics['no_break_runtime_s']:.3f}s`.",
        f"- `base_pages` runtime delta vs `no_break` was `{metrics['base_pages_pct_vs_no_break']:+.2f}%`.",
        f"- `base_pages` dTLB misses were `{metrics['base_pages_dtlb_misses']:.0f}` on average and `{metrics['base_pages_dtlb_misses_pct_vs_no_break']:+.2f}%` versus `no_break`.",
        f"- The `2` hot split-only rows averaged `{metrics['hot_split_mean_miss_pct']:+.2f}%` dTLB misses versus `no_break`.",
        f"- The `1` random split-only row averaged `{metrics['random_split_mean_miss_pct']:+.2f}%` dTLB misses versus `no_break`.",
        "",
    ]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a dTLB dashboard for a 15000-item intermediate replay run.",
    )
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output-html", type=Path, required=True)
    parser.add_argument("--output-metrics", type=Path, required=True)
    parser.add_argument("--output-rows", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--header-title", required=True)
    parser.add_argument("--header-description", required=True)
    parser.add_argument("--workload-label", required=True)
    parser.add_argument("--hot-keys", type=int, default=2)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    args.results = args.results.resolve()
    args.trace = args.trace.resolve()
    args.output_html = args.output_html.resolve()
    args.output_metrics = args.output_metrics.resolve()
    args.output_rows = args.output_rows.resolve()
    args.output_summary = args.output_summary.resolve()
    args.output_html.parent.mkdir(parents=True, exist_ok=True)

    df = pl.read_parquet(args.results)
    records = _classify_rows(df, args.trace, hot_key_rows=args.hot_keys)
    metrics = _build_metrics(
        records,
        results_path=args.results,
        trace_path=args.trace,
        workload_label=args.workload_label,
    )
    _write_outputs(
        records=records,
        metrics=metrics,
        html_path=args.output_html,
        rows_path=args.output_rows,
        metrics_path=args.output_metrics,
        summary_path=args.output_summary,
        title=args.title,
        header_title=args.header_title,
        header_description=args.header_description,
        results_path=args.results,
    )


if __name__ == "__main__":
    main()
