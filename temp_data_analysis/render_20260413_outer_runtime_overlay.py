#!/usr/bin/env python3
"""Render overlaid outer-iteration YCSB runtime charts for two redis logs.

This script reads two curated ``redis_benchmark.log`` files, parses the YCSB
``[OVERALL], RunTime(ms)`` entries for each load and run phase, and writes one
HTML dashboard with three overlaid charts:

1. Total YCSB runtime by outer iteration.
2. Load-only YCSB runtime by outer iteration.
3. Run-only YCSB runtime by outer iteration.

The target logs intentionally have different ``outer_repeat`` counts. The x-axis
therefore spans the larger outer-repeat budget and the shorter series ends early
without inventing extra points.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path


REPO_ROOT = Path("/users/SahilSC/HugePageResearch")
DEFAULT_ALWAYS_ID = "20260413T021419067720"
DEFAULT_NEVER_ID = "20260413T013901313762"

THP_MODE_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+transparent_hugepages:\s+(\w+)$")
OUTER_REPEAT_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+outer_repeat:\s+(\d+)$")
REPEAT_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+repeat:\s+(\d+)$")
LOAD_START_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+Load phase out_i=(\d+) starting")
RUN_START_RE = re.compile(
    r"^\[\d{2}:\d{2}:\d{2}\]\s+Run phase out_i=(\d+)\s+i=(\d+)\s+starting"
)
YCSB_RUNTIME_RE = re.compile(r"^\[OVERALL\], RunTime\(ms\), (\d+)$")

SERIES_COLORS = {
    "always": "#1f77b4",
    "never": "#d62728",
}


@dataclass(frozen=True)
class OuterRuntimeSeries:
    """Parsed YCSB runtime series for one collection.

    Attributes:
        collection_id: Curated collection directory name.
        thp_mode: Parsed THP mode from the collection YAML section.
        outer_repeat: Outer-iteration count declared in the log config.
        load_runtime_ms: YCSB load ``RunTime(ms)`` per outer iteration.
        run_runtime_ms: YCSB run ``RunTime(ms)`` per outer iteration.
    """

    collection_id: str
    thp_mode: str
    outer_repeat: int
    load_runtime_ms: list[int]
    run_runtime_ms: list[int]

    @property
    def total_runtime_ms(self) -> list[int]:
        """Return per-outer-iteration load + run YCSB runtime in milliseconds."""

        return [
            load_ms + run_ms
            for load_ms, run_ms in zip(self.load_runtime_ms, self.run_runtime_ms, strict=True)
        ]


def benchmark_log_path(collection_id: str) -> Path:
    """Return the curated redis benchmark log path for one collection ID.

    Args:
        collection_id: Curated redis collection directory name.

    Returns:
        Absolute path to the matching ``redis_benchmark.log`` file.
    """

    return REPO_ROOT / f"data/curated/redis/{collection_id}/redis_benchmark.log"


def parse_benchmark_log(*, collection_id: str, log_path: Path) -> OuterRuntimeSeries:
    """Parse one curated redis benchmark log.

    The parser is intentionally strict for this task: it requires ``repeat: 1``
    and exactly one load/runtime YCSB ``RunTime(ms)`` entry for each outer
    iteration.

    Args:
        collection_id: Human-readable collection identifier used in labels.
        log_path: Path to the raw ``redis_benchmark.log`` file.

    Returns:
        Parsed load/run runtime series keyed by outer iteration.

    Raises:
        RuntimeError: The log shape does not match the expected single-repeat
            benchmark format.
    """

    if not log_path.exists():
        raise RuntimeError(f"missing log: {log_path}")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    thp_mode: str | None = None
    outer_repeat: int | None = None
    repeat_count: int | None = None
    load_runtime_by_outer: dict[int, int] = {}
    run_runtime_by_outer: dict[int, int] = {}
    current_phase: tuple[str, int] | None = None

    for line in lines:
        if thp_mode is None:
            thp_match = THP_MODE_RE.match(line)
            if thp_match:
                thp_mode = thp_match.group(1)
                continue
        if outer_repeat is None:
            outer_match = OUTER_REPEAT_RE.match(line)
            if outer_match:
                outer_repeat = int(outer_match.group(1))
                continue
        if repeat_count is None:
            repeat_match = REPEAT_RE.match(line)
            if repeat_match:
                repeat_count = int(repeat_match.group(1))
                continue

        load_match = LOAD_START_RE.match(line)
        if load_match:
            current_phase = ("load", int(load_match.group(1)))
            continue

        run_match = RUN_START_RE.match(line)
        if run_match:
            repeat_index = int(run_match.group(2))
            if repeat_index != 0:
                raise RuntimeError(
                    f"{collection_id} expected repeat index 0, found {repeat_index}"
                )
            current_phase = ("run", int(run_match.group(1)))
            continue

        runtime_match = YCSB_RUNTIME_RE.match(line)
        if not runtime_match:
            continue

        if current_phase is None:
            raise RuntimeError(
                f"{collection_id} found YCSB runtime outside a load/run phase"
            )

        runtime_ms = int(runtime_match.group(1))
        phase_name, outer_index = current_phase
        if phase_name == "load":
            if outer_index in load_runtime_by_outer:
                raise RuntimeError(
                    f"{collection_id} duplicated load runtime for outer iteration {outer_index}"
                )
            load_runtime_by_outer[outer_index] = runtime_ms
        else:
            if outer_index in run_runtime_by_outer:
                raise RuntimeError(
                    f"{collection_id} duplicated run runtime for outer iteration {outer_index}"
                )
            run_runtime_by_outer[outer_index] = runtime_ms
        current_phase = None

    if thp_mode is None:
        raise RuntimeError(f"{collection_id} missing transparent_hugepages field")
    if outer_repeat is None:
        raise RuntimeError(f"{collection_id} missing outer_repeat field")
    if repeat_count is None:
        raise RuntimeError(f"{collection_id} missing repeat field")
    if repeat_count != 1:
        raise RuntimeError(
            f"{collection_id} expected repeat=1 for per-outer plots, found {repeat_count}"
        )

    expected_outer = list(range(outer_repeat))
    missing_load = [outer for outer in expected_outer if outer not in load_runtime_by_outer]
    missing_run = [outer for outer in expected_outer if outer not in run_runtime_by_outer]
    if missing_load:
        raise RuntimeError(
            f"{collection_id} missing load runtimes for outer iterations {missing_load}"
        )
    if missing_run:
        raise RuntimeError(
            f"{collection_id} missing run runtimes for outer iterations {missing_run}"
        )

    return OuterRuntimeSeries(
        collection_id=collection_id,
        thp_mode=thp_mode,
        outer_repeat=outer_repeat,
        load_runtime_ms=[load_runtime_by_outer[outer] for outer in expected_outer],
        run_runtime_ms=[run_runtime_by_outer[outer] for outer in expected_outer],
    )


def _format_seconds(runtime_ms: int) -> str:
    """Format milliseconds as a short seconds string."""

    return f"{runtime_ms / 1000.0:.3f}s"


def render_overlay_chart(
    *,
    title: str,
    subtitle: str,
    metric_name: str,
    max_outer_repeat: int,
    series_values_ms: dict[str, list[int]],
    width: int = 1080,
    height: int = 380,
) -> str:
    """Render one overlaid SVG line chart."""

    margin_left = 84
    margin_right = 28
    margin_top = 56
    margin_bottom = 76
    inner_width = width - margin_left - margin_right
    inner_height = height - margin_top - margin_bottom

    all_values = [value for values in series_values_ms.values() for value in values]
    y_min = 0.0
    y_max = max(all_values) / 1000.0 if all_values else 1.0
    y_max *= 1.08

    def sx(iteration_index: int) -> float:
        if max_outer_repeat == 1:
            return margin_left + inner_width / 2.0
        return margin_left + ((iteration_index - 1) / (max_outer_repeat - 1)) * inner_width

    def sy(value_ms: int) -> float:
        value_s = value_ms / 1000.0
        return margin_top + inner_height - ((value_s - y_min) / (y_max - y_min)) * inner_height

    grid_parts: list[str] = []
    for tick_index in range(6):
        tick_value = y_min + ((y_max - y_min) * tick_index / 5.0)
        y = margin_top + inner_height - ((tick_value - y_min) / (y_max - y_min)) * inner_height
        grid_parts.append(
            f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" '
            f'y2="{y:.1f}" stroke="#d9e0ea" stroke-width="1" />'
        )
        grid_parts.append(
            f'<text x="{margin_left - 12}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="12" fill="#425466">{tick_value:.1f}s</text>'
        )

    for iteration_index in range(1, max_outer_repeat + 1):
        x = sx(iteration_index)
        grid_parts.append(
            f'<line x1="{x:.1f}" y1="{margin_top}" x2="{x:.1f}" y2="{height - margin_bottom}" '
            f'stroke="#eef2f7" stroke-width="1" />'
        )
        grid_parts.append(
            f'<text x="{x:.1f}" y="{height - margin_bottom + 22:.1f}" text-anchor="middle" '
            f'font-size="11" fill="#516273">{iteration_index}</text>'
        )

    series_parts: list[str] = []
    legend_parts: list[str] = []
    for legend_index, (label, values_ms) in enumerate(series_values_ms.items()):
        color = SERIES_COLORS[label]
        points = [(outer_index + 1, values_ms[outer_index]) for outer_index in range(len(values_ms))]
        path_d = " ".join(
            ("M" if point_index == 0 else "L")
            + f" {sx(iteration_index):.1f} {sy(value_ms):.1f}"
            for point_index, (iteration_index, value_ms) in enumerate(points)
        )
        series_parts.append(
            f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="3" '
            f'stroke-linecap="round" stroke-linejoin="round" />'
        )
        for iteration_index, value_ms in points:
            tooltip = (
                f"mode={label}, outer_iteration={iteration_index}, "
                f"{metric_name}={_format_seconds(value_ms)}"
            )
            series_parts.append(
                f'<circle cx="{sx(iteration_index):.1f}" cy="{sy(value_ms):.1f}" '
                f'r="4.2" fill="{color}" stroke="#ffffff" stroke-width="1.2">'
                f"<title>{escape(tooltip)}</title></circle>"
            )
        last_iteration, last_value = points[-1]
        label_x = min(width - margin_right - 6, sx(last_iteration) + 10)
        label_y = sy(last_value) - 10 - legend_index * 14
        series_parts.append(
            f'<text x="{label_x:.1f}" y="{label_y:.1f}" font-size="12" '
            f'font-weight="700" fill="{color}">{escape(label)} {_format_seconds(last_value)}</text>'
        )
        legend_y = 22 + legend_index * 20
        legend_parts.append(
            f'<rect x="{margin_left + 8}" y="{legend_y - 10}" width="14" height="14" '
            f'rx="3" fill="{color}" />'
            f'<text x="{margin_left + 30}" y="{legend_y + 1}" font-size="12" '
            f'fill="#243447">{escape(label)}</text>'
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
          Y-axis uses YCSB [OVERALL], RunTime(ms), converted to seconds.
        </text>
        {''.join(grid_parts)}
        <line x1="{margin_left}" y1="{height - margin_bottom}" x2="{width - margin_right}"
              y2="{height - margin_bottom}" stroke="#3a4856" stroke-width="1.4" />
        <line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}"
              y2="{height - margin_bottom}" stroke="#3a4856" stroke-width="1.4" />
        {''.join(series_parts)}
        {''.join(legend_parts)}
        <text x="{width / 2:.1f}" y="{height - 16}" text-anchor="middle" font-size="12" fill="#425466">
          Outer iteration
        </text>
        <text transform="translate(18 {height / 2:.1f}) rotate(-90)" text-anchor="middle"
              font-size="12" fill="#425466">
          {escape(metric_name)}
        </text>
      </svg>
    </section>
    """


