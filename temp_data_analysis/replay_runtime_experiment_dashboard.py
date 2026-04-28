"""Render a runtime-only dashboard for one replay experiment.

This script reads one replay result parquet, reconstructs access counts from the
matching monitor log, and writes a standalone HTML dashboard plus compact JSON
and markdown summaries.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
from pathlib import Path
from typing import Any

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


def _stddev(values: list[float]) -> float:
    """Return sample standard deviation, or zero for one sample."""

    return statistics.stdev(values) if len(values) > 1 else 0.0


def _sorted_run_columns(df: pl.DataFrame, prefix: str) -> list[str]:
    """Return per-run columns sorted by numeric suffix.

    Args:
        df: Replay results dataframe.
        prefix: Column prefix such as ``runtime_s_``.

    Returns:
        Column names ordered by their run suffix.

    Raises:
        RuntimeError: If no matching columns exist.
    """

    columns = [column for column in df.columns if column.startswith(prefix)]
    if not columns:
        raise RuntimeError(f"Required replay column prefix not found: {prefix}")
    return sorted(columns, key=lambda column: int(column.removeprefix(prefix)))


def _mean(values: list[float]) -> float:
    """Return the arithmetic mean for a non-empty list."""

    if not values:
        raise RuntimeError("Expected at least one value when computing a mean")
    return sum(values) / len(values)


def _runtime_chart_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return rows that should appear in the main runtime chart.

    Base rows always stay visible. Split rows only appear after at least one
    replay-time split succeeded.
    """

    return [
        record
        for record in records
        if record["row_type"] in {"base_pages", "no_break"}
        or int(record["split_successes_total"]) > 0
    ]


