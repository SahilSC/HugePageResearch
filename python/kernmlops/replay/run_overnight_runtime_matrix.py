"""Run the overnight runtime-only Redis replay matrix.

This script is intentionally narrow. It captures the exact runtime-only plan
for the April 9, 2026 overnight run:

- no ``--collector-config``
- no dTLB counters or any other hardware counters
- per-workload quick verification with ``2-2-1`` and ``1`` timed run
- full experiments with ``2-14-2`` and ``3`` timed runs
- manifest, dashboard, and report updates after each full experiment

It is designed to run inside the user's existing experiment container and to be
launched from a detached host-side ``tmux`` session.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _PACKAGE_ROOT = Path(__file__).resolve().parents[1]
    _PACKAGE_ROOT_STR = str(_PACKAGE_ROOT)
    if _PACKAGE_ROOT_STR not in sys.path:
        sys.path.insert(0, _PACKAGE_ROOT_STR)

import polars as pl
from replay.replay_trace import BREAK_PAGE_MAX_ATTEMPTS
from redis_runtime import load_repo_redis_endpoint


REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
TRACE_DIR = DATA_DIR / "redis_traces"
MANIFEST_DIR = DATA_DIR / "experiment_manifests"
TEMP_ANALYSIS_DIR = REPO_ROOT / "temp_data_analysis"
REPORT_PATH = REPO_ROOT / "reports" / "collector" / "replay_overnight_2026-04-09.md"

MIN_FREE_BYTES = 10 * 1024**3
TARGET_RUNTIME_S = 60.0
TARGET_RUNTIME_MIN_S = 55.0
TARGET_RUNTIME_MAX_S = 65.0
MAX_CALIBRATION_ATTEMPTS = 3
OVERNIGHT_BUDGET_S = int(8.25 * 3600)
OPTIONAL_EXPERIMENT_ESTIMATE_S = int(2.0 * 3600)

VERIFY_RECORD_COUNT = 1024
VERIFY_OPERATION_COUNT = 4096
VERIFY_HOT_KEYS = 2
VERIFY_RANDOM_ROWS = 1
VERIFY_RUNS = 1

FULL_HOT_KEYS = 14
FULL_RANDOM_ROWS = 2
FULL_RUNS = 3

_REQUIRED_RESULT_PREFIXES = (
    "runtime_s_",
    "commands_replayed_",
    "split_events_",
    "split_successes_",
    "split_failures_",
    "split_syscall_attempts_",
    "split_max_attempts_",
)

_OWNER_UID = REPO_ROOT.stat().st_uid
_OWNER_GID = REPO_ROOT.stat().st_gid


@dataclass(frozen=True)
class WorkloadMix:
    """Exact YCSB proportions for one capture.

    Attributes:
        read: Read proportion passed through ``READ_PROPORTION``.
        delete: Delete proportion passed through ``DELETE_PROPORTION``.
        insert: Insert proportion passed through ``INSERT_PROPORTION``.
        update: Update proportion passed through ``UPDATE_PROPORTION``.
        scan: Scan proportion passed through ``SCAN_PROPORTION``.
        readmodifywrite: Read-modify-write proportion passed through
            ``READMODIFYWRITE_PROPORTION``.
    """

    read: float
    delete: float
    insert: float = 0.0
    update: float = 0.0
    scan: float = 0.0
    readmodifywrite: float = 0.0

    def env(self) -> dict[str, str]:
        """Return stringified environment overrides for the capture script."""

        return {
            "READ_PROPORTION": f"{self.read:.2f}",
            "DELETE_PROPORTION": f"{self.delete:.2f}",
            "INSERT_PROPORTION": f"{self.insert:.2f}",
            "UPDATE_PROPORTION": f"{self.update:.2f}",
            "SCAN_PROPORTION": f"{self.scan:.2f}",
            "READMODIFYWRITE_PROPORTION": f"{self.readmodifywrite:.2f}",
        }

    def label(self) -> str:
        """Return a short human-readable workload label."""

        parts: list[str] = []
        if self.read:
            parts.append(f"{int(self.read * 100)}% read")
        if self.delete:
            parts.append(f"{int(self.delete * 100)}% delete")
        if self.insert:
            parts.append(f"{int(self.insert * 100)}% insert")
        if self.update:
            parts.append(f"{int(self.update * 100)}% update")
        if self.scan:
            parts.append(f"{int(self.scan * 100)}% scan")
        if self.readmodifywrite:
            parts.append(f"{int(self.readmodifywrite * 100)}% readmodifywrite")
        return " / ".join(parts)

    def manifest_dict(self) -> dict[str, float]:
        """Return the workload mix as a JSON-safe mapping."""

        return {
            "read_proportion": self.read,
            "delete_proportion": self.delete,
            "insert_proportion": self.insert,
            "update_proportion": self.update,
            "scan_proportion": self.scan,
            "readmodifywrite_proportion": self.readmodifywrite,
        }


@dataclass(frozen=True)
class ExperimentSpec:
    """One planned experiment in the overnight matrix.

    Attributes:
        experiment_name: Short stable identifier such as ``control``.
        slug: Artifact slug used in paths.
        mix: YCSB operation mix for the capture.
        record_count: Initial YCSB record count.
        operation_count: Initial YCSB operation count.
        record_ratio: Record count divided by operation count. Retuning keeps
            this same ratio unless the user requested a different formula.
        notes: Short notes copied into manifests and the report.
    """

    experiment_name: str
    slug: str
    mix: WorkloadMix
    record_count: int
    operation_count: int
    record_ratio: float
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalibrationAttempt:
    """One calibration attempt for a full-sized capture.

    Attributes:
        record_count: Record count used for the capture.
        operation_count: Operation count used for the capture.
        base_pages_runtime_s: Measured base-pages runtime from the 5-row replay.
        accepted: Whether the result fell inside the target runtime band.
    """

    record_count: int
    operation_count: int
    base_pages_runtime_s: float
    accepted: bool


@dataclass(frozen=True)
class ExperimentOutcome:
    """Artifacts and summary metrics for one completed full experiment."""

    spec: ExperimentSpec
    metrics: dict[str, Any]
    manifest_path: Path
    rows_path: Path
    summary_path: Path
    dashboard_path: Path
    results_path: Path
    breakpoints_path: Path
    trace_path: Path
    snapshot_path: Path
    calibration_attempts: tuple[CalibrationAttempt, ...]


def _utc_now_text() -> str:
    """Return an ISO-8601 UTC timestamp with a trailing ``Z``."""

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _log(message: str) -> None:
    """Print a timestamped progress line for the tmux pane."""

    print(f"[{_utc_now_text()}] {message}", flush=True)


def _repo_redis_cli_command(*args: str) -> list[str]:
    """Build a ``redis-cli`` command for the repo-managed Redis endpoint."""

    endpoint = load_repo_redis_endpoint()
    return ["redis-cli", "-h", endpoint.host, "-p", str(endpoint.port), *args]


def _shell_join(cmd: list[str]) -> str:
    """Return a shell-escaped command string for manifests and reports."""

    return shlex.join(cmd)


def _chown_path(path: Path) -> None:
    """Restore a host-friendly owner on one generated file.

    The detached session runs as root inside the container so the bind-mounted
    outputs would otherwise become root-owned on the host checkout.
    """

    if not path.exists():
        return
    os.chown(path, _OWNER_UID, _OWNER_GID)


def _safe_unlink(path: Path) -> None:
    """Delete an old output file when this run is about to replace it."""

    if path.exists():
        path.unlink()


def _ensure_directories() -> None:
    """Create the output directories used by the overnight matrix."""

    for path in (DATA_DIR, TRACE_DIR, MANIFEST_DIR, TEMP_ANALYSIS_DIR, REPORT_PATH.parent):
        path.mkdir(parents=True, exist_ok=True)
        _chown_path(path)


def _ensure_min_free_space() -> None:
    """Fail fast when the repo volume drops below the required free space."""

    usage = shutil.disk_usage(REPO_ROOT)
    if usage.free < MIN_FREE_BYTES:
        free_gib = usage.free / (1024**3)
        raise RuntimeError(
            f"Need at least 10 GiB free before a full capture; only {free_gib:.2f} GiB remain."
        )


def _run_command(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    log_path: Path | None = None,
) -> None:
    """Run one subprocess from the repo root and fail fast on errors.

    Args:
        cmd: Exact argv vector.
        env: Optional environment overrides.
        log_path: Optional path that receives combined stdout/stderr.

    Raises:
        RuntimeError: If the subprocess exits non-zero.
    """

    merged_env = os.environ.copy()
    if env is not None:
        merged_env.update(env)

    _log(f"Running: {_shell_join(cmd)}")
    if log_path is None:
        completed = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=merged_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if completed.stdout:
            print(completed.stdout, end="", flush=True)
        if completed.returncode != 0:
            raise RuntimeError(
                f"Command failed with exit code {completed.returncode}: {_shell_join(cmd)}"
            )
        return

    _safe_unlink(log_path)
    with open(log_path, "w", encoding="utf-8") as log_file:
        log_file.write(f"$ {_shell_join(cmd)}\n\n")
        completed = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=merged_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    _chown_path(log_path)
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {completed.returncode}: {_shell_join(cmd)} "
            f"(see {log_path})"
        )


def _capture_output(cmd: list[str]) -> str:
    """Return stdout from a small fail-fast subprocess."""

    completed = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {completed.returncode}: {_shell_join(cmd)}\n"
            f"{completed.stdout}"
        )
    return completed.stdout


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write formatted JSON and restore host-friendly ownership."""

    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _chown_path(path)