def render_summary_table(series_list: list[OuterRuntimeSeries], max_outer_repeat: int) -> str:
    """Render one outer-iteration table for the overlaid series."""

    rows_html: list[str] = []
    for iteration_index in range(1, max_outer_repeat + 1):
        row_cells = [f"<td>{iteration_index}</td>"]
        for series in series_list:
            if iteration_index <= series.outer_repeat:
                load_ms = series.load_runtime_ms[iteration_index - 1]
                run_ms = series.run_runtime_ms[iteration_index - 1]
                total_ms = load_ms + run_ms
                row_cells.append(
                    f"<td>{_format_seconds(load_ms)}</td>"
                    f"<td>{_format_seconds(run_ms)}</td>"
                    f"<td>{_format_seconds(total_ms)}</td>"
                )
            else:
                row_cells.append("<td class=\"empty\">&mdash;</td>" * 3)
        rows_html.append("<tr>" + "".join(row_cells) + "</tr>")

    header_cells = ["<th>Outer Iteration</th>"]
    for series in series_list:
        label = f"{series.thp_mode} ({series.collection_id})"
        header_cells.extend(
            [
                f"<th>{escape(label)} load</th>",
                f"<th>{escape(label)} run</th>",
                f"<th>{escape(label)} total</th>",
            ]
        )

    return (
        '<section class="card"><h2>Parsed outer-iteration runtimes</h2>'
        '<p class="subtitle">Missing cells mean the shorter log does not have that outer iteration.</p>'
        '<table><thead><tr>'
        + "".join(header_cells)
        + "</tr></thead><tbody>"
        + "".join(rows_html)
        + "</tbody></table></section>"
    )