def _group_runtime_stats(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    """Return aggregated runtime mean/std for a group of rows."""

    if not rows:
        return {"mean": None, "std": None}

    samples = [
        float(sample)
        for row in rows
        for sample in list(row["runtime_samples"])
    ]
    return {"mean": _mean(samples), "std": _stddev(samples)}


def _classify_rows(
    df: pl.DataFrame,
    trace_path: Path,
    hot_key_rows: int,
    runs: int,
) -> list[dict[str, Any]]:
    """Attach runtime and split metadata to each replay row.

    Args:
        df: Replay parquet loaded into memory.
        trace_path: Accepted monitor log for access-count reconstruction.
        hot_key_rows: Number of hottest split rows expected in the matrix.
        runs: Timed runs per replay row.

    Returns:
        One record per replay row with runtime and split metadata.
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

    runtime_cols = _sorted_run_columns(df, "runtime_s_")
    split_events_cols = _sorted_run_columns(df, "split_events_")
    split_success_cols = _sorted_run_columns(df, "split_successes_")
    split_failure_cols = _sorted_run_columns(df, "split_failures_")
    split_attempt_cols = _sorted_run_columns(df, "split_syscall_attempts_")
    split_max_attempt_cols = _sorted_run_columns(df, "split_max_attempts_")
    command_cols = _sorted_run_columns(df, "commands_replayed_")
    breakpoint_cols = [column for column in df.columns if column.startswith("user")]

    raw_records: list[dict[str, Any]] = []
    for row in df.iter_rows(named=True):
        breakpoints = {key: int(row[key]) for key in breakpoint_cols}
        zero_keys = [key for key, value in breakpoints.items() if value == 0]
        runtime_samples = [float(row[column]) for column in runtime_cols]
        command_samples = [int(row[column]) for column in command_cols]
        split_event_samples = [int(row[column]) for column in split_events_cols]
        split_success_samples = [int(row[column]) for column in split_success_cols]
        split_failure_samples = [int(row[column]) for column in split_failure_cols]
        split_attempt_samples = [int(row[column]) for column in split_attempt_cols]
        split_max_attempt_samples = [
            int(row[column]) for column in split_max_attempt_cols
        ]

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

        expected_split_events = runs if row_type in {
            "hot_split_only",
            "random_split_only",
        } else 0
        split_successes_total = sum(split_success_samples)
        split_failures_total = sum(split_failure_samples)
        split_events_total = sum(split_event_samples)

        raw_records.append(
            {
                "row_index": int(row.get("row_index", len(raw_records))),
                "thp_mode": str(row.get("thp_mode", "unknown")),
                "row_type": row_type,
                "key": key,
                "access_count": access_counts.get(key, 0) if key is not None else None,
                "runtime_samples": runtime_samples,
                "runtime_mean": _mean(runtime_samples),
                "runtime_std": _stddev(runtime_samples),
                "commands_replayed_samples": command_samples,
                "commands_replayed_total": sum(command_samples),
                "split_events_samples": split_event_samples,
                "split_events_total": split_events_total,
                "split_successes_samples": split_success_samples,
                "split_successes_total": split_successes_total,
                "split_failures_samples": split_failure_samples,
                "split_failures_total": split_failures_total,
                "split_syscall_attempts_samples": split_attempt_samples,
                "split_syscall_attempts_total": sum(split_attempt_samples),
                "split_max_attempts_samples": split_max_attempt_samples,
                "observed_max_attempts": max(split_max_attempt_samples, default=0),
                "expected_split_events": expected_split_events,
                "split_success_rate_pct": (
                    (split_successes_total / expected_split_events) * 100.0
                    if expected_split_events
                    else None
                ),
            }
        )

    no_break_runtime = next(
        float(record["runtime_mean"])
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
        record["short_label"] = (
            f"hot#{rank} ({record['split_successes_total']}/{record['expected_split_events']})"
        )
    for rank, record in enumerate(random_rows, start=1):
        record["hot_rank"] = None
        record["random_rank"] = rank
        record["short_label"] = (
            f"rand#{rank} ({record['split_successes_total']}/{record['expected_split_events']})"
        )

    ordered_records: list[dict[str, Any]] = []
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


def _mean_metric(rows: list[dict[str, Any]], field: str) -> float | None:
    """Return a mean across a record field, or ``None`` when no rows exist."""

    if not rows:
        return None
    return _mean([float(row[field]) for row in rows])


def _build_metrics(
    records: list[dict[str, Any]],
    results_path: Path,
    trace_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Build compact runtime-only metrics for the dashboard cards."""

    base_pages = next(record for record in records if record["row_type"] == "base_pages")
    no_break = next(record for record in records if record["row_type"] == "no_break")
    hot_rows = [record for record in records if record["row_type"] == "hot_split_only"]
    random_rows = [record for record in records if record["row_type"] == "random_split_only"]
    successful_hot_rows = [
        record for record in hot_rows if int(record["split_successes_total"]) > 0
    ]
    successful_random_rows = [
        record for record in random_rows if int(record["split_successes_total"]) > 0
    ]
    chart_rows = _runtime_chart_rows(records)
    all_runtime_samples = [
        float(sample)
        for record in records
        for sample in list(record["runtime_samples"])
    ]

    hot_expected = sum(int(record["expected_split_events"]) for record in hot_rows)
    random_expected = sum(int(record["expected_split_events"]) for record in random_rows)
    hot_success_total = sum(int(record["split_successes_total"]) for record in hot_rows)
    random_success_total = sum(
        int(record["split_successes_total"]) for record in random_rows
    )

    return {
        "experiment_name": manifest["experiment_name"],
        "results_path": str(results_path),
        "trace_path": str(trace_path),
        "workload_label": manifest["workload_label"],
        "rows": len(records),
        "chart_rows": len(chart_rows),
        "hot_rows": len(hot_rows),
        "random_rows": len(random_rows),
        "hot_rows_with_success": len(successful_hot_rows),
        "random_rows_with_success": len(successful_random_rows),
        "base_pages_runtime_s": float(base_pages["runtime_mean"]),
        "no_break_runtime_s": float(no_break["runtime_mean"]),
        "base_pages_pct_vs_no_break": float(base_pages["runtime_pct_vs_no_break"]),
        "hot_split_mean_runtime_pct": _mean_metric(
            successful_hot_rows,
            "runtime_pct_vs_no_break",
        ),
        "random_split_mean_runtime_pct": _mean_metric(
            successful_random_rows,
            "runtime_pct_vs_no_break",
        ),
        "hot_split_success_rate_pct": (
            (hot_success_total / hot_expected) * 100.0 if hot_expected else None
        ),
        "random_split_success_rate_pct": (
            (random_success_total / random_expected) * 100.0
            if random_expected
            else None
        ),
        "runtime_min_s": min(all_runtime_samples),
        "runtime_max_s": max(all_runtime_samples),
        "timed_runs": len(all_runtime_samples),
        "configured_max_split_attempts": manifest["replay"]["break_page_max_attempts"],
        "observed_max_split_attempts": max(
            int(record["observed_max_attempts"]) for record in records
        ),
        "split_rows_without_success": sum(
            int(record["split_successes_total"]) == 0
            for record in hot_rows + random_rows
        ),
        "split_rows_with_failures": sum(
            int(record["split_failures_total"]) > 0
            for record in hot_rows + random_rows
        ),
        "started_at_utc": manifest["started_at_utc"],
        "finished_at_utc": manifest["finished_at_utc"],
        "elapsed_seconds": manifest["elapsed_seconds"],
    }


def _build_dashboard_html(
    title: str,
    results_relpath: str,
) -> str:
    """Return the standalone runtime-only HTML dashboard template."""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0a1322;
    --bg-accent: #10263d;
    --card: rgba(10, 22, 39, 0.84);
    --border: rgba(137, 187, 255, 0.16);
    --text: #edf4ff;
    --muted: #9db1ca;
    --blue: #79a8ff;
    --teal: #59d2b2;
    --gold: #f5b54a;
    --red: #ff6e7d;
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
      radial-gradient(circle at top right, rgba(89, 210, 178, 0.14), transparent 28%),
      linear-gradient(180deg, #0a1322 0%, #09111c 64%, #070e18 100%);
    padding: 28px 20px 36px;
  }}
  .page {{
    max-width: 1380px;
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
    font-size: clamp(2rem, 4vw, 3.3rem);
    line-height: 1.04;
    letter-spacing: -0.03em;
  }}
  header p {{
    margin: 0;
    max-width: 920px;
    color: var(--muted);
    font-size: 1rem;
    line-height: 1.65;
  }}
  .notice {{
    margin-top: 14px;
    display: inline-block;
    padding: 10px 12px;
    border-radius: 12px;
    background: rgba(255, 181, 74, 0.09);
    border: 1px solid rgba(255, 181, 74, 0.16);
    color: #ffe7c3;
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
    font-size: clamp(1.65rem, 3vw, 2.35rem);
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
    height: 380px;
  }}
  .chart-wrap.tall {{ height: 470px; }}
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
  .mono {{
    font-family: "SFMono-Regular", ui-monospace, "Cascadia Code", Consolas, monospace;
    font-size: 0.84rem;
    color: #dfe9ff;
    word-break: break-word;
  }}
  .pre-wrap {{
    white-space: pre-wrap;
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
    color: #ffdce2;
    background: rgba(255, 110, 125, 0.13);
  }}
  .chip.teal {{
    color: #dafef3;
    background: rgba(89, 210, 178, 0.13);
  }}
  .chip.blue {{
    color: #dce8ff;
    background: rgba(121, 168, 255, 0.14);
  }}
  .chip.gold {{
    color: #ffedcc;
    background: rgba(245, 181, 74, 0.14);
  }}
  .kv-grid {{
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }}
  .kv {{
    background: rgba(17, 36, 61, 0.72);
    border: 1px solid rgba(135, 178, 255, 0.12);
    border-radius: 16px;
    padding: 14px 16px;
  }}
  .kv .label {{
    color: var(--muted);
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  .kv .value {{
    margin-top: 8px;
    font-size: 0.96rem;
    line-height: 1.5;
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
  table.summary tbody tr:hover {{
    background: rgba(121, 168, 255, 0.05);
  }}
  footer {{
    margin-top: 20px;
    padding: 18px 20px;
    border-radius: 18px;
    background: rgba(10, 22, 39, 0.82);
    border: 1px solid var(--border);
    color: var(--muted);
    font-size: 0.92rem;
    line-height: 1.65;
  }}
  @media (max-width: 1100px) {{
    .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .card {{ grid-column: 1 / -1; }}
    .kv-grid {{ grid-template-columns: 1fr; }}
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
  <div class="eyebrow">Redis Replay · Runtime Only</div>
  <h1 id="headerTitle">--</h1>
  <p id="headerDescription">
    This dashboard reads <code>{results_relpath}</code>. It excludes all hardware
    counters and focuses only on runtime plus replay-time split success/failure metadata.
  </p>
  <div class="notice">
    Runtime only. No dTLB loads, dTLB misses, or any other hardware counter measurements.
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
    <div class="label">Split max attempts</div>
    <div class="value" id="splitAttemptsValue">--</div>
    <div class="detail" id="splitAttemptsDetail">--</div>
  </article>
</section>

<section class="grid">
  <div class="card full-width">
    <h2>Section 1 — Runtime Overview</h2>
    <p class="desc">
      Mean runtime for <code>base_pages</code>, <code>no_break</code>, and only
      the split rows that achieved at least one successful split. Labels include
      successful splits out of the expected run count, and error bars draw runtime std.
    </p>
    <div class="chart-wrap tall"><canvas id="runtimeChart"></canvas></div>
  </div>

  <div class="card">
    <h2>Section 2 — Runtime Delta vs Access Count</h2>
    <p class="desc">
      Each point is one split-only row with at least one successful split. The
      X-axis is accepted-trace access count, and the Y-axis is runtime change
      versus <code>no_break</code>.
    </p>
    <div class="chart-wrap"><canvas id="scatterChart"></canvas></div>
  </div>

  <div class="card">
    <h2>Section 3 — Grouped Runtime View</h2>
    <p class="desc">
      Grouped runtime means for <code>base_pages</code>, <code>no_break</code>,
      successful hot split rows, and successful random split rows. Error bars draw runtime std.
    </p>
    <div class="chart-wrap"><canvas id="groupChart"></canvas></div>
  </div>

  <div class="card full-width">
    <h2>Section 4 — Split Outcomes</h2>
    <p class="desc">
      Successful versus failed replay-time split events for each split-only row.
      Zero-success rows stay here and in the summary even when they are omitted from the main runtime chart.
    </p>
    <div class="chart-wrap"><canvas id="splitOutcomeChart"></canvas></div>
  </div>

  <div class="card full-width">
    <h2>Section 5 — Experiment Config</h2>
    <p class="desc">
      Exact replay, capture, and verification settings needed to rerun this experiment.
    </p>
    <div class="kv-grid" id="configGrid"></div>
  </div>

  <div class="card full-width">
    <h2>Section 6 — Important Insights</h2>
    <p class="desc">
      High-level observations from this runtime-only replay pass.
    </p>
    <div class="story-grid" id="storyGrid"></div>
  </div>

  <div class="card full-width">
    <h2>Section 7 — Row Summary</h2>
    <p class="desc">
      Full per-row runtime samples, split outcomes, and attempt metadata.
    </p>
    <div id="summaryTableWrap"></div>
  </div>
</section>

<footer id="footerMeta">--</footer>
</div>

<script id="runtimeExperimentMetricsData" type="application/json">
{{}}
</script>
<script id="runtimeExperimentRowsData" type="application/json">
[]
</script>
<script id="runtimeExperimentManifestData" type="application/json">
{{}}
</script>
<script>
Chart.defaults.color = '#a0a0b0';
Chart.defaults.borderColor = 'rgba(255,255,255,0.07)';
Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";

const runtimeErrorBarPlugin = {{
  id: 'runtimeErrorBarPlugin',
  afterDatasetsDraw(chart, _args, options) {{
    const datasets = chart.data.datasets ?? [];
    const yScale = chart.scales.y;
    if (!yScale) {{
      return;
    }}

    const ctx = chart.ctx;
    ctx.save();
    ctx.strokeStyle = options.color ?? 'rgba(255,255,255,0.75)';
    ctx.lineWidth = options.lineWidth ?? 1.5;

    datasets.forEach((dataset, datasetIndex) => {{
      const errorBars = dataset.errorBars ?? [];
      const meta = chart.getDatasetMeta(datasetIndex);
      meta.data.forEach((element, index) => {{
        const std = errorBars[index];
        const mean = dataset.data[index];
        if (std == null || mean == null) {{
          return;
        }}
        const x = element.x;
        const topY = yScale.getPixelForValue(mean + std);
        const bottomY = yScale.getPixelForValue(Math.max(mean - std, 0));
        ctx.beginPath();
        ctx.moveTo(x, topY);
        ctx.lineTo(x, bottomY);
        ctx.moveTo(x - 6, topY);
        ctx.lineTo(x + 6, topY);
        ctx.moveTo(x - 6, bottomY);
        ctx.lineTo(x + 6, bottomY);
        ctx.stroke();
      }});
    }});

    ctx.restore();
  }},
}};
Chart.register(runtimeErrorBarPlugin);

const metrics = JSON.parse(document.getElementById('runtimeExperimentMetricsData').textContent);
const rows = JSON.parse(document.getElementById('runtimeExperimentRowsData').textContent);
const manifest = JSON.parse(document.getElementById('runtimeExperimentManifestData').textContent);
const chartRows = rows.filter((row) =>
  row.row_type === 'base_pages' ||
  row.row_type === 'no_break' ||
  row.split_successes_total > 0
);
const splitRows = rows.filter((row) =>
  row.row_type === 'hot_split_only' || row.row_type === 'random_split_only'
);
const successfulHotRows = rows.filter((row) =>
  row.row_type === 'hot_split_only' && row.split_successes_total > 0
);
const successfulRandomRows = rows.filter((row) =>
  row.row_type === 'random_split_only' && row.split_successes_total > 0
);

function formatSeconds(value) {{
  return value == null ? 'n/a' : `${{value.toFixed(3)}}s`;
}}

function formatPercent(value) {{
  if (value == null) {{
    return 'n/a';
  }}
  return `${{value >= 0 ? '+' : ''}}${{value.toFixed(2)}}%`;
}}

function formatRatio(successes, expected) {{
  return `${{successes}}/${{expected}}`;
}}

function rowColor(row) {{
  if (row.row_type === 'base_pages') return '#ff6e7d';
  if (row.row_type === 'no_break') return '#79a8ff';
  if (row.row_type === 'random_split_only') return '#59d2b2';
  return '#f5b54a';
}}

document.getElementById('headerTitle').textContent =
  `${{manifest.workload_label}} · ${{manifest.experiment_name}}`;
document.getElementById('basePagesRuntimeValue').textContent = formatSeconds(metrics.base_pages_runtime_s);
document.getElementById('basePagesRuntimeDetail').innerHTML =
  `<span class="chip ${{metrics.base_pages_pct_vs_no_break > 0 ? 'red' : 'teal'}}">${{formatPercent(metrics.base_pages_pct_vs_no_break)}}</span> versus <code>no_break</code>`;
document.getElementById('hotSplitValue').textContent = formatPercent(metrics.hot_split_mean_runtime_pct);
document.getElementById('hotSplitDetail').textContent =
  `Successful hot rows: ${{metrics.hot_rows_with_success}}/${{metrics.hot_rows}} · success rate ${{formatPercent(metrics.hot_split_success_rate_pct)}}`;
document.getElementById('randomSplitValue').textContent = formatPercent(metrics.random_split_mean_runtime_pct);
document.getElementById('randomSplitDetail').textContent =
  `Successful random rows: ${{metrics.random_rows_with_success}}/${{metrics.random_rows}} · success rate ${{formatPercent(metrics.random_split_success_rate_pct)}}`;
document.getElementById('splitAttemptsValue').textContent =
  `${{metrics.observed_max_split_attempts}} / ${{metrics.configured_max_split_attempts}}`;
document.getElementById('splitAttemptsDetail').textContent =
  `Observed max retry usage versus configured replay split limit.`;

new Chart(document.getElementById('runtimeChart'), {{
  type: 'bar',
  data: {{
    labels: chartRows.map((row) => row.short_label),
    datasets: [{{
      label: 'Mean runtime (s)',
      data: chartRows.map((row) => row.runtime_mean),
      errorBars: chartRows.map((row) => row.runtime_std),
      backgroundColor: chartRows.map(rowColor),
      borderColor: 'rgba(255,255,255,0.18)',
      borderWidth: 1.2,
      borderRadius: 8,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      runtimeErrorBarPlugin: {{ color: 'rgba(255,255,255,0.78)' }},
      tooltip: {{
        callbacks: {{
          label: (ctx) => {{
            const row = chartRows[ctx.dataIndex];
            return [
              `Runtime: ${{formatSeconds(ctx.raw)}}`,
              `Std: ${{formatSeconds(row.runtime_std)}}`,
              `Split success: ${{formatRatio(row.split_successes_total, row.expected_split_events)}}`,
              `Split failures: ${{row.split_failures_total}}`,
              `Observed max attempts: ${{row.observed_max_attempts}}`,
            ];
          }},
        }}
      }}
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#e0e0e0', maxRotation: 45, minRotation: 45 }},
        grid: {{ display: false }}
      }},
      y: {{
        title: {{ display: true, text: 'Mean runtime (s)', color: '#e0e0e0' }},
        ticks: {{
          color: '#e0e0e0',
          callback: (value) => `${{value}}s`,
        }},
        grid: {{ color: 'rgba(255,255,255,0.08)' }}
      }}
    }}
  }}
}});

new Chart(document.getElementById('scatterChart'), {{
  type: 'scatter',
  data: {{
    datasets: chartRows
      .filter((row) => row.row_type === 'hot_split_only' || row.row_type === 'random_split_only')
      .map((row) => ({{
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
          callback: (value) => `${{value}}%`,
        }}
      }}
    }}
  }}
}});

const hotGroup = {{
  label: 'hot split mean',
  mean: successfulHotRows.length
    ? successfulHotRows.flatMap((row) => row.runtime_samples).reduce((sum, value) => sum + value, 0)
      / successfulHotRows.flatMap((row) => row.runtime_samples).length
    : null,
  std: successfulHotRows.length
    ? (() => {{
        const samples = successfulHotRows.flatMap((row) => row.runtime_samples);
        const mean = samples.reduce((sum, value) => sum + value, 0) / samples.length;
        if (samples.length <= 1) {{
          return 0;
        }}
        const variance = samples.reduce((sum, value) => sum + ((value - mean) ** 2), 0) / (samples.length - 1);
        return Math.sqrt(variance);
      }})()
    : null,
}};
const randomGroup = {{
  label: 'random split mean',
  mean: successfulRandomRows.length
    ? successfulRandomRows.flatMap((row) => row.runtime_samples).reduce((sum, value) => sum + value, 0)
      / successfulRandomRows.flatMap((row) => row.runtime_samples).length
    : null,
  std: successfulRandomRows.length
    ? (() => {{
        const samples = successfulRandomRows.flatMap((row) => row.runtime_samples);
        const mean = samples.reduce((sum, value) => sum + value, 0) / samples.length;
        if (samples.length <= 1) {{
          return 0;
        }}
        const variance = samples.reduce((sum, value) => sum + ((value - mean) ** 2), 0) / (samples.length - 1);
        return Math.sqrt(variance);
      }})()
    : null,
}};

new Chart(document.getElementById('groupChart'), {{
  type: 'bar',
  data: {{
    labels: ['base_pages', 'no_break', hotGroup.label, randomGroup.label],
    datasets: [{{
      label: 'Mean runtime (s)',
      data: [
        metrics.base_pages_runtime_s,
        metrics.no_break_runtime_s,
        hotGroup.mean,
        randomGroup.mean,
      ],
      errorBars: [
        rows.find((row) => row.row_type === 'base_pages').runtime_std,
        rows.find((row) => row.row_type === 'no_break').runtime_std,
        hotGroup.std,
        randomGroup.std,
      ],
      backgroundColor: ['#ff6e7d', '#79a8ff', '#f5b54a', '#59d2b2'],
      borderRadius: 8,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      runtimeErrorBarPlugin: {{ color: 'rgba(255,255,255,0.78)' }},
      tooltip: {{
        callbacks: {{
          label: (ctx) => `${{formatSeconds(ctx.raw)}}`,
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
          callback: (value) => `${{value}}s`,
        }}
      }}
    }}
  }}
}});

new Chart(document.getElementById('splitOutcomeChart'), {{
  type: 'bar',
  data: {{
    labels: splitRows.map((row) => row.short_label),
    datasets: [
      {{
        label: 'Successful splits',
        data: splitRows.map((row) => row.split_successes_total),
        backgroundColor: '#59d2b2',
        borderRadius: 8,
      }},
      {{
        label: 'Failed splits',
        data: splitRows.map((row) => row.split_failures_total),
        backgroundColor: '#ff6e7d',
        borderRadius: 8,
      }}
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{
        labels: {{ color: '#e0e0e0' }},
      }},
      tooltip: {{
        callbacks: {{
          afterBody: (items) => {{
            const row = splitRows[items[0].dataIndex];
            return [
              `Expected split events: ${{row.expected_split_events}}`,
              `Total syscall attempts: ${{row.split_syscall_attempts_total}}`,
              `Observed max attempts: ${{row.observed_max_attempts}}`,
            ];
          }},
        }}
      }}
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#e0e0e0', maxRotation: 45, minRotation: 45 }},
        grid: {{ display: false }}
      }},
      y: {{
        ticks: {{
          color: '#e0e0e0',
          precision: 0,
        }}
      }}
    }}
  }}
}});