def _append_report(section: str) -> None:
    """Append one markdown section to the overnight report."""

    with open(REPORT_PATH, "a", encoding="utf-8") as handle:
        handle.write(section)
        if not section.endswith("\n"):
            handle.write("\n")
        handle.write("\n")
    _chown_path(REPORT_PATH)


def _report_header() -> str:
    """Return the fixed report header written before any experiments run."""

    return """# Replay Overnight 2026-04-09

## Overview

This report tracks the April 9, 2026 overnight Redis replay matrix.

The session is runtime-only:

- no `--collector-config`
- no dTLB measurements
- no hardware counters of any kind

The overnight runner performs:

- preflight checks against the existing `clever_hofstadter` container
- one quick verification replay per workload (`2-2-1`, `1` run)
- one full-size calibration replay where needed (`2-2-1`, `1` run)
- one full experiment replay (`2-14-2`, `3` runs)
- manifest, HTML dashboard, JSON summaries, and markdown summaries

## Code And Runbook Changes

- `python/kernmlops/replay/replay_trace.py` now writes runtime-only split stats into the result parquet for every run.
- `temp_data_analysis/replay_runtime_experiment_dashboard.py` renders the generic runtime-only HTML dashboard with std bars, split-success labels, config metadata, and footer metadata.
- `replay.md` now documents the runtime-only replay columns, the runtime-only dashboard flow, and the host-side `tmux` workflow.
- The overnight run is driven by `python/kernmlops/replay/run_overnight_runtime_matrix.py`.
"""


def _record_preflight_section() -> None:
    """Write the report header plus preflight facts for this session."""

    REPORT_PATH.write_text(_report_header(), encoding="utf-8")
    _chown_path(REPORT_PATH)

    free_gib = shutil.disk_usage(REPO_ROOT).free / (1024**3)
    ping_output = _capture_output(_repo_redis_cli_command("ping")).strip()
    vaptr_output = _capture_output(
        _repo_redis_cli_command("COMMAND", "INFO", "VAPTR")
    ).strip()

    _append_report(
        "\n".join(
            [
                "## Preflight",
                "",
                f"- container-side start time: `{_utc_now_text()}`",
                f"- redis ping: `{ping_output}`",
                f"- `VAPTR` command present: `{'vaptr' in vaptr_output.lower()}`",
                f"- free space on repo volume at start: `{free_gib:.2f} GiB`",
                "- all overnight replays are runtime-only and do not pass `--collector-config`",
            ]
        )
    )


