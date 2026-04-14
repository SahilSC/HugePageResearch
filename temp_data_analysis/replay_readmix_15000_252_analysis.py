"""Render runtime-only dashboards for the 15000-item read-mix replay runs.

This script is parameterized so the same analysis path can render:

- the ``100% read`` runtime-only experiment
- the ``80% read / 20% delete`` runtime-only experiment

It writes a standalone HTML dashboard plus compact JSON and markdown summary
files beside it in ``temp_data_analysis``.
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
    """Return per-key access counts reconstructed from a monitor log.

    Args:
        trace_path: Accepted replay monitor log used to generate the result rows.

    Returns:
        Mapping from Redis key to access count. Example output:
            {"user3798598596772897818": 549}

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
    return module.parse_log(trace_path)


def _classify_rows(
    df: pl.DataFrame,
    trace_path: Path,
    hot_key_rows: int,
) -> list[dict[str, object]]:
    """Attach labels and runtime stats to each replay row.

    Args:
        df: Replay parquet loaded into memory.
        trace_path: Accepted monitor log for access-count reconstruction.
        hot_key_rows: Number of hottest split rows expected in the matrix.

    Returns:
        One record per replay row. Example output:
            {
                "row_type": "hot_split_only",
                "short_label": "hot#2",
                "runtime_mean": 58.4,
                "runtime_samples": [57.9, 58.9],
            }
    """

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