const configCards = [
  {{
    label: 'Capture config',
    value: JSON.stringify(manifest.capture, null, 2),
  }},
  {{
    label: 'Row layout',
    value: JSON.stringify(manifest.row_layout, null, 2),
  }},
  {{
    label: 'Verification config',
    value: JSON.stringify(manifest.verification, null, 2),
  }},
  {{
    label: 'Replay config',
    value: JSON.stringify(manifest.replay, null, 2),
  }},
];
document.getElementById('configGrid').innerHTML = configCards.map((card) => `
  <div class="kv">
    <div class="label">${{card.label}}</div>
    <div class="value mono pre-wrap">${{card.value}}</div>
  </div>
`).join('');

const storyCards = [
  {{
    title: 'base_pages vs no_break',
    body: `base_pages averaged ${{formatSeconds(metrics.base_pages_runtime_s)}} and was ${{formatPercent(metrics.base_pages_pct_vs_no_break)}} versus no_break.`,
  }},
  {{
    title: 'Hot split success rate',
    body: `Hot rows succeeded on ${{formatPercent(metrics.hot_split_success_rate_pct)}} of expected split events.`,
  }},
  {{
    title: 'Random split success rate',
    body: `Random rows succeeded on ${{formatPercent(metrics.random_split_success_rate_pct)}} of expected split events.`,
  }},
  {{
    title: 'Observed retry usage',
    body: `Observed max split attempts was ${{metrics.observed_max_split_attempts}} with a configured limit of ${{metrics.configured_max_split_attempts}}.`,
  }},
].concat((manifest.insights ?? []).map((insight, index) => ({{
  title: `manifest insight #${{index + 1}}`,
  body: insight,
}})));
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
        <th>THP Mode</th>
        <th>Key</th>
        <th>Accesses</th>
        <th>Mean Runtime</th>
        <th>Std</th>
        <th>Split Success</th>
        <th>Split Failure</th>
        <th>Attempts</th>
        <th>Max Attempts</th>
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
            <td data-label="THP Mode">${{row.thp_mode}}</td>
            <td data-label="Key" class="mono">${{row.key ?? '—'}}</td>
            <td data-label="Accesses">${{row.access_count == null ? '—' : row.access_count.toLocaleString()}}</td>
            <td data-label="Mean Runtime">${{formatSeconds(row.runtime_mean)}}</td>
            <td data-label="Std">${{formatSeconds(row.runtime_std)}}</td>
            <td data-label="Split Success">${{row.expected_split_events ? formatRatio(row.split_successes_total, row.expected_split_events) : '—'}}</td>
            <td data-label="Split Failure">${{row.expected_split_events ? row.split_failures_total : '—'}}</td>
            <td data-label="Attempts">${{row.expected_split_events ? row.split_syscall_attempts_total : '—'}}</td>
            <td data-label="Max Attempts">${{row.expected_split_events ? row.observed_max_attempts : '—'}}</td>
            <td data-label="Samples" class="mono">${{row.runtime_samples.map(formatSeconds).join(', ')}}</td>
          </tr>
        `).join('')
      }}
    </tbody>
  </table>
`;