def _build_capture_command(record_count: int, operation_count: int, mix: WorkloadMix) -> str:
    """Return the exact capture command string embedded into manifests."""

    env_bits = [
        f"RECORD_COUNT={record_count}",
        f"OPERATION_COUNT={operation_count}",
        *(f"{name}={value}" for name, value in mix.env().items()),
    ]
    return " ".join(env_bits + ["bash", "python/kernmlops/replay/capture_redis_trace.sh"])


def _capture_trace(
    stage_slug: str,
    *,
    record_count: int,
    operation_count: int,
    mix: WorkloadMix,
) -> tuple[Path, Path, Path]:
    """Capture a fresh Redis snapshot plus monitor log for one stage.

    Returns:
        ``(snapshot_path, trace_path, capture_log_path)`` for the preserved
        artifact set.
    """

    capture_log_path = DATA_DIR / f"{stage_slug}_capture.log"
    snapshot_path = TRACE_DIR / f"{stage_slug}_snapshot.rdb"
    trace_path = TRACE_DIR / f"{stage_slug}_monitor_run.log"

    _safe_unlink(snapshot_path)
    _safe_unlink(trace_path)

    env = {
        "RECORD_COUNT": str(record_count),
        "OPERATION_COUNT": str(operation_count),
        **mix.env(),
    }
    _run_command(
        ["bash", "python/kernmlops/replay/capture_redis_trace.sh"],
        env=env,
        log_path=capture_log_path,
    )

    generic_snapshot = TRACE_DIR / "snapshot.rdb"
    generic_trace = TRACE_DIR / "monitor_run.log"
    if not generic_snapshot.exists() or not generic_trace.exists():
        raise RuntimeError(
            "capture_redis_trace.sh completed without producing snapshot.rdb and monitor_run.log"
        )

    shutil.move(generic_snapshot, snapshot_path)
    shutil.move(generic_trace, trace_path)
    _chown_path(snapshot_path)
    _chown_path(trace_path)
    return snapshot_path, trace_path, capture_log_path


def _generate_breakpoints(
    trace_path: Path,
    output_path: Path,
    *,
    hot_keys: int,
    random_rows: int,
) -> None:
    """Generate one replay breakpoint parquet from a monitor log."""

    _safe_unlink(output_path)
    cmd = [
        sys.executable,
        "python/kernmlops/replay/generate_breakpoints.py",
        str(trace_path),
        "--output",
        str(output_path),
        "--hot-keys",
        str(hot_keys),
        "--random-rows",
        str(random_rows),
    ]
    _run_command(cmd)
    _chown_path(output_path)


def _run_replay(
    snapshot_path: Path,
    trace_path: Path,
    breakpoints_path: Path,
    results_path: Path,
    *,
    runs: int,
    log_path: Path,
) -> None:
    """Replay one breakpoint matrix without any hardware counters."""

    _safe_unlink(results_path)
    cmd = [
        sys.executable,
        "python/kernmlops/replay/replay_trace.py",
        str(snapshot_path),
        str(trace_path),
        "--breakpoints",
        str(breakpoints_path),
        "--output",
        str(results_path),
        "--runs",
        str(runs),
        "-v",
    ]
    _run_command(cmd, log_path=log_path)
    _chown_path(results_path)


def _required_columns_for_runs(runs: int) -> set[str]:
    """Return the exact runtime-only columns expected for a given run count."""

    columns = {"row_index", "thp_mode"}
    for run in range(1, runs + 1):
        for prefix in _REQUIRED_RESULT_PREFIXES:
            columns.add(f"{prefix}{run}")
    return columns


def _assert_runtime_only_results(results_path: Path, *, runs: int) -> pl.DataFrame:
    """Validate the runtime-only replay result shape and required columns.

    Raises:
        RuntimeError: If the parquet is missing required runtime-only columns,
            contains dTLB columns, or does not keep the first row in THP
            `never`.
    """

    df = pl.read_parquet(results_path)
    missing = sorted(_required_columns_for_runs(runs) - set(df.columns))
    if missing:
        raise RuntimeError(
            f"Result parquet is missing required runtime-only columns: {missing}"
        )
    if any(column.startswith("dtlb_") for column in df.columns):
        raise RuntimeError("Result parquet unexpectedly contains dTLB columns")

    row0 = df.filter(pl.col("row_index") == 0)
    if row0.height != 1 or row0["thp_mode"][0] != "never":
        raise RuntimeError("Replay row_index=0 must be the THP-never base_pages row")
    return df


def _assert_verification_acceptance(results_path: Path) -> None:
    """Fail fast when the quick verification result misses a required signal."""

    df = _assert_runtime_only_results(results_path, runs=VERIFY_RUNS)
    split_signal = df.filter(
        (pl.col("row_index") >= 2)
        & (
            (pl.col("split_successes_1") > 0)
            | (pl.col("split_failures_1") > 0)
        )
    )
    if split_signal.height == 0:
        raise RuntimeError(
            "Verification result did not record any split success or split failure"
        )


def _base_pages_runtime(results_path: Path) -> float:
    """Return the row-zero runtime from a 1-run calibration parquet."""

    df = _assert_runtime_only_results(results_path, runs=1)
    return float(df.filter(pl.col("row_index") == 0)["runtime_s_1"][0])