def _build_metrics(
    records: list[dict[str, object]],
    results_path: Path,
    trace_path: Path,
    workload_label: str,
) -> dict[str, object]:
    """Build compact runtime metrics for the dashboard cards."""

    base_pages = next(record for record in records if record["row_type"] == "base_pages")
    no_break = next(record for record in records if record["row_type"] == "no_break")
    hot_rows = [record for record in records if record["row_type"] == "hot_split_only"]
    random_rows = [record for record in records if record["row_type"] == "random_split_only"]

    all_runtime_samples = [
        float(sample)
        for record in records
        for sample in list(record["runtime_samples"])
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
    """Return the standalone HTML dashboard template."""

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
    --blue: #79a8ff;
    --teal: #5fd1b3;
    --gold: #ffbf66;
    --red: #ff6c7d;
    --shadow: 0 20px 50px rgba(0, 0, 0, 0.28);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    min-height: 100vh;
    color: var(--text);
    font-family: "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    background:
      radial-gradient(circle at top left, rgba(121, 168, 255, 0.18), transparent 30%),
      radial-gradient(circle at top right, rgba(95, 209, 179, 0.14), transparent 28%),
      linear-gradient(180deg, #0b1423 0%, #09111d 60%, #07101a 100%);
    padding: 28px 20px 36px;
  }}
  .page {{
    max-width: 1360px;
    margin: 0 auto;
  }}
  header {{ margin-bottom: 24px; }}
  .eyebrow {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 7px 12px;
    border-radius: 999px;
    background: rgba(121, 168, 255, 0.12);
    border: 1px solid rgba(121, 168, 255, 0.16);
    color: #cfe0ff;
    font-size: 0.82rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }}
  header h1 {{
    margin: 14px 0 10px;
    font-size: clamp(2rem, 4vw, 3.4rem);
    line-height: 1.04;
    letter-spacing: -0.03em;
  }}
  header p {{
    margin: 0;
    max-width: 900px;
    color: var(--muted);
    font-size: 1rem;
    line-height: 1.65;
  }}
  .notice {{
    margin-top: 14px;
    display: inline-block;
    padding: 10px 12px;
    border-radius: 12px;
    background: rgba(255, 191, 102, 0.09);
    border: 1px solid rgba(255, 191, 102, 0.16);
    color: #ffe4bb;
    font-size: 0.88rem;
  }}
  .stats {{
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 16px;
    margin-bottom: 18px;
  }}
  .stat {{
    background: linear-gradient(180deg, rgba(17, 36, 61, 0.96), rgba(10, 22, 39, 0.96));
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 18px;
    box-shadow: var(--shadow);
  }}
  .stat .label {{
    color: var(--muted);
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  .stat .value {{
    margin-top: 10px;
    font-size: clamp(1.7rem, 3vw, 2.45rem);
    line-height: 1;
    font-weight: 700;
    letter-spacing: -0.04em;
  }}
  .stat .detail {{
    margin-top: 10px;
    color: var(--muted);
    font-size: 0.92rem;
    line-height: 1.45;
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(12, minmax(0, 1fr));
    gap: 18px;
  }}
  .card {{
    grid-column: span 6;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 22px;
    padding: 20px 20px 18px;
    backdrop-filter: blur(16px);
    box-shadow: var(--shadow);
  }}
  .card.full-width {{
    grid-column: 1 / -1;
  }}
  .card h2 {{
    margin: 0 0 8px;
    font-size: 1.18rem;
    line-height: 1.25;
  }}
  .card .desc {{
    margin: 0 0 16px;
    color: var(--muted);
    font-size: 0.93rem;
    line-height: 1.58;
  }}
  .chart-wrap {{
    position: relative;
    height: 360px;
  }}
  .chart-wrap.tall {{ height: 460px; }}
  canvas {{
    width: 100% !important;
    height: 100% !important;
  }}
  .story-grid {{
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
    margin-top: 4px;
  }}
  .story {{
    background: rgba(17, 36, 61, 0.72);
    border: 1px solid rgba(135, 178, 255, 0.12);
    border-radius: 16px;
    padding: 14px 16px;
  }}
  .story strong {{
    display: block;
    margin-bottom: 6px;
    color: #ffffff;
    font-size: 0.96rem;
  }}
  .story p {{
    margin: 0;
    color: var(--muted);
    font-size: 0.9rem;
    line-height: 1.5;
  }}
  .error-msg {{
    margin-top: 8px;
    padding: 12px 14px;
    border-radius: 12px;
    background: rgba(255, 108, 125, 0.1);
    border: 1px solid rgba(255, 108, 125, 0.22);
    color: #ffdbe0;
    font-size: 0.92rem;
  }}
  table.summary {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.92rem;
  }}
  table.summary th, table.summary td {{
    padding: 12px 10px;
    text-align: left;
    border-bottom: 1px solid rgba(135, 178, 255, 0.1);
    vertical-align: top;
  }}
  table.summary th {{
    color: #d8e6ff;
    font-size: 0.84rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  table.summary td:first-child {{
    font-weight: 500;
  }}
  table.summary thead th {{
    color: #fff;
    border-bottom: 2px solid rgba(135, 178, 255, 0.1);
  }}
  table.summary tbody tr:hover {{
    background: rgba(121, 168, 255, 0.05);
  }}
  .chip {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.02em;
  }}
  .chip.red {{
    color: #ffdbe0;
    background: rgba(255, 108, 125, 0.13);
  }}
  .chip.teal {{
    color: #d9fff4;
    background: rgba(95, 209, 179, 0.13);
  }}
  .chip.blue {{
    color: #dce8ff;
    background: rgba(121, 168, 255, 0.14);
  }}
  .chip.gold {{
    color: #ffecce;
    background: rgba(255, 191, 102, 0.14);
  }}
  .mono {{
    font-family: "SFMono-Regular", ui-monospace, "Cascadia Code", Consolas, monospace;
    font-size: 0.84rem;
    color: #dfe9ff;
    word-break: break-all;
  }}
  @media (max-width: 1100px) {{
    .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .card {{ grid-column: 1 / -1; }}
  }}
  @media (max-width: 720px) {{
    body {{ padding: 20px 14px 32px; }}
    .stats {{ grid-template-columns: 1fr; }}
    .story-grid {{ grid-template-columns: 1fr; }}
    .chart-wrap, .chart-wrap.tall {{ height: 320px; }}
    table.summary, table.summary thead, table.summary tbody, table.summary tr, table.summary td, table.summary th {{
      display: block;
    }}
    table.summary thead {{ display: none; }}
    table.summary tr {{
      padding: 14px 0;
      border-bottom: 1px solid rgba(135, 178, 255, 0.1);
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
  <div class="eyebrow">Redis Replay · 15000 Items · Runtime Only</div>
  <h1>{header_title}</h1>
  <p>
    {header_description} This dashboard reads
    <code>{results_relpath}</code>. Row 1 is the THP-disabled
    <code>base_pages</code> baseline, row 2 is the intact-THP
    <code>no_break</code> row, the next five rows split the hottest keys one
    at a time, and the final two rows split random non-hot keys one at a time.
  </p>
  <div class="notice">
    This page is runtime-only. It is the Test 1 read-heavy result set with the
    same visual structure used by the newer base-pages dashboards.
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
  <div class="card full-width">
    <h2>Section 1 — Runtime Overview</h2>
    <p class="desc">
      Percentage runtime delta for each of the 9 replay rows relative to
      <code>no_break</code>. <span class="chip red">Red</span> marks
      <code>base_pages</code>, <span class="chip blue">blue</span> marks
      <code>no_break</code>, <span class="chip gold">gold</span> are the hottest
      split-only keys, and <span class="chip teal">teal</span> are the random split-only keys.
    </p>
    <div class="chart-wrap"><canvas id="runtimeChart"></canvas></div>
    <div id="runtimeError"></div>
  </div>

  <div class="card">
    <h2>Section 2 — Runtime Change vs Access Count</h2>
    <p class="desc">
      Each dot is one split-only-key row. The X-axis is accepted-trace access count, and the Y-axis is
      runtime change (%) relative to <code>no_break</code>.
    </p>
    <div class="chart-wrap"><canvas id="scatterChart"></canvas></div>
    <div id="scatterError"></div>
  </div>

  <div class="card">
    <h2>Section 3 — Grouped Runtime View</h2>
    <p class="desc">
      Group means for <code>base_pages</code>, <code>no_break</code>, the five hot split rows,
      and the two random split rows.
    </p>
    <div class="chart-wrap"><canvas id="groupChart"></canvas></div>
    <div id="groupError"></div>
  </div>

  <div class="card full-width">
    <h2>Section 4 — Split-Row Access Counts</h2>
    <p class="desc">
      Accepted-trace access counts for the seven split rows in this result set.
      This keeps the hot-key ordering visible beside the runtime changes.
    </p>
    <div class="chart-wrap"><canvas id="accessChart"></canvas></div>
    <div id="accessError"></div>
  </div>

  <div class="card full-width">
    <h2>Section 5 — Important Insights</h2>
    <p class="desc">
      High-level runtime takeaways from this accepted replay run.
    </p>
    <div class="story-grid" id="storyGrid"></div>
  </div>

  <div class="card full-width">
    <h2>Section 6 — Row Summary</h2>
    <p class="desc">
      Full per-row runtime samples and accepted-trace access counts.
    </p>
    <div id="summaryTableWrap"></div>
    <div id="summaryError"></div>
  </div>
</section>
</div>

<script id="readmixMetricsData" type="application/json">
{{}}
</script>
<script id="readmixRowsData" type="application/json">
[]
</script>
<script>
Chart.defaults.color = '#a0a0b0';
Chart.defaults.borderColor = 'rgba(255,255,255,0.07)';
Chart.defaults.font.family = \"'Segoe UI', system-ui, sans-serif\";

const metrics = JSON.parse(document.getElementById('readmixMetricsData').textContent);
const rows = JSON.parse(document.getElementById('readmixRowsData').textContent);

function formatSeconds(value) {{
  return `${{value.toFixed(3)}}s`;
}}

function formatPercent(value) {{
  return `${{value >= 0 ? '+' : ''}}${{value.toFixed(2)}}%`;
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
const hotRuntimeMean = hotRows.reduce((sum, row) => sum + row.runtime_mean, 0) / hotRows.length;
const randomRuntimeMean = randomRows.reduce((sum, row) => sum + row.runtime_mean, 0) / randomRows.length;

document.getElementById('basePagesRuntimeValue').textContent = formatSeconds(metrics.base_pages_runtime_s);
document.getElementById('basePagesRuntimeDetail').innerHTML =
  `<span class="chip ${{metrics.base_pages_pct_vs_no_break > 0 ? 'red' : 'teal'}}">${{formatPercent(metrics.base_pages_pct_vs_no_break)}}</span> versus <code>no_break</code>`;
document.getElementById('hotSplitValue').textContent = formatPercent(metrics.hot_split_mean_runtime_pct);
document.getElementById('hotSplitDetail').textContent =
  `Mean runtime delta across ${{metrics.hot_rows}} hottest split-only rows.`;
document.getElementById('randomSplitValue').textContent = formatPercent(metrics.random_split_mean_runtime_pct);
document.getElementById('randomSplitDetail').textContent =
  `Mean runtime delta across ${{metrics.random_rows}} random split-only rows.`;
document.getElementById('runtimeBandValue').textContent =
  `${{metrics.runtime_min_s.toFixed(1)}}s-${{metrics.runtime_max_s.toFixed(1)}}s`;
document.getElementById('runtimeBandDetail').textContent =
  `Across ${{metrics.timed_runs}} timed runs from the runtime-only pass.`;

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
      data: [{{ x: row.access_count, y: row.runtime_pct_vs_no_break }}],
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
          label: (ctx) => `${{ctx.dataset.label}}: ${{ctx.raw.x.toLocaleString()}} accesses, ${{formatPercent(ctx.raw.y)}} vs no_break`,
        }}
      }}
    }},
    scales: {{
      x: {{
        title: {{ display: true, text: 'Accepted-trace access count', color: '#e0e0e0' }},
        ticks: {{ color: '#e0e0e0' }},
      }},
      y: {{
        title: {{ display: true, text: 'Runtime delta vs no_break (%)', color: '#e0e0e0' }},
        ticks: {{
          color: '#e0e0e0',
          callback: (value) => `${{value}}%`
        }}
      }}
    }}
  }}
}});