const footerLines = [
  `Results parquet: ${{metrics.results_path}}`,
  `Trace log: ${{metrics.trace_path}}`,
  `Started: ${{metrics.started_at_utc}}`,
  `Finished: ${{metrics.finished_at_utc}}`,
  `Elapsed: ${{metrics.elapsed_seconds.toFixed(1)}}s`,
  `Notes: ${{(manifest.notes ?? []).join(' | ') || 'n/a'}}`,
];
document.getElementById('footerMeta').innerHTML = footerLines.join('<br>');
</script>
</body>
</html>
"""


def _write_outputs(
    records: list[dict[str, Any]],
    metrics: dict[str, Any],
    manifest: dict[str, Any],
    html_path: Path,
    rows_path: Path,
    metrics_path: Path,
    summary_path: Path,
    title: str,
    results_path: Path,
) -> None:
    """Write HTML, JSON, and markdown summary artifacts."""

    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    rows_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

    html = _build_dashboard_html(
        title=title,
        results_relpath=str(results_path.relative_to(REPO_ROOT)),
    )
    html = _replace_inline_json_script(
        html,
        "runtimeExperimentMetricsData",
        metrics,
    )
    html = _replace_inline_json_script(
        html,
        "runtimeExperimentRowsData",
        records,
    )
    html = _replace_inline_json_script(
        html,
        "runtimeExperimentManifestData",
        manifest,
    )
    html_path.write_text(html, encoding="utf-8")

    successful_hot_rows = [
        record
        for record in records
        if record["row_type"] == "hot_split_only"
        and int(record["split_successes_total"]) > 0
    ]
    successful_random_rows = [
        record
        for record in records
        if record["row_type"] == "random_split_only"
        and int(record["split_successes_total"]) > 0
    ]
    zero_success_rows = [
        record
        for record in records
        if record["row_type"] in {"hot_split_only", "random_split_only"}
        and int(record["split_successes_total"]) == 0
    ]
    slowest = sorted(
        successful_hot_rows + successful_random_rows,
        key=lambda record: float(record["runtime_delta_vs_no_break"]),
        reverse=True,
    )[:5]

    lines = [
        f"# {manifest['workload_label']} · {manifest['experiment_name']}",
        "",
        f"Source parquet: `{results_path.relative_to(REPO_ROOT)}`",
        "",
        "## Important Takeaways",
        "",
        f"- `base_pages` averaged `{metrics['base_pages_runtime_s']:.3f}s` and `no_break` averaged `{metrics['no_break_runtime_s']:.3f}s`.",
        f"- `base_pages` was `{metrics['base_pages_pct_vs_no_break']:+.2f}%` versus `no_break`.",
        f"- Hot split success rate: `{metrics['hot_split_success_rate_pct']:.2f}%`." if metrics["hot_split_success_rate_pct"] is not None else "- Hot split success rate: `n/a`.",
        f"- Random split success rate: `{metrics['random_split_success_rate_pct']:.2f}%`." if metrics["random_split_success_rate_pct"] is not None else "- Random split success rate: `n/a`.",
        f"- Observed max split attempts was `{metrics['observed_max_split_attempts']}` with configured limit `{metrics['configured_max_split_attempts']}`.",
        f"- Timed runs ranged from `{metrics['runtime_min_s']:.3f}s` to `{metrics['runtime_max_s']:.3f}s`.",
        "",
        "## Slowest Successful Split Rows",
        "",
    ]
    if slowest:
        for record in slowest:
            lines.append(
                f"- `{record['short_label']}` = `{record['key']}` with `{record['access_count']}` accesses, "
                f"runtime delta `{record['runtime_pct_vs_no_break']:+.2f}%`, "
                f"split success `{record['split_successes_total']}/{record['expected_split_events']}`."
            )
    else:
        lines.append("- No split-only row achieved a successful split.")

    lines.extend(
        [
            "",
            "## Zero-Success Rows",
            "",
        ]
    )
    if zero_success_rows:
        for record in zero_success_rows:
            lines.append(
                f"- `{record['short_label']}` = `{record['key']}` had `0/{record['expected_split_events']}` successful splits, "
                f"`{record['split_failures_total']}` failed split events, and observed max attempts `{record['observed_max_attempts']}`."
            )
    else:
        lines.append("- Every split-only row had at least one successful split.")

    lines.extend(
        [
            "",
            "## Manifest Notes",
            "",
        ]
    )
    for note in manifest.get("notes", []):
        lines.append(f"- {note}")
    if not manifest.get("notes"):
        lines.append("- No extra notes recorded.")

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    """Return manifest JSON with the required runtime-only fields.

    Raises:
        RuntimeError: If required fields are missing.
    """

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required_top_level = [
        "experiment_name",
        "workload_label",
        "row_layout",
        "capture",
        "verification",
        "replay",
        "started_at_utc",
        "finished_at_utc",
        "elapsed_seconds",
        "notes",
        "insights",
    ]
    for field in required_top_level:
        if field not in manifest:
            raise RuntimeError(f"Manifest is missing required field: {field}")

    for replay_field in ["break_page_max_attempts"]:
        if replay_field not in manifest["replay"]:
            raise RuntimeError(f"Manifest replay config is missing: {replay_field}")

    for layout_field in ["hot_keys", "random_rows", "runs"]:
        if layout_field not in manifest["row_layout"]:
            raise RuntimeError(f"Manifest row layout is missing: {layout_field}")

    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a runtime-only dashboard for one replay experiment.",
    )
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-html", type=Path, required=True)
    parser.add_argument("--output-metrics", type=Path, required=True)
    parser.add_argument("--output-rows", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Generate the runtime-only dashboard and supporting files."""

    args = _build_parser().parse_args(argv)
    args.results = args.results.resolve()
    args.trace = args.trace.resolve()
    args.manifest = args.manifest.resolve()
    args.output_html = args.output_html.resolve()
    args.output_metrics = args.output_metrics.resolve()
    args.output_rows = args.output_rows.resolve()
    args.output_summary = args.output_summary.resolve()
    args.output_html.parent.mkdir(parents=True, exist_ok=True)

    manifest = _load_manifest(args.manifest)
    records = _classify_rows(
        pl.read_parquet(args.results),
        trace_path=args.trace,
        hot_key_rows=int(manifest["row_layout"]["hot_keys"]),
        runs=int(manifest["row_layout"]["runs"]),
    )
    metrics = _build_metrics(
        records,
        results_path=args.results,
        trace_path=args.trace,
        manifest=manifest,
    )
    _write_outputs(
        records=records,
        metrics=metrics,
        manifest=manifest,
        html_path=args.output_html,
        rows_path=args.output_rows,
        metrics_path=args.output_metrics,
        summary_path=args.output_summary,
        title=f"{manifest['workload_label']} · {manifest['experiment_name']}",
        results_path=args.results,
    )


if __name__ == "__main__":
    main()