def _verification_manifest(
    *,
    spec: ExperimentSpec,
    stage_slug: str,
    record_count: int,
    operation_count: int,
    trace_path: Path,
    results_path: Path,
    started_at_utc: str,
    finished_at_utc: str,
    elapsed_seconds: float,
    capture_command: str,
    replay_command: str,
    dashboard_command: str,
) -> dict[str, Any]:
    """Build the manifest used by the runtime-only dashboard generator."""

    return {
        "experiment_name": f"{spec.experiment_name} verification",
        "workload_label": spec.mix.label(),
        "row_layout": {
            "hot_keys": VERIFY_HOT_KEYS,
            "random_rows": VERIFY_RANDOM_ROWS,
            "runs": VERIFY_RUNS,
        },
        "capture": {
            "record_count": record_count,
            "operation_count": operation_count,
            **spec.mix.manifest_dict(),
        },
        "verification": {
            "record_count": VERIFY_RECORD_COUNT,
            "operation_count": VERIFY_OPERATION_COUNT,
            "hot_keys": VERIFY_HOT_KEYS,
            "random_rows": VERIFY_RANDOM_ROWS,
            "runs": VERIFY_RUNS,
        },
        "replay": {
            "break_page_max_attempts": BREAK_PAGE_MAX_ATTEMPTS,
            "collector_config": None,
            "hardware_counters": [],
        },
        "commands": {
            "capture": capture_command,
            "replay": replay_command,
            "dashboard": dashboard_command,
        },
        "artifacts": {
            "trace": str(trace_path),
            "results": str(results_path),
        },
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
        "elapsed_seconds": elapsed_seconds,
        "notes": [
            "Quick runtime-only verification for this workload.",
            "No dTLB or other hardware counters are collected.",
        ],
        "insights": [
            "Verification confirms the runtime-only replay path before the full overnight replay.",
        ],
    }


def _build_manifest(
    *,
    spec: ExperimentSpec,
    record_count: int,
    operation_count: int,
    trace_path: Path,
    results_path: Path,
    row_layout_hot_keys: int,
    row_layout_random_rows: int,
    row_layout_runs: int,
    started_at_utc: str,
    finished_at_utc: str,
    elapsed_seconds: float,
    capture_command: str,
    replay_command: str,
    dashboard_command: str,
    notes: list[str],
    insights: list[str],
) -> dict[str, Any]:
    """Build the full-experiment dashboard manifest."""

    return {
        "experiment_name": spec.experiment_name,
        "workload_label": spec.mix.label(),
        "row_layout": {
            "hot_keys": row_layout_hot_keys,
            "random_rows": row_layout_random_rows,
            "runs": row_layout_runs,
        },
        "capture": {
            "record_count": record_count,
            "operation_count": operation_count,
            **spec.mix.manifest_dict(),
        },
        "verification": {
            "record_count": VERIFY_RECORD_COUNT,
            "operation_count": VERIFY_OPERATION_COUNT,
            "hot_keys": VERIFY_HOT_KEYS,
            "random_rows": VERIFY_RANDOM_ROWS,
            "runs": VERIFY_RUNS,
        },
        "replay": {
            "break_page_max_attempts": BREAK_PAGE_MAX_ATTEMPTS,
            "collector_config": None,
            "hardware_counters": [],
        },
        "commands": {
            "capture": capture_command,
            "replay": replay_command,
            "dashboard": dashboard_command,
        },
        "artifacts": {
            "trace": str(trace_path),
            "results": str(results_path),
        },
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
        "elapsed_seconds": elapsed_seconds,
        "notes": notes,
        "insights": insights,
    }


def _dashboard_paths(stage_slug: str) -> tuple[Path, Path, Path, Path]:
    """Return the four dashboard artifact paths for one stage slug."""

    return (
        TEMP_ANALYSIS_DIR / f"{stage_slug}_dashboard.html",
        TEMP_ANALYSIS_DIR / f"{stage_slug}_metrics.json",
        TEMP_ANALYSIS_DIR / f"{stage_slug}_rows.json",
        TEMP_ANALYSIS_DIR / f"{stage_slug}_summary.md",
    )


def _render_dashboard(
    *,
    stage_slug: str,
    results_path: Path,
    trace_path: Path,
    manifest_path: Path,
) -> tuple[Path, Path, Path, Path]:
    """Run the generic runtime-only dashboard generator for one result parquet."""

    html_path, metrics_path, rows_path, summary_path = _dashboard_paths(stage_slug)
    for path in (html_path, metrics_path, rows_path, summary_path):
        _safe_unlink(path)

    cmd = [
        sys.executable,
        "temp_data_analysis/replay_runtime_experiment_dashboard.py",
        "--results",
        str(results_path),
        "--trace",
        str(trace_path),
        "--manifest",
        str(manifest_path),
        "--output-html",
        str(html_path),
        "--output-metrics",
        str(metrics_path),
        "--output-rows",
        str(rows_path),
        "--output-summary",
        str(summary_path),
    ]
    _run_command(cmd)
    for path in (html_path, metrics_path, rows_path, summary_path):
        _chown_path(path)
    return html_path, metrics_path, rows_path, summary_path


def _load_json(path: Path) -> dict[str, Any]:
    """Load one JSON file into memory."""

    return json.loads(path.read_text(encoding="utf-8"))


def _build_insights(metrics: dict[str, Any], rows_path: Path) -> list[str]:
    """Build a short list of high-signal observations for the dashboard footer."""

    rows = _load_json(rows_path)
    hot_rows = [row for row in rows if row["row_type"] == "hot_split_only"]
    random_rows = [row for row in rows if row["row_type"] == "random_split_only"]
    zero_success_rows = [
        row["short_label"]
        for row in hot_rows + random_rows
        if int(row["split_successes_total"]) == 0
    ]

    insights = [
        (
            "Base pages versus no_break runtime delta: "
            f"{metrics['base_pages_pct_vs_no_break']:.2f}%."
        ),
        (
            "Hot split mean runtime delta versus no_break: "
            f"{metrics['hot_split_mean_runtime_pct']:.2f}%."
            if metrics["hot_split_mean_runtime_pct"] is not None
            else "Hot split mean runtime delta versus no_break was not available."
        ),
        (
            "Random split mean runtime delta versus no_break: "
            f"{metrics['random_split_mean_runtime_pct']:.2f}%."
            if metrics["random_split_mean_runtime_pct"] is not None
            else "Random split mean runtime delta versus no_break was not available."
        ),
        (
            "Hot-row split success rate: "
            f"{metrics['hot_split_success_rate_pct']:.2f}%."
            if metrics["hot_split_success_rate_pct"] is not None
            else "Hot-row split success rate was not available."
        ),
        (
            "Configured max split attempts: "
            f"{metrics['configured_max_split_attempts']}; observed max split attempts: "
            f"{metrics['observed_max_split_attempts']}."
        ),
    ]
    if zero_success_rows:
        insights.append(
            "Zero-success split rows were omitted from the main runtime chart: "
            + ", ".join(zero_success_rows)
            + "."
        )
    return insights