new Chart(document.getElementById('groupChart'), {{
  type: 'bar',
  data: {{
    labels: ['base_pages', 'no_break', 'hot split mean', 'random split mean'],
    datasets: [{{
      label: 'Mean runtime (s)',
      data: [
        metrics.base_pages_runtime_s,
        metrics.no_break_runtime_s,
        hotRuntimeMean,
        randomRuntimeMean,
      ],
      backgroundColor: ['#e94560', '#4f8cff', '#f5a623', '#53c9b1'],
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: (ctx) => `${{formatSeconds(ctx.raw)}}`
        }}
      }}
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#e0e0e0' }},
        grid: {{ display: false }}
      }},
      y: {{
        ticks: {{
          color: '#e0e0e0',
          callback: (value) => `${{value}}s`
        }}
      }}
    }}
  }}
}});

new Chart(document.getElementById('accessChart'), {{
  type: 'bar',
  data: {{
    labels: splitRows.map((row) => row.short_label),
    datasets: [{{
      label: 'Accepted-trace access count',
      data: splitRows.map((row) => row.access_count),
      backgroundColor: splitRows.map(rowColor),
      borderColor: 'rgba(255,255,255,0.18)',
      borderWidth: 1.0,
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: (ctx) => `${{ctx.raw.toLocaleString()}} accepted-trace accesses`,
        }}
      }}
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#e0e0e0' }},
        grid: {{ display: false }}
      }},
      y: {{
        ticks: {{
          color: '#e0e0e0',
          callback: (value) => Number(value).toLocaleString(),
        }}
      }}
    }}
  }}
}});

