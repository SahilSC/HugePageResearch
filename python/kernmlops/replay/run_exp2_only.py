"""Exp2-only runtime replay runner for April 9, 2026.

Narrow single-experiment script that runs only the exp2 workload
(80% read / 20% delete, record_count = 0.4 * operation_count) with:

- row layout ``2-12-2`` (16 rows total)
- ``3`` timed runs per row
- ``120s`` base_pages runtime target
- no ``--collector-config``, no dTLB, no hardware counters
- wide calibration band ``105s-135s`` to minimise recapture cycles

Designed to run inside the ``clever_hofstadter`` container, launched from
a host-side ``tmux`` session.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    _PACKAGE_ROOT = Path(__file__).resolve().parents[1]
    _PACKAGE_ROOT_STR = str(_PACKAGE_ROOT)
    if _PACKAGE_ROOT_STR not in sys.path:
        sys.path.insert(0, _PACKAGE_ROOT_STR)

from replay.run_overnight_runtime_matrix import (
    VERIFY_RECORD_COUNT,
    VERIFY_OPERATION_COUNT,
    VERIFY_HOT_KEYS,
    VERIFY_RANDOM_ROWS,
    VERIFY_RUNS,
    TRACE_DIR,
    DATA_DIR,
    MANIFEST_DIR,
    TEMP_ANALYSIS_DIR,
    REPO_ROOT,
    WorkloadMix,
    ExperimentSpec,
    CalibrationAttempt,
    _utc_now_text,
    _log,
    _shell_join,
    _repo_redis_cli_command,
    _chown_path,
    _safe_unlink,
    _ensure_directories,
    _ensure_min_free_space,
    _run_command,
    _capture_output,
    _write_json,
    _capture_trace,
    _generate_breakpoints,
    _run_replay,
    _assert_runtime_only_results,
    _assert_verification_acceptance,
    _base_pages_runtime,
    _verification_manifest,
    _build_manifest,
    _dashboard_paths,
    _render_dashboard,
    _load_json,
    _build_insights,
    _build_capture_command,
)
from replay.replay_trace import BREAK_PAGE_MAX_ATTEMPTS

# ---------------------------------------------------------------------------
# Exp2-only constants
# ---------------------------------------------------------------------------

SLUG = "exp2_read80_delete20_delEqHalfRecord_2122_2min3"
MIX = WorkloadMix(read=0.80, delete=0.20)
STARTING_RECORD_COUNT = 8000
STARTING_OPERATION_COUNT = 20000
RECORD_RATIO = 0.4

TARGET_RUNTIME_S = 120.0
TARGET_RUNTIME_MIN_S = 105.0
TARGET_RUNTIME_MAX_S = 135.0
MAX_CALIBRATION_ATTEMPTS = 2

FULL_HOT_KEYS = 12
FULL_RANDOM_ROWS = 2
FULL_RUNS = 3

REPORT_PATH = REPO_ROOT / "reports" / "collector" / "replay_exp2_2026-04-09.md"

EXP2_SPEC = ExperimentSpec(
    experiment_name="exp2",
    slug=SLUG,
    mix=MIX,
    record_count=STARTING_RECORD_COUNT,
    operation_count=STARTING_OPERATION_COUNT,
    record_ratio=RECORD_RATIO,
    notes=(
        "Delete count is targeted to roughly match half of the starting record count.",
    ),
)

# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------


def _append_report(section: str) -> None:
    """Append one markdown section to the exp2 report."""

    with open(REPORT_PATH, "a", encoding="utf-8") as handle:
        handle.write(section)
        if not section.endswith("\n"):
            handle.write("\n")
        handle.write("\n")
    _chown_path(REPORT_PATH)


def _write_report_header() -> None:
    """Write the fixed report header."""

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        "# Replay Exp2-Only 2026-04-09\n"
        "\n"
        "## Overview\n"
        "\n"
        "Exp2-only runtime replay with:\n"
        "\n"
        "- workload: 80% read / 20% delete\n"
        "- record_count = 0.4 * operation_count\n"
        "- row layout: 2-12-2 (16 rows)\n"
        "- 3 timed runs per row\n"
        "- 120s base_pages runtime target (105-135s acceptance band)\n"
        "- no dTLB or hardware counters\n"
        "\n",
        encoding="utf-8",
    )
    _chown_path(REPORT_PATH)


def _record_preflight() -> None:
    """Run preflight checks and record them in the report."""

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
                f"- start time: `{_utc_now_text()}`",
                f"- redis ping: `{ping_output}`",
                f"- VAPTR present: `{'vaptr' in vaptr_output.lower()}`",
                f"- free space: `{free_gib:.2f} GiB`",
                "- runtime-only (no --collector-config)",
            ]
        )
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _run_verification() -> None:
    """Run a quick 5-row verification, then delete the snapshot to save space."""

    _log("Starting exp2 verification")
    stage_slug = f"{SLUG}_verification"
    stage_started_utc = _utc_now_text()
    stage_start = time.monotonic()

    snapshot_path, trace_path, capture_log_path = _capture_trace(
        stage_slug,
        record_count=VERIFY_RECORD_COUNT,
        operation_count=VERIFY_OPERATION_COUNT,
        mix=MIX,
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

    # Build verification manifest and dashboard
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
        spec=EXP2_SPEC,
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
            MIX,
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

    import polars as pl

    df = pl.read_parquet(results_path)
    runtimes = [float(value) for value in df["runtime_s_1"]]
    _append_report(
        "\n".join(
            [
                "## Verification",
                "",
                f"- slug: `{stage_slug}`",
                f"- record count: `{VERIFY_RECORD_COUNT}`",
                f"- operation count: `{VERIFY_OPERATION_COUNT}`",
                f"- result shape: `{df.shape}`",
                f"- runtime range: `{min(runtimes):.3f}s` to `{max(runtimes):.3f}s`",
                f"- dashboard: `{html_path}`",
                "- verification passed",
            ]
        )
    )

    # Delete verification snapshot to reclaim space
    _log("Deleting verification snapshot to reclaim space")
    _safe_unlink(snapshot_path)
    _safe_unlink(trace_path)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def _calibrate() -> tuple[int, int, list[CalibrationAttempt]]:
    """Calibrate operation/record counts so base_pages lands in 105-135s.

    Returns:
        ``(accepted_record_count, accepted_operation_count, attempts)``

    Raises:
        RuntimeError: If calibration fails after MAX_CALIBRATION_ATTEMPTS.
    """

    attempts: list[CalibrationAttempt] = []
    record_count = STARTING_RECORD_COUNT
    operation_count = STARTING_OPERATION_COUNT

    for attempt_index in range(1, MAX_CALIBRATION_ATTEMPTS + 1):
        _ensure_min_free_space()
        _log(
            f"Calibration attempt {attempt_index}: "
            f"record_count={record_count}, operation_count={operation_count}"
        )

        # Delete previous full-size capture if retrying
        if attempt_index > 1:
            _log("Deleting previous full-size capture before recapture")
            _safe_unlink(TRACE_DIR / f"{SLUG}_snapshot.rdb")
            _safe_unlink(TRACE_DIR / f"{SLUG}_monitor_run.log")

        snapshot_path, trace_path, _capture_log = _capture_trace(
            SLUG,
            record_count=record_count,
            operation_count=operation_count,
            mix=MIX,
        )

        breakpoints_path = DATA_DIR / f"{SLUG}_calibration_breakpoints_5rows.parquet"
        results_path = DATA_DIR / f"{SLUG}_calibration_results_5rows.parquet"
        runtime_log_path = DATA_DIR / f"{SLUG}_calibration_runtime.log"

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
            runs=1,
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
            _log(f"Accepted calibration: base_pages={base_runtime:.3f}s")
            _append_report(
                "\n".join(
                    [
                        "## Calibration",
                        "",
                        f"- attempt {attempt_index} accepted",
                        f"- record_count: `{record_count}`",
                        f"- operation_count: `{operation_count}`",
                        f"- base_pages runtime: `{base_runtime:.3f}s`",
                        f"- target band: `{TARGET_RUNTIME_MIN_S:.0f}s-{TARGET_RUNTIME_MAX_S:.0f}s`",
                    ]
                )
            )
            return record_count, operation_count, attempts

        # Retune
        new_operation_count = max(
            1,
            int(round(operation_count * TARGET_RUNTIME_S / base_runtime)),
        )
        new_record_count = max(1, int(round(RECORD_RATIO * new_operation_count)))
        _log(
            f"Calibration attempt {attempt_index} missed "
            f"(base_pages={base_runtime:.3f}s); "
            f"retuning to record_count={new_record_count}, "
            f"operation_count={new_operation_count}"
        )
        if new_operation_count == operation_count and new_record_count == record_count:
            raise RuntimeError(
                f"Calibration did not move after attempt {attempt_index}; "
                f"base_pages remained {base_runtime:.3f}s"
            )
        operation_count = new_operation_count
        record_count = new_record_count

    raise RuntimeError(
        f"Calibration did not land in {TARGET_RUNTIME_MIN_S:.0f}s-"
        f"{TARGET_RUNTIME_MAX_S:.0f}s after {MAX_CALIBRATION_ATTEMPTS} attempts. "
        "Last attempts: "
        + ", ".join(
            f"({a.record_count}/{a.operation_count} -> {a.base_pages_runtime_s:.1f}s)"
            for a in attempts
        )
    )


# ---------------------------------------------------------------------------
# Full experiment
# ---------------------------------------------------------------------------


def _run_full_experiment(
    record_count: int,
    operation_count: int,
    calibration_attempts: list[CalibrationAttempt],
) -> None:
    """Run the full 2-12-2 runtime-only replay and generate all artifacts."""

    _log("Starting full exp2 experiment (2-12-2, 3 runs)")
    stage_started_utc = _utc_now_text()
    stage_start = time.monotonic()

    snapshot_path = TRACE_DIR / f"{SLUG}_snapshot.rdb"
    trace_path = TRACE_DIR / f"{SLUG}_monitor_run.log"
    if not snapshot_path.exists() or not trace_path.exists():
        raise RuntimeError(
            "Accepted full-size capture is missing; "
            "calibration must produce the snapshot and monitor log first."
        )

    total_rows = 2 + FULL_HOT_KEYS + FULL_RANDOM_ROWS  # 16
    breakpoints_path = DATA_DIR / f"{SLUG}_breakpoints_{total_rows}rows.parquet"
    results_path = DATA_DIR / f"{SLUG}_results_{total_rows}rows.parquet"
    runtime_log_path = DATA_DIR / f"{SLUG}_runtime.log"
    manifest_path = MANIFEST_DIR / f"{SLUG}.json"

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

    # Build manifest
    html_path, metrics_path, rows_path, summary_path = _dashboard_paths(SLUG)
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
    capture_command = _build_capture_command(record_count, operation_count, MIX)

    notes = [
        "Runtime-only exp2 replay. No dTLB or other hardware counters were collected.",
        f"Row layout: 2 base + 12 hottest + 2 random = {total_rows} rows.",
        f"Runtime target: {TARGET_RUNTIME_S:.0f}s (acceptance band {TARGET_RUNTIME_MIN_S:.0f}s-{TARGET_RUNTIME_MAX_S:.0f}s).",
        "Delete count is targeted to roughly match half of the starting record count.",
    ]
    notes.extend(
        f"Calibration attempt {i}: record_count={a.record_count}, "
        f"operation_count={a.operation_count}, "
        f"base_pages={a.base_pages_runtime_s:.3f}s, accepted={a.accepted}"
        for i, a in enumerate(calibration_attempts, start=1)
    )

    # First render with placeholder insights
    placeholder_manifest = _build_manifest(
        spec=ExperimentSpec(
            experiment_name="exp2",
            slug=SLUG,
            mix=MIX,
            record_count=record_count,
            operation_count=operation_count,
            record_ratio=RECORD_RATIO,
        ),
        record_count=record_count,
        operation_count=operation_count,
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
        notes=notes,
        insights=[],
    )
    _write_json(manifest_path, placeholder_manifest)
    _render_dashboard(
        stage_slug=SLUG,
        results_path=results_path,
        trace_path=trace_path,
        manifest_path=manifest_path,
    )

    # Re-render with real insights
    metrics = _load_json(metrics_path)
    insights = _build_insights(metrics, rows_path)
    final_manifest = _build_manifest(
        spec=ExperimentSpec(
            experiment_name="exp2",
            slug=SLUG,
            mix=MIX,
            record_count=record_count,
            operation_count=operation_count,
            record_ratio=RECORD_RATIO,
        ),
        record_count=record_count,
        operation_count=operation_count,
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
        notes=notes,
        insights=insights,
    )
    _write_json(manifest_path, final_manifest)
    _render_dashboard(
        stage_slug=SLUG,
        results_path=results_path,
        trace_path=trace_path,
        manifest_path=manifest_path,
    )
    metrics = _load_json(metrics_path)

    _append_report(
        "\n".join(
            [
                "## Full Experiment",
                "",
                f"- slug: `{SLUG}`",
                f"- workload: `{MIX.label()}`",
                f"- accepted record count: `{record_count}`",
                f"- accepted operation count: `{operation_count}`",
                f"- row layout: `2-{FULL_HOT_KEYS}-{FULL_RANDOM_ROWS}` ({total_rows} rows)",
                f"- timed runs: `{FULL_RUNS}`",
                f"- base_pages mean runtime: `{metrics['base_pages_runtime_s']:.3f}s`",
                f"- no_break mean runtime: `{metrics['no_break_runtime_s']:.3f}s`",
                f"- base_pages vs no_break: `{metrics['base_pages_pct_vs_no_break']:.2f}%`",
                (
                    f"- hot split mean runtime delta vs no_break: `{metrics['hot_split_mean_runtime_pct']:.2f}%`"
                    if metrics["hot_split_mean_runtime_pct"] is not None
                    else "- hot split mean runtime delta vs no_break: `n/a`"
                ),
                (
                    f"- random split mean runtime delta vs no_break: `{metrics['random_split_mean_runtime_pct']:.2f}%`"
                    if metrics["random_split_mean_runtime_pct"] is not None
                    else "- random split mean runtime delta vs no_break: `n/a`"
                ),
                (
                    f"- hot split success rate: `{metrics['hot_split_success_rate_pct']:.2f}%`"
                    if metrics["hot_split_success_rate_pct"] is not None
                    else "- hot split success rate: `n/a`"
                ),
                f"- observed max split attempts: `{metrics['observed_max_split_attempts']}` (configured `{metrics['configured_max_split_attempts']}`)",
                f"- result parquet: `{results_path}`",
                f"- dashboard: `{html_path}`",
                f"- manifest: `{manifest_path}`",
                f"- elapsed: `{time.monotonic() - stage_start:.0f}s`",
            ]
        )
    )

    _log(f"Full experiment complete. Dashboard: {html_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the exp2-only runtime replay end-to-end."""

    session_start = time.monotonic()

    _ensure_directories()
    _ensure_min_free_space()
    _write_report_header()
    _record_preflight()

    # 1. Verification
    _run_verification()

    # 2. Calibration
    record_count, operation_count, calibration_attempts = _calibrate()

    # 3. Full experiment
    _run_full_experiment(record_count, operation_count, calibration_attempts)

    # 4. Session footer
    elapsed = time.monotonic() - session_start
    _append_report(
        "\n".join(
            [
                "## Session End",
                "",
                f"- completed at: `{_utc_now_text()}`",
                f"- total elapsed: `{elapsed:.0f}s` (`{elapsed / 60:.1f} min`)",
                "- all replays are runtime-only with no hardware counters.",
            ]
        )
    )
    _log(f"Exp2-only session complete in {elapsed:.0f}s ({elapsed / 60:.1f} min)")


if __name__ == "__main__":
    main()