def _run_verification(spec: ExperimentSpec) -> None:
    """Run the quick runtime-only verification for one workload."""

    _log(f"Starting verification for {spec.experiment_name}")
    stage_slug = f"{spec.slug}_verification"
    stage_started_utc = _utc_now_text()
    stage_start = time.monotonic()

    snapshot_path, trace_path, capture_log_path = _capture_trace(
        stage_slug,
        record_count=VERIFY_RECORD_COUNT,
        operation_count=VERIFY_OPERATION_COUNT,
        mix=spec.mix,
    )
    breakpoints_path = DATA_DIR / f"{stage_slug}_breakpoints_5rows.parquet"
    results_path = DATA_DIR / f"{stage_slug}_results_5rows.parquet"
    runtime_log_path = DATA_DIR / f"{stage_slug}_runtime.log"

    _generate_breakpoints(
        trace_path,
        breakpoints_path,
        hot_keys=VERIFY_HOT_KEYS,
        random_rows=VERIFY_RANDOM_ROWS,
    )
    _run_replay(
        snapshot_path,
        trace_path,
        breakpoints_path,
        results_path,
        runs=VERIFY_RUNS,
        log_path=runtime_log_path,
    )
    _assert_verification_acceptance(results_path)

    manifest_path = MANIFEST_DIR / f"{stage_slug}.json"
    html_path, metrics_path, rows_path, summary_path = _dashboard_paths(stage_slug)
    replay_command = _shell_join(
        [
            sys.executable,
            "python/kernmlops/replay/replay_trace.py",
            str(snapshot_path),
            str(trace_path),
            "--breakpoints",
            str(breakpoints_path),
            "--output",
            str(results_path),
            "--runs",
            str(VERIFY_RUNS),
            "-v",
        ]
    )
    dashboard_command = _shell_join(
        [
            sys.executable,
            "temp_data_analysis/replay_runtime_experiment_dashboard.py",
            "--results",
            str(results_path),
            "--trace",
            str(trace_path),
            "--manifest",
            str(manifest_path),
            "--output-html",
            str(html_path),
            "--output-metrics",
            str(metrics_path),
            "--output-rows",
            str(rows_path),
            "--output-summary",
            str(summary_path),
        ]
    )
    finished_at_utc = _utc_now_text()
    elapsed_seconds = time.monotonic() - stage_start
    manifest = _verification_manifest(
        spec=spec,
        stage_slug=stage_slug,
        record_count=VERIFY_RECORD_COUNT,
        operation_count=VERIFY_OPERATION_COUNT,
        trace_path=trace_path,
        results_path=results_path,
        started_at_utc=stage_started_utc,
        finished_at_utc=finished_at_utc,
        elapsed_seconds=elapsed_seconds,
        capture_command=_build_capture_command(
            VERIFY_RECORD_COUNT,
            VERIFY_OPERATION_COUNT,
            spec.mix,
        ),
        replay_command=replay_command,
        dashboard_command=dashboard_command,
    )
    _write_json(manifest_path, manifest)
    _render_dashboard(
        stage_slug=stage_slug,
        results_path=results_path,
        trace_path=trace_path,
        manifest_path=manifest_path,
    )

    df = pl.read_parquet(results_path)
    runtimes = [float(value) for value in df["runtime_s_1"]]
    _append_report(
        "\n".join(
            [
                f"## {spec.experiment_name} Verification",
                "",
                f"- slug: `{stage_slug}`",
                f"- workload: `{spec.mix.label()}`",
                f"- record count: `{VERIFY_RECORD_COUNT}`",
                f"- operation count: `{VERIFY_OPERATION_COUNT}`",
                f"- result parquet shape: `{df.shape}`",
                f"- runtime range: `{min(runtimes):.3f}s` to `{max(runtimes):.3f}s`",
                f"- capture log: `{capture_log_path}`",
                f"- runtime log: `{runtime_log_path}`",
                f"- dashboard: `{html_path}`",
                "- runtime-only verification passed with no dTLB columns.",
            ]
        )
    )


