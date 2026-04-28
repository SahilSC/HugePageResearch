#!/usr/bin/env python3
"""Render outer-iteration YCSB runtime charts for one runtime-search candidate.

This helper reads the runtime-search manifest for
``r45_u45_d5_i5_zipfian_largevar`` and writes a small HTML dashboard that
shows completed YCSB load/run runtimes by outer iteration.

The manifest does not store a separate outer-iteration field for this
candidate. We therefore fail fast and define outer iteration as the execution
order of completed manifest rows sorted by ``timestamp_utc``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from pathlib import Path


REPO_ROOT = Path("/users/SahilSC/HugePageResearch")
MANIFEST_PATH = (
    REPO_ROOT
    / "data/redis_thp_replication/runtime_search/20260412T201057Z/runtime_search_manifest.json"
)
OUTPUT_PATH = (
    REPO_ROOT
    / "temp_data_analysis/r45_u45_d5_i5_zipfian_largevar_outer_iterations.html"
)
CANDIDATE_NAME = "r45_u45_d5_i5_zipfian_largevar"

COLORS = {
    "always": "#1f77b4",
    "never": "#d62728",
}


@dataclass(frozen=True)
class OuterIterationRow:
    """One completed manifest row shown as an outer iteration.

    Attributes:
        outer_iteration: Execution-order index starting at 1.
        timestamp_utc: Completion timestamp copied from the manifest.
        thp_mode: THP mode for this run.
        load_ycsb_s: Final YCSB load runtime in seconds.
        run_ycsb_s: Final YCSB run runtime in seconds.
        total_ycsb_s: Sum of load and run YCSB runtime in seconds.
    """

    outer_iteration: int
    timestamp_utc: str
    thp_mode: str
    load_ycsb_s: float
    run_ycsb_s: float
    total_ycsb_s: float


def load_rows() -> list[OuterIterationRow]:
    """Load completed candidate rows from the runtime-search manifest.

    Returns:
        Completed rows for the target candidate sorted by ``timestamp_utc``.

    Raises:
        RuntimeError: The manifest is missing or there are no completed rows.
    """

    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"missing manifest: {MANIFEST_PATH}")

    manifest = json.loads(MANIFEST_PATH.read_text())
    raw_rows = [
        row
        for row in manifest.get("mode_results", [])
        if row.get("candidate_name") == CANDIDATE_NAME and row.get("status") == "completed"
    ]
    if not raw_rows:
        raise RuntimeError(f"no completed rows found for {CANDIDATE_NAME}")

    raw_rows.sort(key=lambda row: row["timestamp_utc"])
    rows: list[OuterIterationRow] = []
    for outer_iteration, row in enumerate(raw_rows, start=1):
        rows.append(
            OuterIterationRow(
                outer_iteration=outer_iteration,
                timestamp_utc=str(row["timestamp_utc"]),
                thp_mode=str(row["thp_mode"]),
                load_ycsb_s=float(row["load_ycsb_s"]),
                run_ycsb_s=float(row["run_ycsb_s"]),
                total_ycsb_s=float(row["load_plus_run_ycsb_s"]),
            )
        )
    return rows


def render_bar_chart(
    rows: list[OuterIterationRow],
    *,
    metric_name: str,
    value_getter: callable,
    title: str,
    subtitle: str,
    width: int = 1080,
    height: int = 360,
) -> str:
    """Render one SVG bar chart for a selected runtime metric."""

    margin_left = 84
    margin_right = 24
    margin_top = 54
    margin_bottom = 96
    inner_width = width - margin_left - margin_right
    inner_height = height - margin_top - margin_bottom

    values = [value_getter(row) for row in rows]
    max_value = max(values)
    y_max = max_value * 1.12 if max_value > 0 else 1.0

    def sy(value: float) -> float:
        return margin_top + inner_height - ((value / y_max) * inner_height)

    grid_parts: list[str] = []
    for tick_index in range(6):
        tick_value = y_max * tick_index / 5.0
        y = sy(tick_value)
        grid_parts.append(
            f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" '
            f'y2="{y:.1f}" stroke="#d9e0ea" stroke-width="1" />'
        )
        grid_parts.append(
            f'<text x="{margin_left - 12}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="12" fill="#425466">{tick_value:.1f}s</text>'
        )

    bar_gap = 40
    bar_width = min(150, (inner_width - bar_gap * (len(rows) - 1)) / len(rows))
    start_x = margin_left + (
        inner_width - (len(rows) * bar_width + (len(rows) - 1) * bar_gap)
    ) / 2.0

    bar_parts: list[str] = []
    for index, row in enumerate(rows):
        value = value_getter(row)
        x = start_x + index * (bar_width + bar_gap)
        y = sy(value)
        bar_height = margin_top + inner_height - y
        color = COLORS.get(row.thp_mode, "#6b7280")
        tooltip = (
            f"outer_iteration={row.outer_iteration}, thp_mode={row.thp_mode}, "
            f"{metric_name}={value:.3f}s, timestamp_utc={row.timestamp_utc}"
        )
        bar_parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
            f'height="{bar_height:.1f}" rx="12" fill="{color}" opacity="0.90">'
            f"<title>{escape(tooltip)}</title></rect>"
        )
        bar_parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{y - 8:.1f}" text-anchor="middle" '
            f'font-size="12" font-weight="700" fill="#16212b">{value:.3f}s</text>'
        )
        bar_parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{height - margin_bottom + 20:.1f}" '
            f'text-anchor="middle" font-size="12" font-weight="700" fill="#233444">'
            f"outer {row.outer_iteration}</text>"
        )
        bar_parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{height - margin_bottom + 39:.1f}" '
            f'text-anchor="middle" font-size="11" fill="#516273">{escape(row.thp_mode)}</text>'
        )
        bar_parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{height - margin_bottom + 58:.1f}" '
            f'text-anchor="middle" font-size="11" fill="#516273">{escape(row.timestamp_utc)}</text>'
        )

    return f"""
    <section class="card">
      <h2>{escape(title)}</h2>
      <p class="subtitle">{escape(subtitle)}</p>
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">
        <rect x="0" y="0" width="{width}" height="{height}" rx="18" fill="#ffffff" />
        <text x="{width / 2:.1f}" y="24" text-anchor="middle" font-size="14" font-weight="700" fill="#1f2a36">
          {escape(title)}
        </text>
        <text x="{width / 2:.1f}" y="42" text-anchor="middle" font-size="12" fill="#5e6c7b">
          Outer iteration is the execution-order index for completed manifest rows.
        </text>
        {''.join(grid_parts)}
        <line x1="{margin_left}" y1="{height - margin_bottom}" x2="{width - margin_right}"
              y2="{height - margin_bottom}" stroke="#3a4856" stroke-width="1.4" />
        <line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}"
              y2="{height - margin_bottom}" stroke="#3a4856" stroke-width="1.4" />
        {''.join(bar_parts)}
        <text x="{width / 2:.1f}" y="{height - 16}" text-anchor="middle" font-size="12" fill="#425466">
          Outer iteration
        </text>
        <text transform="translate(18 {height / 2:.1f}) rotate(-90)" text-anchor="middle"
              font-size="12" fill="#425466">
          {escape(metric_name)} in seconds
        </text>
      </svg>
    </section>
    """


def render_table(rows: list[OuterIterationRow]) -> str:
    """Render one HTML table for the completed outer-iteration rows."""

    table_rows = []
    for row in rows:
        table_rows.append(
            "<tr>"
            f"<td>{row.outer_iteration}</td>"
            f'<td><span class="pill pill-{escape(row.thp_mode)}">{escape(row.thp_mode)}</span></td>'
            f"<td>{escape(row.timestamp_utc)}</td>"
            f"<td>{row.load_ycsb_s:.3f}</td>"
            f"<td>{row.run_ycsb_s:.3f}</td>"
            f"<td>{row.total_ycsb_s:.3f}</td>"
            "</tr>"
        )
    return (
        '<section class="card"><h2>Completed outer iterations</h2>'
        '<p class="subtitle">These rows come from '
        '<code>runtime_search_manifest.json</code> for the target candidate.</p>'
        '<table><thead><tr>'
        '<th>Outer Iteration</th><th>THP Mode</th><th>Timestamp UTC</th>'
        '<th>Load YCSB (s)</th><th>Run YCSB (s)</th><th>Total YCSB (s)</th>'
        "</tr></thead><tbody>"
        + "".join(table_rows)
        + "</tbody></table></section>"
    )


def render_html(rows: list[OuterIterationRow]) -> str:
    """Render the full outer-iteration dashboard."""

    latest_always = next(row for row in reversed(rows) if row.thp_mode == "always")
    latest_never = next(row for row in reversed(rows) if row.thp_mode == "never")
    run_delta = latest_never.run_ycsb_s - latest_always.run_ycsb_s

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{escape(CANDIDATE_NAME)} outer iterations</title>
  <style>
    :root {{
      --bg: #f4f6f9;
      --ink: #1b2733;
      --muted: #5e6c7b;
      --border: #d8e0ea;
      --card: #ffffff;
      --always: #1f77b4;
      --never: #d62728;
      --accent: #0f8b8d;
    }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15, 139, 141, 0.10), transparent 34%),
        linear-gradient(180deg, #f8fafc 0%, var(--bg) 100%);
      font-family: Georgia, "Times New Roman", serif;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 22px 48px;
    }}
    header {{
      background: linear-gradient(135deg, #102a43 0%, #1f4b6e 55%, #0f8b8d 100%);
      color: #f8fbff;
      border-radius: 24px;
      padding: 28px 30px;
      box-shadow: 0 24px 70px rgba(16, 42, 67, 0.22);
      margin-bottom: 24px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 34px;
      line-height: 1.1;
      letter-spacing: -0.02em;
    }}
    header p {{
      margin: 6px 0;
      max-width: 920px;
      color: rgba(248, 251, 255, 0.92);
      line-height: 1.45;
      font-size: 16px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
      margin-bottom: 20px;
    }}
    .metric, .card {{
      background: var(--card);
      border-radius: 20px;
      border: 1px solid rgba(216, 224, 234, 0.85);
      box-shadow: 0 18px 48px rgba(26, 38, 53, 0.08);
    }}
    .metric {{
      padding: 16px 18px;
    }}
    .metric-label {{
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
      font-family: "Helvetica Neue", Arial, sans-serif;
    }}
    .metric-value {{
      font-size: 28px;
      font-weight: 700;
      font-family: "Helvetica Neue", Arial, sans-serif;
    }}
    .metric-sub {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
      font-family: "Helvetica Neue", Arial, sans-serif;
    }}
    .card {{
      padding: 18px;
      margin-top: 18px;
    }}
    .card h2 {{
      margin: 0 0 4px;
      font-size: 22px;
    }}
    .subtitle {{
      margin: 0 0 14px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
      font-family: "Helvetica Neue", Arial, sans-serif;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-family: "Helvetica Neue", Arial, sans-serif;
      font-size: 14px;
    }}
    th, td {{
      padding: 12px 10px;
      text-align: left;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }}
    th {{
      font-size: 12px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
      background: #f7fafc;
    }}
    .pill {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      color: white;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.02em;
    }}
    .pill-always {{ background: var(--always); }}
    .pill-never {{ background: var(--never); }}
    code {{
      background: #edf2f7;
      border-radius: 6px;
      padding: 2px 6px;
      font-size: 0.95em;
      font-family: "SFMono-Regular", Consolas, monospace;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{escape(CANDIDATE_NAME)} outer-iteration runtimes</h1>
      <p>This dashboard uses completed manifest rows for the candidate and puts
      outer iteration on the x-axis. Here, outer iteration means execution order
      after sorting completed rows by <code>timestamp_utc</code>.</p>
      <p>Manifest source: <code>{escape(str(MANIFEST_PATH))}</code></p>
    </header>

    <div class="metrics">
      <div class="metric">
        <div class="metric-label">Completed Outer Iterations</div>
        <div class="metric-value">{len(rows)}</div>
        <div class="metric-sub">There are two completed <code>always</code> runs and one completed <code>never</code> run for this candidate.</div>
      </div>
      <div class="metric">
        <div class="metric-label">Latest Always Run</div>
        <div class="metric-value">{latest_always.run_ycsb_s:.3f}s</div>
        <div class="metric-sub">Outer iteration {latest_always.outer_iteration}, timestamp {escape(latest_always.timestamp_utc)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Never Run</div>
        <div class="metric-value">{latest_never.run_ycsb_s:.3f}s</div>
        <div class="metric-sub">Outer iteration {latest_never.outer_iteration}, timestamp {escape(latest_never.timestamp_utc)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Never - Latest Always</div>
        <div class="metric-value">{run_delta:.3f}s</div>
        <div class="metric-sub">Positive means the completed <code>never</code> run took longer.</div>
      </div>
    </div>

    {render_bar_chart(
        rows,
        metric_name="YCSB run runtime",
        value_getter=lambda row: row.run_ycsb_s,
        title="YCSB run runtime by outer iteration",
        subtitle="This is the final [OVERALL], RunTime(ms) from the YCSB run phase.",
    )}

    {render_bar_chart(
        rows,
        metric_name="YCSB load runtime",
        value_getter=lambda row: row.load_ycsb_s,
        title="YCSB load runtime by outer iteration",
        subtitle="Same outer-iteration view, but for the YCSB load phase.",
    )}

    {render_bar_chart(
        rows,
        metric_name="YCSB total runtime",
        value_getter=lambda row: row.total_ycsb_s,
        title="YCSB load + run runtime by outer iteration",
        subtitle="Combined YCSB load and run runtime for each completed outer iteration.",
    )}

    {render_table(rows)}
  </main>
</body>
</html>
"""


def main() -> None:
    """Render the outer-iteration dashboard to the temp analysis directory."""

    rows = load_rows()
    OUTPUT_PATH.write_text(render_html(rows))
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
