"""Render a live runtime dashboard from a replay session log.

This dashboard is designed for in-progress replay runs where the final result
parquet is not available yet. It combines the current session log with the
accepted breakpoint matrix and monitor log so the page can still show:

- base_pages vs no_break progress
- per-row runtime means/std for completed runs so far
- inferred split successes and failures for split-only rows
- current in-progress row/run state

The split counts are inferred from the log plus the single-split-per-row matrix
shape. They should be treated as a live preview until the final result parquet
exists.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATE_BREAKPOINTS_PATH = (
    REPO_ROOT / "python" / "kernmlops" / "replay" / "generate_breakpoints.py"
)

_RESTORE_RE = re.compile(
    r"INFO: \[(?P<row>\d+)/(?P<row_total>\d+) run (?P<run>\d+)/(?P<run_total>\d+)\] "
    r"Restoring snapshot \(THP (?P<thp_mode>never|always)\) \.\.\."
)
_TIMING_RE = re.compile(
    r"INFO: \[(?P<row>\d+)/(?P<row_total>\d+) run (?P<run>\d+)/(?P<run_total>\d+)\] "
    r"Timing run trace \.\.\."
)
_DONE_RE = re.compile(
    r"INFO: \[(?P<row>\d+)/(?P<row_total>\d+) run (?P<run>\d+)/(?P<run_total>\d+)\] "
    r"Done — (?P<commands>\d+) commands in (?P<runtime_s>\d+\.\d+)s"
)
_RETRY_RE = re.compile(
    r"break_page: retrying split_thp\(pid=(?P<pid>\d+), vaddr=(?P<vaddr>0x[0-9a-f]+)\) "
    r"for key '(?P<key>[^']+)' after attempt (?P<attempt>\d+)/(?P<max_attempts>\d+) failed: "
    r"(?P<reason>.+)$"
)
_WARNING_RE = re.compile(
    r"WARNING: line (?P<trace_line>\d+): break_page failed for key '(?P<key>[^']+)' "
    r"at access (?P<access>\d+) after (?P<attempts>\d+) attempts: (?P<reason>.+)$"
)


@dataclass
class ParsedRun:
    """Live state for one row/run pair inside the session log.

    Attributes:
        row_number: One-based replay row number from the log.
        row_total: Total rows expected in the run.
        run_number: One-based timed run index from the log.
        run_total: Total timed runs per row.
        thp_mode: THP mode logged for the restore step.
        status: Current stage: ``restoring``, ``timing``, or ``done``.
        commands: Replay command count when the run is done.
        runtime_s: Measured runtime when the run is done.
        retry_count: Count of retry log lines observed for this run.
        retry_messages: Raw retry messages seen for this run.
        warning_messages: Raw split-failure warning messages for this run.
        warning_keys: Keys named by warning messages for this run.
    """

    row_number: int
    row_total: int
    run_number: int
    run_total: int
    thp_mode: str | None = None
    status: str = "pending"
    commands: int | None = None
    runtime_s: float | None = None
    retry_count: int = 0
    retry_messages: list[str] = field(default_factory=list)
    warning_messages: list[str] = field(default_factory=list)
    warning_keys: list[str] = field(default_factory=list)


def _utc_now_text() -> str:
    """Return an ISO-8601 UTC timestamp."""

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _load_access_counts(trace_path: Path) -> dict[str, int]:
    """Return access counts reconstructed from the accepted monitor log."""

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
    """Return sample standard deviation, or zero when only one sample exists."""

    return statistics.stdev(values) if len(values) > 1 else 0.0


def _mean(values: list[float]) -> float:
    """Return the arithmetic mean for a non-empty sample set."""

    if not values:
        raise RuntimeError("Expected at least one value when computing a mean")
    return sum(values) / len(values)


def _parse_session_log(session_log_path: Path) -> dict[str, Any]:
    """Parse the current replay session log into per-run state."""

    runs: dict[tuple[int, int], ParsedRun] = {}
    active_run_key: tuple[int, int] | None = None
    last_line = ""
    row_total = 0
    run_total = 0

    for raw_line in session_log_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        last_line = line

        restore_match = _RESTORE_RE.match(line)
        if restore_match is not None:
            row_number = int(restore_match.group("row"))
            run_number = int(restore_match.group("run"))
            row_total = int(restore_match.group("row_total"))
            run_total = int(restore_match.group("run_total"))
            key = (row_number, run_number)
            record = runs.setdefault(
                key,
                ParsedRun(
                    row_number=row_number,
                    row_total=row_total,
                    run_number=run_number,
                    run_total=run_total,
                ),
            )
            record.thp_mode = restore_match.group("thp_mode")
            record.status = "restoring"
            active_run_key = key
            continue

        timing_match = _TIMING_RE.match(line)
        if timing_match is not None:
            row_number = int(timing_match.group("row"))
            run_number = int(timing_match.group("run"))
            row_total = int(timing_match.group("row_total"))
            run_total = int(timing_match.group("run_total"))
            key = (row_number, run_number)
            record = runs.setdefault(
                key,
                ParsedRun(
                    row_number=row_number,
                    row_total=row_total,
                    run_number=run_number,
                    run_total=run_total,
                ),
            )
            record.status = "timing"
            active_run_key = key
            continue

        done_match = _DONE_RE.match(line)
        if done_match is not None:
            row_number = int(done_match.group("row"))
            run_number = int(done_match.group("run"))
            row_total = int(done_match.group("row_total"))
            run_total = int(done_match.group("run_total"))
            key = (row_number, run_number)
            record = runs.setdefault(
                key,
                ParsedRun(
                    row_number=row_number,
                    row_total=row_total,
                    run_number=run_number,
                    run_total=run_total,
                ),
            )
            record.status = "done"
            record.commands = int(done_match.group("commands"))
            record.runtime_s = float(done_match.group("runtime_s"))
            if active_run_key == key:
                active_run_key = None
            continue

        retry_match = _RETRY_RE.search(line)
        if retry_match is not None and active_run_key is not None:
            runs[active_run_key].retry_count += 1
            runs[active_run_key].retry_messages.append(line)
            continue

        warning_match = _WARNING_RE.match(line)
        if warning_match is not None and active_run_key is not None:
            runs[active_run_key].warning_messages.append(line)
            runs[active_run_key].warning_keys.append(warning_match.group("key"))
            continue

    completed_runs = [record for record in runs.values() if record.status == "done"]
    current_run = runs.get(active_run_key) if active_run_key is not None else None

    return {
        "runs": runs,
        "row_total": row_total,
        "run_total": run_total,
        "completed_runs": completed_runs,
        "current_run": current_run,
        "last_line": last_line,
        "session_log_mtime_utc": (
            datetime.fromtimestamp(session_log_path.stat().st_mtime, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        ),
    }


def _classify_rows(
    breakpoints_path: Path,
    trace_path: Path,
    *,
    hot_keys: int,
    random_rows: int,
    runs_per_row: int,
    parsed_log: dict[str, Any],
) -> list[dict[str, Any]]:
    """Combine the breakpoint matrix with the live session log."""

    breakpoints_df = pl.read_parquet(breakpoints_path)
    access_counts = _load_access_counts(trace_path)
    ordered_keys = [
        key
        for key, _count in sorted(
            access_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]
    hot_key_set = set(ordered_keys[:hot_keys])
    breakpoint_cols = breakpoints_df.columns

    raw_records: list[dict[str, Any]] = []
    for row_index, row in enumerate(breakpoints_df.iter_rows(named=True)):
        breakpoints = {key: int(row[key]) for key in breakpoint_cols}
        zero_keys = [key for key, value in breakpoints.items() if value == 0]

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

        run_records = [
            parsed_log["runs"][(row_index + 1, run_number)]
            for run_number in range(1, runs_per_row + 1)
            if (row_index + 1, run_number) in parsed_log["runs"]
        ]
        completed_run_records = [
            run_record for run_record in run_records if run_record.status == "done"
        ]
        runtime_samples = [
            float(run_record.runtime_s)
            for run_record in completed_run_records
            if run_record.runtime_s is not None
        ]
        command_samples = [
            int(run_record.commands)
            for run_record in completed_run_records
            if run_record.commands is not None
        ]
        retry_count_total = sum(run_record.retry_count for run_record in completed_run_records)
        warning_messages = [
            warning
            for run_record in completed_run_records
            for warning in run_record.warning_messages
        ]
        completed_runs = len(completed_run_records)
        completed_split_runs = (
            completed_runs if row_type in {"hot_split_only", "random_split_only"} else 0
        )
        inferred_failures = len(warning_messages)
        inferred_successes = max(completed_split_runs - inferred_failures, 0)
        observed_max_attempts = (
            max(1 + run_record.retry_count for run_record in completed_run_records)
            if completed_split_runs
            else 0
        )

        in_progress_run = next(
            (run_record for run_record in run_records if run_record.status != "done"),
            None,
        )
        status = (
            "complete"
            if completed_runs == runs_per_row
            else "in_progress"
            if in_progress_run is not None or completed_runs > 0
            else "pending"
        )
        thp_mode = (
            completed_run_records[0].thp_mode
            if completed_run_records
            else in_progress_run.thp_mode
            if in_progress_run is not None
            else "never"
            if row_type == "base_pages"
            else "always"
        )

        raw_records.append(
            {
                "row_index": row_index,
                "row_number": row_index + 1,
                "row_type": row_type,
                "key": key,
                "access_count": access_counts.get(key, 0) if key is not None else None,
                "runtime_samples": runtime_samples,
                "runtime_mean": _mean(runtime_samples) if runtime_samples else None,
                "runtime_std": _stddev(runtime_samples) if runtime_samples else None,
                "commands_samples": command_samples,
                "commands_mean": _mean([float(value) for value in command_samples])
                if command_samples
                else None,
                "thp_mode": thp_mode,
                "completed_runs": completed_runs,
                "total_runs": runs_per_row,
                "status": status,
                "current_stage": in_progress_run.status if in_progress_run is not None else None,
                "current_run_number": in_progress_run.run_number
                if in_progress_run is not None
                else None,
                "inferred_split_successes": inferred_successes,
                "inferred_split_failures": inferred_failures,
                "completed_split_runs": completed_split_runs,
                "inferred_split_success_rate_pct": (
                    (inferred_successes / completed_split_runs) * 100.0
                    if completed_split_runs
                    else None
                ),
                "inferred_split_success_ratio_total": (
                    (inferred_successes / runs_per_row)
                    if row_type in {"hot_split_only", "random_split_only"}
                    else None
                ),
                "inferred_retry_count_total": retry_count_total,
                "inferred_attempts_total": (
                    completed_split_runs + retry_count_total if completed_split_runs else 0
                ),
                "observed_max_attempts": observed_max_attempts,
                "warning_messages": warning_messages,
                "latest_warning": warning_messages[-1] if warning_messages else None,
            }
        )

    hot_rows = sorted(
        (record for record in raw_records if record["row_type"] == "hot_split_only"),
        key=lambda record: (-int(record["access_count"]), str(record["key"])),
    )
    random_only_rows = sorted(
        (record for record in raw_records if record["row_type"] == "random_split_only"),
        key=lambda record: (-int(record["access_count"]), str(record["key"])),
    )

    for hot_rank, record in enumerate(hot_rows, start=1):
        record["hot_rank"] = hot_rank
        record["random_rank"] = None
        record["short_label"] = f"hot#{hot_rank} ({record['inferred_split_successes']}/{runs_per_row})"
    for random_rank, record in enumerate(random_only_rows, start=1):
        record["hot_rank"] = None
        record["random_rank"] = random_rank
        record["short_label"] = f"rand#{random_rank} ({record['inferred_split_successes']}/{runs_per_row})"

    for record in raw_records:
        if record["row_type"] == "base_pages":
            record["short_label"] = "base_pages"
            record["hot_rank"] = None
            record["random_rank"] = None
        elif record["row_type"] == "no_break":
            record["short_label"] = "no_break"
            record["hot_rank"] = None
            record["random_rank"] = None

    ordered_records = sorted(raw_records, key=lambda record: record["row_index"])
    no_break_mean = next(
        (
            float(record["runtime_mean"])
            for record in ordered_records
            if record["row_type"] == "no_break" and record["runtime_mean"] is not None
        ),
        None,
    )
    if no_break_mean is not None:
        for record in ordered_records:
            if record["runtime_mean"] is None:
                record["runtime_delta_vs_no_break"] = None
                record["runtime_pct_vs_no_break"] = None
                record["runtime_std_pct_vs_no_break"] = None
                continue
            runtime_mean = float(record["runtime_mean"])
            record["runtime_delta_vs_no_break"] = runtime_mean - no_break_mean
            record["runtime_pct_vs_no_break"] = (
                (runtime_mean / no_break_mean - 1.0) * 100.0
            )
            record["runtime_std_pct_vs_no_break"] = (
                (float(record["runtime_std"]) / no_break_mean) * 100.0
                if record["runtime_std"] is not None
                else None
            )
    else:
        for record in ordered_records:
            record["runtime_delta_vs_no_break"] = None
            record["runtime_pct_vs_no_break"] = None
            record["runtime_std_pct_vs_no_break"] = None

    return ordered_records


def _build_metrics(
    *,
    experiment_name: str,
    workload_label: str,
    session_log_path: Path,
    breakpoints_path: Path,
    trace_path: Path,
    report_path: Path | None,
    rows: list[dict[str, Any]],
    parsed_log: dict[str, Any],
    hot_keys: int,
    random_rows: int,
    runs_per_row: int,
) -> dict[str, Any]:
    """Build the top-level live dashboard metrics."""

    completed_rows = [
        row for row in rows if int(row["completed_runs"]) == int(row["total_runs"])
    ]
    started_rows = [row for row in rows if int(row["completed_runs"]) > 0 or row["status"] == "in_progress"]
    completed_runs_count = len(parsed_log["completed_runs"])
    expected_runs_count = len(rows) * runs_per_row
    runtime_samples = [
        float(sample)
        for row in rows
        for sample in list(row["runtime_samples"])
    ]
    split_rows = [
        row for row in rows if row["row_type"] in {"hot_split_only", "random_split_only"}
    ]
    base_pages = next((row for row in rows if row["row_type"] == "base_pages"), None)
    no_break = next((row for row in rows if row["row_type"] == "no_break"), None)
    current_run = parsed_log["current_run"]

    return {
        "experiment_name": experiment_name,
        "workload_label": workload_label,
        "session_log_path": str(session_log_path),
        "breakpoints_path": str(breakpoints_path),
        "trace_path": str(trace_path),
        "report_path": str(report_path) if report_path is not None else None,
        "generated_at_utc": _utc_now_text(),
        "session_log_mtime_utc": parsed_log["session_log_mtime_utc"],
        "observed_rows": len(rows),
        "observed_row_total_from_log": parsed_log["row_total"] or len(rows),
        "runs_per_row": runs_per_row,
        "expected_runs": expected_runs_count,
        "completed_runs": completed_runs_count,
        "progress_pct": (
            (completed_runs_count / expected_runs_count) * 100.0
            if expected_runs_count
            else 0.0
        ),
        "completed_rows": len(completed_rows),
        "started_rows": len(started_rows),
        "pending_rows": len(rows) - len(started_rows),
        "hot_keys": hot_keys,
        "random_rows": random_rows,
        "base_pages_runtime_s": base_pages["runtime_mean"] if base_pages is not None else None,
        "no_break_runtime_s": no_break["runtime_mean"] if no_break is not None else None,
        "base_pages_pct_vs_no_break": (
            base_pages["runtime_pct_vs_no_break"]
            if base_pages is not None
            else None
        ),
        "runtime_min_s": min(runtime_samples) if runtime_samples else None,
        "runtime_max_s": max(runtime_samples) if runtime_samples else None,
        "runtime_mean_s": _mean(runtime_samples) if runtime_samples else None,
        "commands_mean": (
            _mean(
                [
                    float(run_record.commands)
                    for run_record in parsed_log["completed_runs"]
                    if run_record.commands is not None
                ]
            )
            if parsed_log["completed_runs"]
            else None
        ),
        "inferred_split_successes": sum(int(row["inferred_split_successes"]) for row in split_rows),
        "inferred_split_failures": sum(int(row["inferred_split_failures"]) for row in split_rows),
        "split_rows_with_failures": sum(
            int(row["inferred_split_failures"]) > 0 for row in split_rows
        ),
        "rows_with_samples": sum(len(row["runtime_samples"]) > 0 for row in rows),
        "current_row_label": (
            f"row {current_run.row_number}/{current_run.row_total} "
            f"run {current_run.run_number}/{current_run.run_total}"
            if current_run is not None
            else None
        ),
        "current_stage": current_run.status if current_run is not None else None,
        "last_line": parsed_log["last_line"],
        "estimated_remaining_seconds": (
            _mean(runtime_samples) * (expected_runs_count - completed_runs_count)
            if runtime_samples
            else None
        ),
        "limitations": [
            "This is a log-only live view. The final result parquet is not available yet.",
            "Split successes, failures, and attempt counts are inferred from the session log and the one-split-per-row matrix layout.",
            "Exact full-run record_count and operation_count were not recoverable from the current live artifacts.",
        ],
    }


def _replace_inline_json_script(html: str, script_id: str, payload: object) -> str:
    """Replace one embedded JSON block inside the HTML template."""

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
        raise RuntimeError(f"Could not find inline JSON block '{script_id}' in template")
    return updated_html


def _build_dashboard_html(title: str) -> str:
    """Return the standalone HTML template for the live session dashboard."""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #08131f;
    --bg-accent: #11253d;
    --card: rgba(10, 24, 42, 0.88);
    --border: rgba(126, 176, 255, 0.18);
    --text: #eef5ff;
    --muted: #9eb0c8;
    --blue: #7caeff;
    --teal: #58d0b2;
    --gold: #f3b74d;
    --red: #ff7080;
    --shadow: 0 18px 42px rgba(0, 0, 0, 0.28);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    min-height: 100vh;
    color: var(--text);
    font-family: "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    background:
      radial-gradient(circle at top left, rgba(124, 174, 255, 0.16), transparent 30%),
      radial-gradient(circle at top right, rgba(88, 208, 178, 0.14), transparent 28%),
      linear-gradient(180deg, #08131f 0%, #09121b 64%, #070d15 100%);
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
    background: rgba(124, 174, 255, 0.12);
    border: 1px solid rgba(124, 174, 255, 0.16);
    color: #d5e5ff;
    font-size: 0.82rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }}
  header h1 {{
    margin: 14px 0 10px;
    font-size: clamp(2rem, 4vw, 3.2rem);
    line-height: 1.04;
    letter-spacing: -0.03em;
  }}
  header p {{
    margin: 0;
    max-width: 940px;
    color: var(--muted);
    font-size: 1rem;
    line-height: 1.65;
  }}
  .notice {{
    margin-top: 14px;
    display: inline-block;
    padding: 10px 12px;
    border-radius: 12px;
    background: rgba(243, 183, 77, 0.10);
    border: 1px solid rgba(243, 183, 77, 0.16);
    color: #ffe9c6;
    font-size: 0.88rem;
  }}
  .stats {{
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 16px;
    margin-bottom: 18px;
  }}
  .stat {{
    background: linear-gradient(180deg, rgba(18, 37, 61, 0.96), rgba(10, 23, 40, 0.96));
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
    height: 390px;
  }}
  .chart-wrap.tall {{ height: 470px; }}
  canvas {{
    width: 100% !important;
    height: 100% !important;
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
  .story-grid {{
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
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
  table.summary {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
  }}
  table.summary th, table.summary td {{
    padding: 12px 10px;
    text-align: left;
    border-bottom: 1px solid rgba(135, 178, 255, 0.10);
    vertical-align: top;
  }}
  table.summary th {{
    color: #d7e3f7;
    font-size: 0.76rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  .badge {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 9px;
    border-radius: 999px;
    font-size: 0.76rem;
    font-weight: 700;
  }}
  .badge.complete {{
    color: #d8fff3;
    background: rgba(88, 208, 178, 0.14);
  }}
  .badge.progress {{
    color: #ffeccc;
    background: rgba(243, 183, 77, 0.14);
  }}
  .badge.pending {{
    color: #dce8ff;
    background: rgba(124, 174, 255, 0.14);
  }}
  footer {{
    margin-top: 20px;
    color: var(--muted);
    font-size: 0.9rem;
    line-height: 1.6;
  }}
  @media (max-width: 1180px) {{
    .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .card {{ grid-column: 1 / -1; }}
  }}
  @media (max-width: 720px) {{
    body {{ padding: 20px 14px 28px; }}
    .stats, .kv-grid, .story-grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<div class="page">
  <header>
    <span class="eyebrow">Log-Only Live View</span>
    <h1>{title}</h1>
    <p>
      This page is built from the in-progress replay session log, the accepted
      breakpoint matrix, and the accepted monitor trace. It is intentionally a
      live preview, not a final parquet-backed result page.
    </p>
    <div class="notice">
      Split outcomes and split attempts are inferred from log lines. Final
      parquet numbers may differ slightly once the replay finishes.
    </div>
  </header>

  <section class="stats">
    <div class="stat">
      <div class="label">Run Progress</div>
      <div class="value" id="progressValue">--</div>
      <div class="detail" id="progressDetail">--</div>
    </div>
    <div class="stat">
      <div class="label">Current Stage</div>
      <div class="value" id="currentStageValue">--</div>
      <div class="detail" id="currentStageDetail">--</div>
    </div>
    <div class="stat">
      <div class="label">Base vs No Break</div>
      <div class="value" id="baseVsNoBreakValue">--</div>
      <div class="detail" id="baseVsNoBreakDetail">--</div>
    </div>
    <div class="stat">
      <div class="label">Split Outcomes</div>
      <div class="value" id="splitOutcomeValue">--</div>
      <div class="detail" id="splitOutcomeDetail">--</div>
    </div>
  </section>

  <section class="grid">
    <article class="card">
      <h2>Section 1 — Percent Change Vs no_break</h2>
      <p class="desc">
        Mean percent change versus <code>no_break</code> using only completed
        runs so far. Error bars show the current standard deviation in
        percentage points.
      </p>
      <div class="chart-wrap tall">
        <canvas id="deltaChart"></canvas>
      </div>
    </article>

    <article class="card">
      <h2>Section 2 — Full Run Times So Far</h2>
      <p class="desc">
        Mean runtime in seconds using the completed runs available so far.
        Error bars show the current standard deviation in seconds.
      </p>
      <div class="chart-wrap tall">
        <canvas id="runtimeChart"></canvas>
      </div>
    </article>

    <article class="card full-width">
      <h2>Section 3 — Split Outcomes So Far</h2>
      <p class="desc">
        Inferred split successes and failures for completed split-only runs. A
        row can still change while the replay is running.
      </p>
      <div class="chart-wrap tall">
        <canvas id="splitChart"></canvas>
      </div>
    </article>

    <article class="card full-width">
      <h2>Section 4 — Live Story</h2>
      <p class="desc">
        These callouts summarize what the current session log already tells us.
      </p>
      <div class="story-grid" id="storyGrid"></div>
    </article>

    <article class="card full-width">
      <h2>Section 5 — Config And Limits</h2>
      <p class="desc">
        Exact configuration when it is available from the surrounding artifacts,
        plus the limitations of a log-only view.
      </p>
      <div class="kv-grid" id="configGrid"></div>
    </article>

    <article class="card full-width">
      <h2>Section 6 — Row Summary</h2>
      <p class="desc">
        Every row from the breakpoint matrix, including pending rows that do not
        have completed timings yet. The row summary includes both the current
        mean runtime and every completed runtime sample seen so far.
      </p>
      <div style="overflow-x:auto;">
        <table class="summary">
          <thead>
            <tr>
              <th>Label</th>
              <th>Status</th>
              <th>Key</th>
              <th>Accesses</th>
              <th>THP</th>
              <th>Runs</th>
              <th>Run Times</th>
              <th>Mean Runtime</th>
              <th>% vs no_break</th>
              <th>Std (pct pts)</th>
              <th>Split</th>
              <th>Warning</th>
            </tr>
          </thead>
          <tbody id="rowsTableBody"></tbody>
        </table>
      </div>
    </article>
  </section>

  <footer id="footerText"></footer>
</div>

<script id="liveLogMetricsData" type="application/json">
{{}}
</script>
<script id="liveLogRowsData" type="application/json">
[]
</script>
<script>
const metrics = JSON.parse(document.getElementById('liveLogMetricsData').textContent);
const rows = JSON.parse(document.getElementById('liveLogRowsData').textContent);

function formatSeconds(value) {{
  if (value === null || value === undefined) return 'n/a';
  return `${{value.toFixed(3)}}s`;
}}

function formatPercent(value) {{
  if (value === null || value === undefined) return 'n/a';
  return `${{value >= 0 ? '+' : ''}}${{value.toFixed(2)}}%`;
}}

function formatInteger(value) {{
  if (value === null || value === undefined) return 'n/a';
  return Number(value).toLocaleString();
}}

function formatRuntimeSamples(values) {{
  if (!Array.isArray(values) || values.length === 0) return '—';
  return values.map((value, index) => `r${{index + 1}}: ${{formatSeconds(value)}}`).join(', ');
}}

function formatEta(seconds) {{
  if (seconds === null || seconds === undefined) return 'n/a';
  const rounded = Math.max(0, Math.round(seconds));
  const h = Math.floor(rounded / 3600);
  const m = Math.floor((rounded % 3600) / 60);
  const s = rounded % 60;
  if (h > 0) return `${{h}}h ${{m}}m ${{s}}s`;
  if (m > 0) return `${{m}}m ${{s}}s`;
  return `${{s}}s`;
}}

function barColorForRow(row) {{
  if (row.row_type === 'base_pages') return 'rgba(243, 183, 77, 0.82)';
  if (row.row_type === 'no_break') return 'rgba(124, 174, 255, 0.82)';
  if (row.row_type === 'hot_split_only') return 'rgba(88, 208, 178, 0.78)';
  if (row.row_type === 'random_split_only') return 'rgba(160, 204, 255, 0.78)';
  return 'rgba(170, 184, 204, 0.78)';
}}

const errorBarPlugin = {{
  id: 'errorBarPlugin',
  afterDatasetsDraw(chart) {{
    const dataset = chart.data.datasets[0];
    if (!dataset || !dataset.errorBars) return;
    const {{ ctx, scales: {{ y }} }} = chart;
    const meta = chart.getDatasetMeta(0);
    ctx.save();
    ctx.lineWidth = 2;
    meta.data.forEach((bar, index) => {{
      const error = dataset.errorBars[index];
      if (!error) return;
      ctx.strokeStyle = 'rgba(219, 231, 255, 0.95)';
      const x = bar.x;
      const topY = y.getPixelForValue(dataset.data[index] + error);
      const bottomY = y.getPixelForValue(dataset.data[index] - error);
      ctx.beginPath();
      ctx.moveTo(x, topY);
      ctx.lineTo(x, bottomY);
      ctx.moveTo(x - 6, topY);
      ctx.lineTo(x + 6, topY);
      ctx.moveTo(x - 6, bottomY);
      ctx.lineTo(x + 6, bottomY);
      ctx.stroke();
    }});
    ctx.restore();
  }},
}};

Chart.register(errorBarPlugin);

document.getElementById('progressValue').textContent =
  `${{formatInteger(metrics.completed_runs)}} / ${{formatInteger(metrics.expected_runs)}}`;
document.getElementById('progressDetail').innerHTML =
  `${{formatPercent(metrics.progress_pct)}} complete across ${{formatInteger(metrics.observed_rows)}} rows. Estimated remaining time: <strong>${{formatEta(metrics.estimated_remaining_seconds)}}</strong>.`;

document.getElementById('currentStageValue').textContent =
  metrics.current_row_label ? metrics.current_row_label : 'complete';
document.getElementById('currentStageDetail').textContent =
  metrics.current_stage
    ? `Current stage: ${{metrics.current_stage}}. Last log line: ${{metrics.last_line}}`
    : `No active row detected. Last log line: ${{metrics.last_line}}`;

document.getElementById('baseVsNoBreakValue').textContent =
  metrics.base_pages_runtime_s !== null && metrics.no_break_runtime_s !== null
    ? formatPercent(metrics.base_pages_pct_vs_no_break)
    : 'n/a';
document.getElementById('baseVsNoBreakDetail').textContent =
  metrics.base_pages_runtime_s !== null && metrics.no_break_runtime_s !== null
    ? `base_pages ${{formatSeconds(metrics.base_pages_runtime_s)}} vs no_break ${{formatSeconds(metrics.no_break_runtime_s)}} so far.`
    : 'Waiting for both base_pages and no_break samples.';

document.getElementById('splitOutcomeValue').textContent =
  `${{formatInteger(metrics.inferred_split_successes)}} / ${{formatInteger(metrics.inferred_split_successes + metrics.inferred_split_failures)}}`;
document.getElementById('splitOutcomeDetail').textContent =
  `${{formatInteger(metrics.split_rows_with_failures)}} rows have logged split failures so far.`;

const storyItems = [
  {{
    title: 'Observed Layout',
    body: `This live run currently shows ${{formatInteger(metrics.observed_rows)}} rows and ${{formatInteger(metrics.runs_per_row)}} timed runs per row. The session log itself reports ${{formatInteger(metrics.observed_row_total_from_log)}} rows.`,
  }},
  {{
    title: 'Work Completed So Far',
    body: `${{formatInteger(metrics.completed_rows)}} rows are fully complete and ${{formatInteger(metrics.started_rows)}} rows have at least one finished run.`,
  }},
  {{
    title: 'Runtime Range',
    body: metrics.runtime_min_s !== null
      ? `Completed runtimes currently span ${{formatSeconds(metrics.runtime_min_s)}} to ${{formatSeconds(metrics.runtime_max_s)}} with an overall mean of ${{formatSeconds(metrics.runtime_mean_s)}}.`
      : 'No completed runtimes are available yet.',
  }},
  {{
    title: 'Limits Of This View',
    body: metrics.limitations.join(' '),
  }},
];

const storyGrid = document.getElementById('storyGrid');
storyItems.forEach((item) => {{
  const card = document.createElement('div');
  card.className = 'story';
  card.innerHTML = `<strong>${{item.title}}</strong><p>${{item.body}}</p>`;
  storyGrid.appendChild(card);
}});

const configItems = [
  ['Experiment', metrics.experiment_name],
  ['Workload', metrics.workload_label],
  ['Session Log', metrics.session_log_path],
  ['Breakpoints', metrics.breakpoints_path],
  ['Trace', metrics.trace_path],
  ['Report', metrics.report_path ?? 'n/a'],
  ['Generated', metrics.generated_at_utc],
  ['Log Mtime', metrics.session_log_mtime_utc],
];

const configGrid = document.getElementById('configGrid');
configItems.forEach(([label, value]) => {{
  const item = document.createElement('div');
  item.className = 'kv';
  item.innerHTML = `<div class="label">${{label}}</div><div class="value mono pre-wrap">${{value}}</div>`;
  configGrid.appendChild(item);
}});

const rowsTableBody = document.getElementById('rowsTableBody');
rows.forEach((row) => {{
  const tr = document.createElement('tr');
  const badgeClass = row.status === 'complete' ? 'complete' : row.status === 'in_progress' ? 'progress' : 'pending';
  const warningText = row.latest_warning ? row.latest_warning : '—';
  tr.innerHTML = `
    <td><strong>${{row.short_label}}</strong></td>
    <td><span class="badge ${{badgeClass}}">${{row.status}}</span></td>
    <td class="mono">${{row.key ?? '—'}}</td>
    <td>${{row.access_count !== null ? formatInteger(row.access_count) : '—'}}</td>
    <td><code>${{row.thp_mode}}</code></td>
    <td>${{formatInteger(row.completed_runs)}} / ${{formatInteger(row.total_runs)}}</td>
    <td class="mono pre-wrap">${{formatRuntimeSamples(row.runtime_samples)}}</td>
    <td>${{formatSeconds(row.runtime_mean)}}</td>
    <td>${{formatPercent(row.runtime_pct_vs_no_break)}}</td>
    <td>${{formatPercent(row.runtime_std_pct_vs_no_break)}}</td>
    <td>${{row.row_type === 'hot_split_only' || row.row_type === 'random_split_only' ? `${{formatInteger(row.inferred_split_successes)}} success / ${{formatInteger(row.inferred_split_failures)}} fail` : '—'}}</td>
    <td class="mono pre-wrap">${{warningText}}</td>
  `;
  rowsTableBody.appendChild(tr);
}});

const chartRows = rows.filter((row) => row.runtime_pct_vs_no_break !== null);
new Chart(document.getElementById('deltaChart'), {{
  type: 'bar',
  data: {{
    labels: chartRows.map((row) => row.short_label),
    datasets: [{{
      label: 'Mean delta vs no_break so far (%)',
      data: chartRows.map((row) => row.runtime_pct_vs_no_break),
      errorBars: chartRows.map((row) => row.runtime_std_pct_vs_no_break ?? 0),
      backgroundColor: chartRows.map((row) => barColorForRow(row)),
      borderRadius: 10,
    }}],
  }},
  options: {{
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label(context) {{
            const row = chartRows[context.dataIndex];
            return [
              `delta vs no_break: ${{formatPercent(row.runtime_pct_vs_no_break)}}`,
              `std: ${{formatPercent(row.runtime_std_pct_vs_no_break)}}`,
              `mean runtime: ${{formatSeconds(row.runtime_mean)}}`,
              `completed runs: ${{row.completed_runs}}/${{row.total_runs}}`,
              `split successes: ${{row.inferred_split_successes}}/${{row.total_runs}}`,
            ];
          }},
        }},
      }},
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#d7e4f7', maxRotation: 55, minRotation: 25 }},
        grid: {{ display: false }},
      }},
      y: {{
        ticks: {{ color: '#d7e4f7' }},
        grid: {{ color: 'rgba(156, 176, 200, 0.12)' }},
        title: {{ display: true, text: '% vs no_break', color: '#d7e4f7' }},
      }},
    }},
  }},
}});

const runtimeRows = rows.filter((row) => row.runtime_mean !== null);
new Chart(document.getElementById('runtimeChart'), {{
  type: 'bar',
  data: {{
    labels: runtimeRows.map((row) => row.short_label),
    datasets: [{{
      label: 'Mean runtime so far (s)',
      data: runtimeRows.map((row) => row.runtime_mean),
      errorBars: runtimeRows.map((row) => row.runtime_std ?? 0),
      backgroundColor: runtimeRows.map((row) => barColorForRow(row)),
      borderRadius: 10,
    }}],
  }},
  options: {{
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label(context) {{
            const row = runtimeRows[context.dataIndex];
            return [
              `mean runtime: ${{formatSeconds(row.runtime_mean)}}`,
              `std: ${{formatSeconds(row.runtime_std)}}`,
              `delta vs no_break: ${{formatPercent(row.runtime_pct_vs_no_break)}}`,
              `completed runs: ${{row.completed_runs}}/${{row.total_runs}}`,
              `split successes: ${{row.inferred_split_successes}}/${{row.total_runs}}`,
            ];
          }},
        }},
      }},
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#d7e4f7', maxRotation: 55, minRotation: 25 }},
        grid: {{ display: false }},
      }},
      y: {{
        ticks: {{ color: '#d7e4f7' }},
        grid: {{ color: 'rgba(156, 176, 200, 0.12)' }},
        title: {{ display: true, text: 'Runtime (seconds)', color: '#d7e4f7' }},
      }},
    }},
  }},
}});

const splitRows = rows.filter((row) => row.row_type === 'hot_split_only' || row.row_type === 'random_split_only');
new Chart(document.getElementById('splitChart'), {{
  type: 'bar',
  data: {{
    labels: splitRows.map((row) => row.short_label),
    datasets: [
      {{
        label: 'Inferred successes',
        data: splitRows.map((row) => row.inferred_split_successes),
        backgroundColor: 'rgba(88, 208, 178, 0.76)',
        borderRadius: 8,
      }},
      {{
        label: 'Inferred failures',
        data: splitRows.map((row) => row.inferred_split_failures),
        backgroundColor: 'rgba(255, 112, 128, 0.76)',
        borderRadius: 8,
      }},
    ],
  }},
  options: {{
    maintainAspectRatio: false,
    plugins: {{
      legend: {{
        labels: {{ color: '#d7e4f7' }},
      }},
      tooltip: {{
        callbacks: {{
          afterBody(items) {{
            const row = splitRows[items[0].dataIndex];
            return [
              `completed split runs: ${{row.completed_split_runs}}/${{row.total_runs}}`,
              `retry lines: ${{row.inferred_retry_count_total}}`,
              `observed max attempts: ${{row.observed_max_attempts}}`,
            ];
          }},
        }},
      }},
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#d7e4f7', maxRotation: 55, minRotation: 25 }},
        grid: {{ display: false }},
      }},
      y: {{
        ticks: {{ color: '#d7e4f7', precision: 0 }},
        grid: {{ color: 'rgba(156, 176, 200, 0.12)' }},
        title: {{ display: true, text: 'Completed runs so far', color: '#d7e4f7' }},
      }},
    }},
  }},
}});

document.getElementById('footerText').textContent =
  `Generated at ${{metrics.generated_at_utc}} from the live session log. This page is a preview for an in-progress run and should be regenerated as the log grows.`;
</script>
</body>
</html>
"""