def _calibrate_full_capture(spec: ExperimentSpec) -> tuple[ExperimentSpec, tuple[CalibrationAttempt, ...]]:
    """Run 5-row full-size calibration replays until the base-pages row is near 60s."""

    attempts: list[CalibrationAttempt] = []
    record_count = spec.record_count
    operation_count = spec.operation_count

    for attempt_index in range(1, MAX_CALIBRATION_ATTEMPTS + 1):
        _ensure_min_free_space()
        _log(
            f"Calibration attempt {attempt_index} for {spec.experiment_name}: "
            f"record_count={record_count}, operation_count={operation_count}"
        )

        snapshot_path, trace_path, _capture_log_path = _capture_trace(
            spec.slug,
            record_count=record_count,
            operation_count=operation_count,
            mix=spec.mix,
        )
        _ = snapshot_path, trace_path
        breakpoints_path = DATA_DIR / f"{spec.slug}_calibration_breakpoints_5rows.parquet"
        results_path = DATA_DIR / f"{spec.slug}_calibration_results_5rows.parquet"
        runtime_log_path = DATA_DIR / f"{spec.slug}_calibration_runtime.log"

        _generate_breakpoints(
            trace_path,
            breakpoints_path,
            hot_keys=VERIFY_HOT_KEYS,
            random_rows=VERIFY_RANDOM_ROWS,
        )
        _run_replay(
            snapshot_path,
            trace_path,
            breakpoints_path,
            results_path,
            runs=VERIFY_RUNS,
            log_path=runtime_log_path,
        )
        base_runtime = _base_pages_runtime(results_path)
        accepted = TARGET_RUNTIME_MIN_S <= base_runtime <= TARGET_RUNTIME_MAX_S
        attempts.append(
            CalibrationAttempt(
                record_count=record_count,
                operation_count=operation_count,
                base_pages_runtime_s=base_runtime,
                accepted=accepted,
            )
        )
        if accepted:
            _log(
                f"Accepted calibration for {spec.experiment_name}: "
                f"base_pages={base_runtime:.3f}s"
            )
            return (
                ExperimentSpec(
                    experiment_name=spec.experiment_name,
                    slug=spec.slug,
                    mix=spec.mix,
                    record_count=record_count,
                    operation_count=operation_count,
                    record_ratio=spec.record_ratio,
                    notes=spec.notes,
                ),
                tuple(attempts),
            )

        new_operation_count = max(
            1,
            int(round(operation_count * TARGET_RUNTIME_S / base_runtime)),
        )
        new_record_count = max(1, int(round(new_operation_count * spec.record_ratio)))
        if new_operation_count == operation_count and new_record_count == record_count:
            raise RuntimeError(
                f"Calibration for {spec.experiment_name} did not move after attempt "
                f"{attempt_index}; base_pages remained {base_runtime:.3f}s"
            )
        operation_count = new_operation_count
        record_count = new_record_count

    raise RuntimeError(
        f"Calibration for {spec.experiment_name} did not land in {TARGET_RUNTIME_MIN_S:.0f}s-"
        f"{TARGET_RUNTIME_MAX_S:.0f}s after {MAX_CALIBRATION_ATTEMPTS} attempts"
    )


def _full_experiment_notes(
    spec: ExperimentSpec,
    calibration_attempts: tuple[CalibrationAttempt, ...],
    selection_note: str | None,
) -> list[str]:
    """Return the note list embedded into the full manifest."""

    notes = [
        "Runtime-only overnight replay. No dTLB or other hardware counters were collected.",
        "Row layout uses base_pages, no_break, 14 hottest split-only rows, and 2 random split-only rows.",
        *spec.notes,
    ]
    if selection_note is not None:
        notes.append(selection_note)
    notes.extend(
        [
            (
                "Calibration attempt "
                f"{index}: record_count={attempt.record_count}, "
                f"operation_count={attempt.operation_count}, "
                f"base_pages={attempt.base_pages_runtime_s:.3f}s, "
                f"accepted={attempt.accepted}"
            )
            for index, attempt in enumerate(calibration_attempts, start=1)
        ]
    )
    return notes