def render_html(series_list: list[OuterRuntimeSeries]) -> str:
    """Render the full overlaid runtime dashboard."""

    max_outer_repeat = max(series.outer_repeat for series in series_list)
    line_order = ["always", "never"]
    line_series = {series.thp_mode: series for series in series_list}
    missing_modes = [thp_mode for thp_mode in line_order if thp_mode not in line_series]
    if missing_modes:
        raise RuntimeError(f"missing expected THP modes: {missing_modes}")

    shorter_series = min(series_list, key=lambda series: series.outer_repeat)
    same_length = all(
        series.outer_repeat == line_series["always"].outer_repeat for series in series_list
    )
    if same_length:
        range_copy = (
            f"Both logs run through the same <code>outer_repeat={max_outer_repeat}</code> "
            "range, so every overlaid point shares the same x-axis extent."
        )
        shorter_behavior = "Both complete"
        shorter_behavior_copy = "Neither series ends early."
    else:
        range_copy = (
            f"The x-axis spans the longer <code>outer_repeat={max_outer_repeat}</code> run, "
            f"while the shorter <code>outer_repeat={shorter_series.outer_repeat}</code> run "
            "stops at its final observed point."
        )
        shorter_behavior = f"Stops at {shorter_series.outer_repeat}"
        shorter_behavior_copy = (
            "No synthetic points are added after the last observed outer iteration."
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Redis outer-iteration YCSB runtime overlay</title>
  <style>
    :root {{
      --bg: #f4f6f9;
      --ink: #1b2733;
      --muted: #5e6c7b;
      --border: #d8e0ea;
      --card: #ffffff;
      --always: #1f77b4;
      --never: #d62728;
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
      max-width: 940px;
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
    td.empty {{
      color: #98a3ad;
    }}
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
      <h1>YCSB outer-iteration runtime overlay</h1>
      <p>This report compares the curated Redis benchmark logs for
      <code>{line_series["always"].collection_id}</code> and
      <code>{line_series["never"].collection_id}</code>.
      {range_copy}</p>
      <p>The charts use only raw YCSB <code>[OVERALL], RunTime(ms)</code>
      values, converted to seconds.</p>
    </header>

    <div class="metrics">
      <div class="metric">
        <div class="metric-label">Always Collection</div>
        <div class="metric-value">{line_series["always"].collection_id}</div>
        <div class="metric-sub">outer_repeat={line_series["always"].outer_repeat}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Never Collection</div>
        <div class="metric-value">{line_series["never"].collection_id}</div>
        <div class="metric-sub">outer_repeat={line_series["never"].outer_repeat}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Longest X-Axis</div>
        <div class="metric-value">{max_outer_repeat}</div>
        <div class="metric-sub">Both overlays share this outer-iteration range.</div>
      </div>
      <div class="metric">
        <div class="metric-label">Shorter Series Behavior</div>
        <div class="metric-value">{shorter_behavior}</div>
        <div class="metric-sub">{shorter_behavior_copy}</div>
      </div>
    </div>

    {render_overlay_chart(
        title="Total YCSB runtime by outer iteration",
        subtitle="Per outer iteration, this is load runtime plus run runtime.",
        metric_name="Total YCSB runtime in seconds",
        max_outer_repeat=max_outer_repeat,
        series_values_ms={
            "always": line_series["always"].total_runtime_ms,
            "never": line_series["never"].total_runtime_ms,
        },
    )}

    {render_overlay_chart(
        title="Load YCSB runtime by outer iteration",
        subtitle="Per outer iteration, this is the YCSB load-phase [OVERALL] runtime.",
        metric_name="Load YCSB runtime in seconds",
        max_outer_repeat=max_outer_repeat,
        series_values_ms={
            "always": line_series["always"].load_runtime_ms,
            "never": line_series["never"].load_runtime_ms,
        },
    )}

    {render_overlay_chart(
        title="Run YCSB runtime by outer iteration",
        subtitle="Per outer iteration, this is the YCSB run-phase [OVERALL] runtime.",
        metric_name="Run YCSB runtime in seconds",
        max_outer_repeat=max_outer_repeat,
        series_values_ms={
            "always": line_series["always"].run_runtime_ms,
            "never": line_series["never"].run_runtime_ms,
        },
    )}

    {render_summary_table([line_series["always"], line_series["never"]], max_outer_repeat)}
  </main>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for selecting the overlay log pair.

    Returns:
        Parsed CLI arguments with ``always_id``, ``never_id``, and ``output``.
    """

    parser = argparse.ArgumentParser(
        description="Render overlaid YCSB runtime charts for one always/never redis pair."
    )
    parser.add_argument(
        "--always-id",
        default=DEFAULT_ALWAYS_ID,
        help="Collection ID for the THP always log.",
    )
    parser.add_argument(
        "--never-id",
        default=DEFAULT_NEVER_ID,
        help="Collection ID for the THP never log.",
    )
    parser.add_argument(
        "--output",
        help="Optional HTML output path. Defaults to temp_data_analysis/redis_outer_runtime_overlay_<always>_vs_<never>.html",
    )
    return parser.parse_args()


def default_output_path(always_id: str, never_id: str) -> Path:
    """Build the default HTML output path for one comparison pair.

    Args:
        always_id: Collection ID for the THP always run.
        never_id: Collection ID for the THP never run.

    Returns:
        HTML output path under ``temp_data_analysis``.
    """

    return (
        REPO_ROOT
        / f"temp_data_analysis/redis_outer_runtime_overlay_{always_id}_vs_{never_id}.html"
    )


def main() -> None:
    """Parse both logs and write the overlaid runtime dashboard."""

    args = parse_args()
    always_series = parse_benchmark_log(
        collection_id=args.always_id,
        log_path=benchmark_log_path(args.always_id),
    )
    never_series = parse_benchmark_log(
        collection_id=args.never_id,
        log_path=benchmark_log_path(args.never_id),
    )
    if always_series.thp_mode != "always":
        raise RuntimeError(
            f"{always_series.collection_id} is {always_series.thp_mode}, expected always"
        )
    if never_series.thp_mode != "never":
        raise RuntimeError(
            f"{never_series.collection_id} is {never_series.thp_mode}, expected never"
        )

    output_path = Path(args.output) if args.output else default_output_path(
        args.always_id,
        args.never_id,
    )
    output_path.write_text(render_html([always_series, never_series]), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