def _write_dashboard(
    *,
    output_html: Path,
    title: str,
    metrics: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    """Write the standalone HTML dashboard with embedded live data."""

    html = _build_dashboard_html(title)
    html = _replace_inline_json_script(html, "liveLogMetricsData", metrics)
    html = _replace_inline_json_script(html, "liveLogRowsData", rows)
    output_html.write_text(html, encoding="utf-8")


def build_live_dashboard(
    *,
    session_log_path: Path,
    breakpoints_path: Path,
    trace_path: Path,
    output_html: Path,
    experiment_name: str,
    workload_label: str,
    hot_keys: int,
    random_rows: int,
    runs_per_row: int,
    report_path: Path | None = None,
) -> None:
    """Generate the live replay dashboard for an in-progress session log."""

    parsed_log = _parse_session_log(session_log_path)
    rows = _classify_rows(
        breakpoints_path,
        trace_path,
        hot_keys=hot_keys,
        random_rows=random_rows,
        runs_per_row=runs_per_row,
        parsed_log=parsed_log,
    )
    metrics = _build_metrics(
        experiment_name=experiment_name,
        workload_label=workload_label,
        session_log_path=session_log_path,
        breakpoints_path=breakpoints_path,
        trace_path=trace_path,
        report_path=report_path,
        rows=rows,
        parsed_log=parsed_log,
        hot_keys=hot_keys,
        random_rows=random_rows,
        runs_per_row=runs_per_row,
    )
    output_html.parent.mkdir(parents=True, exist_ok=True)
    _write_dashboard(
        output_html=output_html,
        title=f"{experiment_name} Live Replay Dashboard",
        metrics=metrics,
        rows=rows,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a log-only live dashboard for an in-progress replay session.",
    )
    parser.add_argument("--session-log", type=Path, required=True, help="Path to the live replay session log.")
    parser.add_argument("--breakpoints", type=Path, required=True, help="Path to the accepted breakpoint parquet.")
    parser.add_argument("--trace", type=Path, required=True, help="Path to the accepted monitor trace.")
    parser.add_argument("--output-html", type=Path, required=True, help="Output path for the standalone HTML dashboard.")
    parser.add_argument("--experiment-name", type=str, required=True, help="Short experiment label for the page title.")
    parser.add_argument("--workload-label", type=str, required=True, help="Human-readable workload description.")
    parser.add_argument("--hot-keys", type=int, required=True, help="Number of hot split-only rows in the matrix.")
    parser.add_argument("--random-rows", type=int, required=True, help="Number of random split-only rows in the matrix.")
    parser.add_argument("--runs", type=int, required=True, help="Timed runs per replay row.")
    parser.add_argument("--report", type=Path, default=None, help="Optional report path to display in the config section.")
    return parser


def main() -> None:
    """Entry point for the live log dashboard CLI."""

    parser = _build_parser()
    args = parser.parse_args()

    for path in (args.session_log, args.breakpoints, args.trace):
        if not path.is_file():
            parser.error(f"File not found: {path}")
    if args.report is not None and not args.report.is_file():
        parser.error(f"Report file not found: {args.report}")

    build_live_dashboard(
        session_log_path=args.session_log,
        breakpoints_path=args.breakpoints,
        trace_path=args.trace,
        output_html=args.output_html,
        experiment_name=args.experiment_name,
        workload_label=args.workload_label,
        hot_keys=args.hot_keys,
        random_rows=args.random_rows,
        runs_per_row=args.runs,
        report_path=args.report,
    )


if __name__ == "__main__":
    main()