def _run_full_experiment(
    spec: ExperimentSpec,
    *,
    calibration_attempts: tuple[CalibrationAttempt, ...],
    selection_note: str | None = None,
) -> ExperimentOutcome:
    """Run the full ``2-14-2`` runtime-only replay for one workload."""

    _log(f"Starting full experiment for {spec.experiment_name}")
    stage_started_utc = _utc_now_text()
    stage_start = time.monotonic()

    snapshot_path = TRACE_DIR / f"{spec.slug}_snapshot.rdb"
    trace_path = TRACE_DIR / f"{spec.slug}_monitor_run.log"
    if not snapshot_path.exists() or not trace_path.exists():
        raise RuntimeError(
            f"Accepted full-size capture for {spec.experiment_name} is missing; "
            "calibration must produce the final snapshot and monitor log first."
        )

    breakpoints_path = DATA_DIR / f"{spec.slug}_breakpoints_18rows.parquet"
    results_path = DATA_DIR / f"{spec.slug}_results_18rows.parquet"
    runtime_log_path = DATA_DIR / f"{spec.slug}_runtime.log"
    manifest_path = MANIFEST_DIR / f"{spec.slug}.json"

    _generate_breakpoints(
        trace_path,
        breakpoints_path,
        hot_keys=FULL_HOT_KEYS,
        random_rows=FULL_RANDOM_ROWS,
    )
    _run_replay(
        snapshot_path,
        trace_path,
        breakpoints_path,
        results_path,
        runs=FULL_RUNS,
        log_path=runtime_log_path,
    )
    _assert_runtime_only_results(results_path, runs=FULL_RUNS)

    html_path, metrics_path, rows_path, summary_path = _dashboard_paths(spec.slug)
    replay_command = _shell_join(
        [
            sys.executable,
            "python/kernmlops/replay/replay_trace.py",
            str(snapshot_path),
            str(trace_path),
            "--breakpoints",
            str(breakpoints_path),
            "--output",
            str(results_path),
            "--runs",
            str(FULL_RUNS),
            "-v",
        ]
    )
    dashboard_command = _shell_join(
        [
            sys.executable,
            "temp_data_analysis/replay_runtime_experiment_dashboard.py",
            "--results",
            str(results_path),
            "--trace",
            str(trace_path),
            "--manifest",
            str(manifest_path),
            "--output-html",
            str(html_path),
            "--output-metrics",
            str(metrics_path),
            "--output-rows",
            str(rows_path),
            "--output-summary",
            str(summary_path),
        ]
    )
    capture_command = _build_capture_command(
        spec.record_count,
        spec.operation_count,
        spec.mix,
    )
    placeholder_manifest = _build_manifest(
        spec=spec,
        record_count=spec.record_count,
        operation_count=spec.operation_count,
        trace_path=trace_path,
        results_path=results_path,
        row_layout_hot_keys=FULL_HOT_KEYS,
        row_layout_random_rows=FULL_RANDOM_ROWS,
        row_layout_runs=FULL_RUNS,
        started_at_utc=stage_started_utc,
        finished_at_utc=_utc_now_text(),
        elapsed_seconds=time.monotonic() - stage_start,
        capture_command=capture_command,
        replay_command=replay_command,
        dashboard_command=dashboard_command,
        notes=_full_experiment_notes(spec, calibration_attempts, selection_note),
        insights=[],
    )
    _write_json(manifest_path, placeholder_manifest)
    _render_dashboard(
        stage_slug=spec.slug,
        results_path=results_path,
        trace_path=trace_path,
        manifest_path=manifest_path,
    )

    metrics = _load_json(metrics_path)
    insights = _build_insights(metrics, rows_path)
    final_manifest = _build_manifest(
        spec=spec,
        record_count=spec.record_count,
        operation_count=spec.operation_count,
        trace_path=trace_path,
        results_path=results_path,
        row_layout_hot_keys=FULL_HOT_KEYS,
        row_layout_random_rows=FULL_RANDOM_ROWS,
        row_layout_runs=FULL_RUNS,
        started_at_utc=stage_started_utc,
        finished_at_utc=_utc_now_text(),
        elapsed_seconds=time.monotonic() - stage_start,
        capture_command=capture_command,
        replay_command=replay_command,
        dashboard_command=dashboard_command,
        notes=_full_experiment_notes(spec, calibration_attempts, selection_note),
        insights=insights,
    )
    _write_json(manifest_path, final_manifest)
    html_path, metrics_path, rows_path, summary_path = _render_dashboard(
        stage_slug=spec.slug,
        results_path=results_path,
        trace_path=trace_path,
        manifest_path=manifest_path,
    )
    metrics = _load_json(metrics_path)

    _append_report(
        "\n".join(
            [
                f"## {spec.experiment_name}",
                "",
                f"- slug: `{spec.slug}`",
                f"- workload: `{spec.mix.label()}`",
                f"- accepted record count: `{spec.record_count}`",
                f"- accepted operation count: `{spec.operation_count}`",
                (
                    f"- base_pages mean runtime: `{metrics['base_pages_runtime_s']:.3f}s`"
                ),
                (
                    f"- no_break mean runtime: `{metrics['no_break_runtime_s']:.3f}s`"
                ),
                (
                    f"- base_pages vs no_break: `{metrics['base_pages_pct_vs_no_break']:.2f}%`"
                ),
                (
                    "- hot split mean runtime delta vs no_break: "
                    f"`{metrics['hot_split_mean_runtime_pct']:.2f}%`"
                    if metrics["hot_split_mean_runtime_pct"] is not None
                    else "- hot split mean runtime delta vs no_break: `n/a`"
                ),
                (
                    "- random split mean runtime delta vs no_break: "
                    f"`{metrics['random_split_mean_runtime_pct']:.2f}%`"
                    if metrics["random_split_mean_runtime_pct"] is not None
                    else "- random split mean runtime delta vs no_break: `n/a`"
                ),
                (
                    "- hot split success rate: "
                    f"`{metrics['hot_split_success_rate_pct']:.2f}%`"
                    if metrics["hot_split_success_rate_pct"] is not None
                    else "- hot split success rate: `n/a`"
                ),
                (
                    f"- observed max split attempts: `{metrics['observed_max_split_attempts']}` "
                    f"(configured `{metrics['configured_max_split_attempts']}`)"
                ),
                f"- result parquet: `{results_path}`",
                f"- dashboard: `{html_path}`",
                f"- metrics JSON: `{metrics_path}`",
                f"- rows JSON: `{rows_path}`",
                f"- summary markdown: `{summary_path}`",
                f"- manifest: `{manifest_path}`",
            ]
        )
    )

    return ExperimentOutcome(
        spec=spec,
        metrics=metrics,
        manifest_path=manifest_path,
        rows_path=rows_path,
        summary_path=summary_path,
        dashboard_path=html_path,
        results_path=results_path,
        breakpoints_path=breakpoints_path,
        trace_path=trace_path,
        snapshot_path=snapshot_path,
        calibration_attempts=calibration_attempts,
    )


def _qualifies_for_exp3(metrics: dict[str, Any]) -> bool:
    """Return whether one delete-heavy workload is strong enough to guide exp3."""

    hot_mean = metrics.get("hot_split_mean_runtime_pct")
    random_mean = metrics.get("random_split_mean_runtime_pct")
    hot_success_rate = metrics.get("hot_split_success_rate_pct")
    if hot_mean is None or random_mean is None or hot_success_rate is None:
        return False
    if hot_success_rate < 80.0:
        return False
    return abs(float(hot_mean)) > abs(float(random_mean))


def _choose_exp3(exp1: ExperimentOutcome, exp2: ExperimentOutcome) -> tuple[ExperimentSpec, str]:
    """Choose the exp3 workload using the agreed runtime-only rule.

    If neither delete-heavy workload passes the gating rule, fall back to the
    `60/20/20` insert-balanced mix with the `8000`-record starting point.
    """

    candidates = [exp1, exp2]
    qualifying = [
        outcome
        for outcome in candidates
        if _qualifies_for_exp3(outcome.metrics)
    ]
    if not qualifying:
        return (
            ExperimentSpec(
                experiment_name="exp3",
                slug="exp3_read60_delete20_insert20_2142_2min3",
                mix=WorkloadMix(read=0.60, delete=0.20, insert=0.20),
                record_count=8000,
                operation_count=20000,
                record_ratio=8000 / 20000,
                notes=(
                    "Defaulted to the balanced insert/delete workload because neither exp1 nor exp2 cleared the exp3 gate.",
                ),
            ),
            "Neither exp1 nor exp2 met the exp3 gate, so exp3 defaulted to 60% read / 20% delete / 20% insert with 8000 starting records.",
        )

    winner = max(
        qualifying,
        key=lambda outcome: abs(float(outcome.metrics["base_pages_pct_vs_no_break"])),
    )
    if winner.spec.experiment_name == "exp1":
        return (
            ExperimentSpec(
                experiment_name="exp3",
                slug="exp3_read60_delete20_insert20_from_exp1_2142_2min3",
                mix=WorkloadMix(read=0.60, delete=0.20, insert=0.20),
                record_count=4000,
                operation_count=20000,
                record_ratio=4000 / 20000,
                notes=(
                    "Exp3 kept the 60/20/20 mix but used exp1's denser delete pressure as the starting point.",
                ),
            ),
            "Exp1 showed the stronger qualifying signal, so exp3 uses 60% read / 20% delete / 20% insert with the exp1-style 4000-record starting point.",
        )
    return (
        ExperimentSpec(
            experiment_name="exp3",
            slug="exp3_read60_delete20_insert20_from_exp2_2142_2min3",
            mix=WorkloadMix(read=0.60, delete=0.20, insert=0.20),
            record_count=8000,
            operation_count=20000,
            record_ratio=8000 / 20000,
            notes=(
                "Exp3 kept the 60/20/20 mix and used exp2's less sparse starting point.",
            ),
        ),
        "Exp2 showed the stronger qualifying signal, so exp3 uses 60% read / 20% delete / 20% insert with the exp2-style 8000-record starting point.",
    )