const storyCards = [
  {{
    title: 'base_pages vs no_break',
    body: `base_pages averaged ${{formatSeconds(metrics.base_pages_runtime_s)}} and was ${{formatPercent(metrics.base_pages_pct_vs_no_break)}} versus no_break.`,
  }},
  {{
    title: 'Hot split average',
    body: `The five hot split rows averaged ${{formatPercent(metrics.hot_split_mean_runtime_pct)}} versus no_break.`,
  }},
  {{
    title: 'Random split average',
    body: `The two random split rows averaged ${{formatPercent(metrics.random_split_mean_runtime_pct)}} versus no_break.`,
  }},
  {{
    title: 'Runtime band',
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
        <th>Mean Runtime</th>
        <th>Delta vs no_break</th>
        <th>Samples</th>
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
            <td data-label="Mean Runtime">${{formatSeconds(row.runtime_mean)}}</td>
            <td data-label="Delta vs no_break">${{formatPercent(row.runtime_pct_vs_no_break)}}</td>
            <td data-label="Samples" class="mono">${{row.runtime_samples.map(formatSeconds).join(', ')}}</td>
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
    """Write HTML, JSON, and markdown summary artifacts."""

    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    rows_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

    html = _build_dashboard_html(
        title=title,
        header_title=header_title,
        header_description=header_description,
        results_relpath=str(results_path.relative_to(REPO_ROOT)),
    )
    html = _replace_inline_json_script(html, "readmixMetricsData", metrics)
    html = _replace_inline_json_script(html, "readmixRowsData", records)
    html_path.write_text(html, encoding="utf-8")

    hot_rows = [record for record in records if record["row_type"] == "hot_split_only"]
    random_rows = [record for record in records if record["row_type"] == "random_split_only"]
    slowest = sorted(
        hot_rows + random_rows,
        key=lambda record: float(record["runtime_delta_vs_no_break"]),
        reverse=True,
    )[:5]

    lines = [
        f"# {header_title}",
        "",
        f"Source parquet: `{results_path.relative_to(REPO_ROOT)}`",
        "",
        "## Important Takeaways",
        "",
        f"- `base_pages` averaged `{metrics['base_pages_runtime_s']:.3f}s` and `no_break` averaged `{metrics['no_break_runtime_s']:.3f}s`.",
        f"- `base_pages` was `{metrics['base_pages_pct_vs_no_break']:+.2f}%` versus `no_break`.",
        f"- The `5` hot split-only rows averaged `{metrics['hot_split_mean_runtime_pct']:+.2f}%` versus `no_break`.",
        f"- The `2` random split-only rows averaged `{metrics['random_split_mean_runtime_pct']:+.2f}%` versus `no_break`.",
        f"- Timed runs ranged from `{metrics['runtime_min_s']:.3f}s` to `{metrics['runtime_max_s']:.3f}s`.",
        "",
        "## Slowest Split Rows",
        "",
    ]
    for record in slowest:
        lines.append(
            f"- `{record['short_label']}` = `{record['key']}` with `{record['access_count']}` accesses, "
            f"runtime delta `{record['runtime_pct_vs_no_break']:+.2f}%`."
        )
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a runtime-only dashboard for a 15000-item read-mix replay run.",
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
    parser.add_argument("--hot-keys", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Generate the dashboard and supporting files."""

    args = _build_parser().parse_args(argv)
    args.results = args.results.resolve()
    args.trace = args.trace.resolve()
    args.output_html = args.output_html.resolve()
    args.output_metrics = args.output_metrics.resolve()
    args.output_rows = args.output_rows.resolve()
    args.output_summary = args.output_summary.resolve()
    args.output_html.parent.mkdir(parents=True, exist_ok=True)

    records = _classify_rows(
        pl.read_parquet(args.results),
        trace_path=args.trace,
        hot_key_rows=args.hot_keys,
    )
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