def _maybe_run_optional_experiments(session_start: float) -> None:
    """Run optional experiments only when the overnight budget still allows them."""

    optional_specs = [
        ExperimentSpec(
            experiment_name="exp4",
            slug="exp4_read50_delete50_2142_2min3",
            mix=WorkloadMix(read=0.50, delete=0.50),
            record_count=12000,
            operation_count=12000,
            record_ratio=12000 / 12000,
            notes=("Optional workload: 50% read / 50% delete.",),
        ),
        ExperimentSpec(
            experiment_name="exp5",
            slug="exp5_read70_delete20_insert10_2142_2min3",
            mix=WorkloadMix(read=0.70, delete=0.20, insert=0.10),
            record_count=10000,
            operation_count=20000,
            record_ratio=10000 / 20000,
            notes=("Optional workload: 70% read / 20% delete / 10% insert.",),
        ),
        ExperimentSpec(
            experiment_name="exp6",
            slug="exp6_read40_delete30_insert30_2142_2min3",
            mix=WorkloadMix(read=0.40, delete=0.30, insert=0.30),
            record_count=8000,
            operation_count=20000,
            record_ratio=8000 / 20000,
            notes=("Optional workload: 40% read / 30% delete / 30% insert.",),
        ),
    ]

    for spec in optional_specs:
        elapsed = time.monotonic() - session_start
        if elapsed + OPTIONAL_EXPERIMENT_ESTIMATE_S > OVERNIGHT_BUDGET_S:
            _append_report(
                "\n".join(
                    [
                        "## Optional Workloads",
                        "",
                        f"- skipped `{spec.experiment_name}` because the overnight budget had only `{(OVERNIGHT_BUDGET_S - elapsed) / 3600:.2f}` hours left by estimate.",
                    ]
                )
            )
            break
        _run_verification(spec)
        accepted_spec, calibration_attempts = _calibrate_full_capture(spec)
        _run_full_experiment(accepted_spec, calibration_attempts=calibration_attempts)


def _record_session_footer() -> None:
    """Append the final session footer to the report."""

    _append_report(
        "\n".join(
            [
                "## Session End",
                "",
                f"- completed at: `{_utc_now_text()}`",
                "- all replay runs in this report are runtime-only only and collected no hardware counters.",
            ]
        )
    )


def main() -> None:
    """Run the overnight runtime-only matrix end-to-end."""

    _ensure_directories()
    _ensure_min_free_space()
    _record_preflight_section()

    session_start = time.monotonic()
    control = ExperimentSpec(
        experiment_name="control",
        slug="control_read100_2142_2min3",
        mix=WorkloadMix(read=1.00, delete=0.00),
        record_count=15000,
        operation_count=10000,
        record_ratio=15000 / 10000,
        notes=("Control workload: 100% reads.",),
    )
    exp1 = ExperimentSpec(
        experiment_name="exp1",
        slug="exp1_read80_delete20_delEqRecord_2142_2min3",
        mix=WorkloadMix(read=0.80, delete=0.20),
        record_count=4000,
        operation_count=20000,
        record_ratio=4000 / 20000,
        notes=("Delete count is targeted to roughly match the starting record count.",),
    )
    exp2 = ExperimentSpec(
        experiment_name="exp2",
        slug="exp2_read80_delete20_delEqHalfRecord_2142_2min3",
        mix=WorkloadMix(read=0.80, delete=0.20),
        record_count=8000,
        operation_count=20000,
        record_ratio=8000 / 20000,
        notes=(
            "Delete count is targeted to roughly match half of the starting record count.",
        ),
    )

    outcomes: list[ExperimentOutcome] = []
    for spec in (control, exp1, exp2):
        _run_verification(spec)
        accepted_spec, calibration_attempts = _calibrate_full_capture(spec)
        outcome = _run_full_experiment(
            accepted_spec,
            calibration_attempts=calibration_attempts,
        )
        outcomes.append(outcome)

    exp3_spec, exp3_note = _choose_exp3(outcomes[1], outcomes[2])
    _append_report(
        "\n".join(
            [
                "## Exp3 Selection",
                "",
                f"- selection note: {exp3_note}",
                f"- chosen slug: `{exp3_spec.slug}`",
                f"- chosen workload: `{exp3_spec.mix.label()}`",
                f"- starting record count: `{exp3_spec.record_count}`",
                f"- starting operation count: `{exp3_spec.operation_count}`",
            ]
        )
    )
    _run_verification(exp3_spec)
    exp3_accepted_spec, exp3_calibration_attempts = _calibrate_full_capture(exp3_spec)
    _run_full_experiment(
        exp3_accepted_spec,
        calibration_attempts=exp3_calibration_attempts,
        selection_note=exp3_note,
    )

    _maybe_run_optional_experiments(session_start)
    _record_session_footer()


if __name__ == "__main__":
    main()
