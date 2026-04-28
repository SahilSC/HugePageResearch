"""Part B: compare inline versus threaded replay-time huge-page breaking."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _PACKAGE_ROOT = Path(__file__).resolve().parents[3]
    _PACKAGE_ROOT_STR = str(_PACKAGE_ROOT)
    if _PACKAGE_ROOT_STR not in sys.path:
        sys.path.insert(0, _PACKAGE_ROOT_STR)

import polars as pl

from experiments.redis_thp_replication.common import (
    PART_B_DIR,
    TEMP_ANALYSIS_DIR,
    RawBenchmarkConfig,
    ensure_experiment_directories,
    now_text,
    start_repo_redis,
    stop_repo_redis,
    write_json,
    write_markdown,
)
from experiments.redis_thp_replication.replay_analysis import (
    classify_breakpoint_rows,
    sample_mean,
    sample_std,
    slice_runs,
)
from experiments.redis_thp_replication.reporting import grouped_bar_png, write_html_report
from replay.replay_trace import BREAK_PAGE_MAX_ATTEMPTS
from replay.run_overnight_runtime_matrix import (
    VERIFY_OPERATION_COUNT,
    VERIFY_RECORD_COUNT,
    WorkloadMix,
    _assert_runtime_only_results,
    _capture_trace,
    _chown_path,
    _generate_breakpoints,
    _run_command,
    _safe_unlink,
    _shell_join,
)


DEFAULT_PART_A_MANIFEST = PART_B_DIR.parent / "part_a" / "part_a_manifest.json"
PART_B_VERIFICATION_RESULTS_PATH = PART_B_DIR / "part_b_verification_results_5rows.parquet"
PART_B_RESULTS_PATH = PART_B_DIR / "part_b_results_16rows.parquet"
PART_B_VERIFICATION_LOG_PATH = PART_B_DIR / "part_b_verification_runtime.log"
PART_B_RUNTIME_LOG_PATH = PART_B_DIR / "part_b_runtime.log"
PART_B_VERIFICATION_BREAKPOINTS_PATH = PART_B_DIR / "part_b_verification_breakpoints_5rows.parquet"
PART_B_BREAKPOINTS_PATH = PART_B_DIR / "part_b_breakpoints_16rows.parquet"
PART_B_MANIFEST_PATH = PART_B_DIR / "part_b_manifest.json"
PART_B_REPORT_PATH = PART_B_DIR / "part_b_summary.md"
PART_B_PNG_PATH = TEMP_ANALYSIS_DIR / "redis_thp_part_b_runtime_compare.png"
PART_B_SPLIT_PNG_PATH = TEMP_ANALYSIS_DIR / "redis_thp_part_b_split_latency.png"
PART_B_HTML_PATH = TEMP_ANALYSIS_DIR / "redis_thp_part_b_runtime_compare.html"

PART_B_VERIFICATION_SLUG = "redis_thp_part_b_verification"
PART_B_FULL_SLUG = "redis_thp_part_b_dispatch_mixed_2122_8runs"

VERIFY_HOT_KEYS = 2
VERIFY_RANDOM_ROWS = 1
VERIFY_RUNS = 2
FULL_HOT_KEYS = 12
FULL_RANDOM_ROWS = 2
FULL_RUNS = 8
INLINE_RUNS = [1, 2, 3, 4]
THREADED_RUNS = [5, 6, 7, 8]


def _load_part_a_manifest(path: Path) -> dict[str, Any]:
    """Load the part A manifest and fail fast when it is missing."""

    if not path.exists():
        raise RuntimeError(
            f"Part A manifest not found at {path}. Run part_a.py before part_b.py."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_capture_config(part_a_manifest: dict[str, Any]) -> tuple[RawBenchmarkConfig, str]:
    """Return the recommended capture config for replay parts B and C."""

    payload = part_a_manifest.get("recommended_capture_config")
    if not isinstance(payload, dict):
        raise RuntimeError("Part A manifest is missing recommended_capture_config.")
    return RawBenchmarkConfig(**payload), str(
        part_a_manifest.get("recommended_capture_source", "unknown")
    )


def _workload_mix(config: RawBenchmarkConfig) -> WorkloadMix:
    """Convert the chosen raw benchmark config into capture-script proportions."""

    return WorkloadMix(
        read=config.read_proportion,
        delete=config.delete_proportion,
        insert=config.insert_proportion,
        update=config.update_proportion,
        scan=config.scan_proportion,
        readmodifywrite=config.readmodifywrite_proportion,
    )


def _run_replay_with_dispatch(
    *,
    snapshot_path: Path,
    trace_path: Path,
    breakpoints_path: Path,
    results_path: Path,
    runs: int,
    break_dispatch: str,
    log_path: Path,
) -> None:
    """Run replay_trace.py with an explicit break-dispatch mode."""

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
        "--break-dispatch",
        break_dispatch,
        "-v",
    ]
    _run_command(cmd, log_path=log_path)
    _chown_path(results_path)


def _capture_trace_with_repo_redis(
    slug: str,
    *,
    record_count: int,
    operation_count: int,
    mix: WorkloadMix,
) -> tuple[Path, Path, Path]:
    """Start repo Redis for one capture and leave it up for replay restore.

    The capture script intentionally fails fast when Redis is unreachable. Part B
    is a one-off experiment driver, so it should satisfy that precondition
    itself rather than depending on leftover container state from part A. The
    later replay step needs that live Redis instance so ``restore_snapshot()``
    can read the server PID and configured RDB directory before it restarts the
    process from the captured snapshot.

    Args:
        slug: Artifact prefix for this capture attempt.
        record_count: YCSB load record count.
        operation_count: YCSB run operation count.
        mix: Operation proportions for the capture run.

    Returns:
        The snapshot path, monitor trace path, and capture log path produced by
        ``_capture_trace``.
    """

    server_dir = PART_B_DIR / f"{slug}_redis_server"
    start_repo_redis(run_dir=server_dir)
    return _capture_trace(
        slug,
        record_count=record_count,
        operation_count=operation_count,
        mix=mix,
    )


def _assert_mixed_dispatch_results(results_path: Path, *, runs: int, expected_rows: int) -> pl.DataFrame:
    """Validate the mixed inline/threaded replay result."""

    df = _assert_runtime_only_results(results_path, runs=runs)
    if df.height != expected_rows:
        raise RuntimeError(
            f"Expected {expected_rows} replay rows, found {df.height} in {results_path}."
        )
    if runs % 2 != 0:
        raise RuntimeError("Mixed dispatch verification requires an even run count.")
    split_point = runs // 2
    for run_number in range(1, runs + 1):
        expected_mode = "inline" if run_number <= split_point else "threaded"
        column = f"break_dispatch_{run_number}"
        if set(df[column].to_list()) != {expected_mode}:
            raise RuntimeError(
                f"{column} was not uniformly {expected_mode} in {results_path}."
            )
    return df


def _format_runtimes(samples: list[float]) -> str:
    """Return one compact `rN=value` string for a run-sample list."""

    return ", ".join(
        f"r{index}={sample:.3f}s" for index, sample in enumerate(samples, start=1)
    )


def _render_artifacts(
    *,
    results_df: pl.DataFrame,
    trace_path: Path,
    config: RawBenchmarkConfig,
    config_source: str,
    verification_artifacts: dict[str, str],
    full_artifacts: dict[str, str],
    started_at_utc: str,
    finished_at_utc: str,
) -> dict[str, Any]:
    """Render the part B HTML/PNG outputs and return summary metadata."""

    records = classify_breakpoint_rows(
        results_df,
        trace_path=trace_path,
        hot_key_rows=FULL_HOT_KEYS,
    )
    categories = [record["label"] for record in records]
    inline_means = [
        sample_mean([float(value) for value in slice_runs(record["runtime_samples"], INLINE_RUNS)])
        for record in records
    ]
    inline_stds = [
        sample_std([float(value) for value in slice_runs(record["runtime_samples"], INLINE_RUNS)])
        for record in records
    ]
    threaded_means = [
        sample_mean([float(value) for value in slice_runs(record["runtime_samples"], THREADED_RUNS)])
        for record in records
    ]
    threaded_stds = [
        sample_std(
            [float(value) for value in slice_runs(record["runtime_samples"], THREADED_RUNS)]
        )
        for record in records
    ]

    inline_split_wall_means = [
        sample_mean(
            [
                float(value)
                for value in slice_runs(record["split_total_wall_ms_samples"], INLINE_RUNS)
            ]
        )
        for record in records
    ]
    threaded_split_wall_means = [
        sample_mean(
            [
                float(value)
                for value in slice_runs(record["split_total_wall_ms_samples"], THREADED_RUNS)
            ]
        )
        for record in records
    ]
    inline_split_wall_stds = [
        sample_std(
            [
                float(value)
                for value in slice_runs(record["split_total_wall_ms_samples"], INLINE_RUNS)
            ]
        )
        for record in records
    ]
    threaded_split_wall_stds = [
        sample_std(
            [
                float(value)
                for value in slice_runs(record["split_total_wall_ms_samples"], THREADED_RUNS)
            ]
        )
        for record in records
    ]

    grouped_bar_png(
        categories=categories,
        series=[
            {
                "label": "Inline",
                "means": inline_means,
                "stds": inline_stds,
                "color": "#1f77b4",
            },
            {
                "label": "Threaded",
                "means": threaded_means,
                "stds": threaded_stds,
                "color": "#d62728",
            },
        ],
        title="Part B: Inline vs Threaded Replay Runtime",
        ylabel="Seconds",
        output_path=PART_B_PNG_PATH,
    )
    grouped_bar_png(
        categories=categories,
        series=[
            {
                "label": "Inline split wall time",
                "means": inline_split_wall_means,
                "stds": inline_split_wall_stds,
                "color": "#2ca02c",
            },
            {
                "label": "Threaded split wall time",
                "means": threaded_split_wall_means,
                "stds": threaded_split_wall_stds,
                "color": "#9467bd",
            },
        ],
        title="Part B: Split Wall Time by Dispatch Mode",
        ylabel="Milliseconds",
        output_path=PART_B_SPLIT_PNG_PATH,
    )

    summary_rows = []
    for record in records:
        inline_runtime_samples = [
            float(value) for value in slice_runs(record["runtime_samples"], INLINE_RUNS)
        ]
        threaded_runtime_samples = [
            float(value) for value in slice_runs(record["runtime_samples"], THREADED_RUNS)
        ]
        inline_success_total = int(
            sum(slice_runs(record["split_successes_samples"], INLINE_RUNS))
        )
        threaded_success_total = int(
            sum(slice_runs(record["split_successes_samples"], THREADED_RUNS))
        )
        inline_failure_total = int(
            sum(slice_runs(record["split_failures_samples"], INLINE_RUNS))
        )
        threaded_failure_total = int(
            sum(slice_runs(record["split_failures_samples"], THREADED_RUNS))
        )
        inline_queue_lag = [
            float(value)
            for value in slice_runs(record["split_queue_lag_ms_mean_samples"], INLINE_RUNS)
        ]
        threaded_queue_lag = [
            float(value)
            for value in slice_runs(record["split_queue_lag_ms_mean_samples"], THREADED_RUNS)
        ]
        summary_rows.append(
            {
                "label": record["label"],
                "row_type": record["row_type"],
                "access_count": record["access_count"],
                "inline_mean_s": sample_mean(inline_runtime_samples),
                "inline_std_s": sample_std(inline_runtime_samples),
                "threaded_mean_s": sample_mean(threaded_runtime_samples),
                "threaded_std_s": sample_std(threaded_runtime_samples),
                "inline_split_successes": inline_success_total,
                "threaded_split_successes": threaded_success_total,
                "inline_split_failures": inline_failure_total,
                "threaded_split_failures": threaded_failure_total,
                "inline_queue_lag_mean_ms": sample_mean(inline_queue_lag),
                "threaded_queue_lag_mean_ms": sample_mean(threaded_queue_lag),
                "inline_runtimes": _format_runtimes(inline_runtime_samples),
                "threaded_runtimes": _format_runtimes(threaded_runtime_samples),
            }
        )
    summary_df = pl.DataFrame(summary_rows)

    config_df = pl.DataFrame(
        [
            {
                "config_name": config.name,
                "record_count": config.record_count,
                "operation_count": config.operation_count,
                "read_proportion": config.read_proportion,
                "delete_proportion": config.delete_proportion,
                "insert_proportion": config.insert_proportion,
                "config_source": config_source,
                "break_page_max_attempts": BREAK_PAGE_MAX_ATTEMPTS,
                "verification_layout": "2-2-1, 2 runs, mixed dispatch",
                "full_layout": "2-12-2, 8 runs, mixed dispatch",
            }
        ]
    )
    write_html_report(
        title="Part B: Inline vs Threaded Replay Breaking",
        subtitle=(
            "Runs 1-4 break huge pages inline on the replay hot path. "
            "Runs 5-8 enqueue the split to a dedicated background worker."
        ),
        sections=[
            {
                "title": "Configuration",
                "description": (
                    "This part reuses the replay capture configuration chosen by part A "
                    "or the documented 80/20 fallback when part A did not find a clear THP effect."
                ),
                "table": config_df,
                "bullets": [
                    f"Started: {started_at_utc}",
                    f"Finished: {finished_at_utc}",
                    f"Verification replay log: {verification_artifacts['runtime_log']}",
                    f"Full replay log: {full_artifacts['runtime_log']}",
                ],
            },
            {
                "title": "Runtime Comparison",
                "description": "Grouped means with sample-standard-deviation error bars for inline versus threaded replay dispatch.",
                "image_path": PART_B_PNG_PATH,
            },
            {
                "title": "Split Wall Time",
                "description": "Total replay-time split wall time per row, comparing inline dispatch against threaded dispatch.",
                "image_path": PART_B_SPLIT_PNG_PATH,
            },
            {
                "title": "Per-Row Summary",
                "description": "All timed samples plus split-success and queue-lag summaries for each replay row.",
                "table": summary_df,
            },
        ],
        output_path=PART_B_HTML_PATH,
    )
    write_markdown(
        PART_B_REPORT_PATH,
        "\n".join(
            [
                "# Part B Summary",
                "",
                f"- started: `{started_at_utc}`",
                f"- finished: `{finished_at_utc}`",
                f"- config source: `{config_source}`",
                f"- verification results: `{verification_artifacts['results']}`",
                f"- full results: `{full_artifacts['results']}`",
                f"- html: `{PART_B_HTML_PATH}`",
                f"- runtime png: `{PART_B_PNG_PATH}`",
                f"- split png: `{PART_B_SPLIT_PNG_PATH}`",
                "",
            ]
        ),
    )
    return {
        "records": len(records),
        "inline_overall_mean_s": sample_mean(inline_means),
        "threaded_overall_mean_s": sample_mean(threaded_means),
        "summary_rows": summary_df.to_dicts(),
    }


def run_part_b(part_a_manifest_path: Path = DEFAULT_PART_A_MANIFEST) -> dict[str, Any]:
    """Run part B end to end and return its manifest payload."""

    ensure_experiment_directories()
    part_a_manifest = _load_part_a_manifest(part_a_manifest_path)
    config, config_source = _resolve_capture_config(part_a_manifest)
    mix = _workload_mix(config)
    started_at_utc = now_text()
    try:
        verification_snapshot, verification_trace, verification_capture_log = _capture_trace_with_repo_redis(
            PART_B_VERIFICATION_SLUG,
            record_count=VERIFY_RECORD_COUNT,
            operation_count=VERIFY_OPERATION_COUNT,
            mix=mix,
        )
        _generate_breakpoints(
            verification_trace,
            PART_B_VERIFICATION_BREAKPOINTS_PATH,
            hot_keys=VERIFY_HOT_KEYS,
            random_rows=VERIFY_RANDOM_ROWS,
        )
        _run_replay_with_dispatch(
            snapshot_path=verification_snapshot,
            trace_path=verification_trace,
            breakpoints_path=PART_B_VERIFICATION_BREAKPOINTS_PATH,
            results_path=PART_B_VERIFICATION_RESULTS_PATH,
            runs=VERIFY_RUNS,
            break_dispatch="mixed",
            log_path=PART_B_VERIFICATION_LOG_PATH,
        )
        _assert_mixed_dispatch_results(
            PART_B_VERIFICATION_RESULTS_PATH,
            runs=VERIFY_RUNS,
            expected_rows=5,
        )

        full_snapshot, full_trace, full_capture_log = _capture_trace_with_repo_redis(
            PART_B_FULL_SLUG,
            record_count=config.record_count,
            operation_count=config.operation_count,
            mix=mix,
        )
        _generate_breakpoints(
            full_trace,
            PART_B_BREAKPOINTS_PATH,
            hot_keys=FULL_HOT_KEYS,
            random_rows=FULL_RANDOM_ROWS,
        )
        _run_replay_with_dispatch(
            snapshot_path=full_snapshot,
            trace_path=full_trace,
            breakpoints_path=PART_B_BREAKPOINTS_PATH,
            results_path=PART_B_RESULTS_PATH,
            runs=FULL_RUNS,
            break_dispatch="mixed",
            log_path=PART_B_RUNTIME_LOG_PATH,
        )
        results_df = _assert_mixed_dispatch_results(
            PART_B_RESULTS_PATH,
            runs=FULL_RUNS,
            expected_rows=16,
        )
        finished_at_utc = now_text()
    finally:
        stop_repo_redis()

    verification_replay_command = _shell_join(
        [
            sys.executable,
            "python/kernmlops/replay/replay_trace.py",
            str(verification_snapshot),
            str(verification_trace),
            "--breakpoints",
            str(PART_B_VERIFICATION_BREAKPOINTS_PATH),
            "--output",
            str(PART_B_VERIFICATION_RESULTS_PATH),
            "--runs",
            str(VERIFY_RUNS),
            "--break-dispatch",
            "mixed",
            "-v",
        ]
    )
    full_replay_command = _shell_join(
        [
            sys.executable,
            "python/kernmlops/replay/replay_trace.py",
            str(full_snapshot),
            str(full_trace),
            "--breakpoints",
            str(PART_B_BREAKPOINTS_PATH),
            "--output",
            str(PART_B_RESULTS_PATH),
            "--runs",
            str(FULL_RUNS),
            "--break-dispatch",
            "mixed",
            "-v",
        ]
    )
    summary = _render_artifacts(
        results_df=results_df,
        trace_path=full_trace,
        config=config,
        config_source=config_source,
        verification_artifacts={
            "snapshot": str(verification_snapshot),
            "trace": str(verification_trace),
            "capture_log": str(verification_capture_log),
            "runtime_log": str(PART_B_VERIFICATION_LOG_PATH),
            "results": str(PART_B_VERIFICATION_RESULTS_PATH),
        },
        full_artifacts={
            "snapshot": str(full_snapshot),
            "trace": str(full_trace),
            "capture_log": str(full_capture_log),
            "runtime_log": str(PART_B_RUNTIME_LOG_PATH),
            "results": str(PART_B_RESULTS_PATH),
        },
        started_at_utc=started_at_utc,
        finished_at_utc=finished_at_utc,
    )

    manifest = {
        "part": "B",
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
        "config_source": config_source,
        "capture_config": config.manifest_dict(),
        "row_layout": {"hot_keys": FULL_HOT_KEYS, "random_rows": FULL_RANDOM_ROWS, "runs": FULL_RUNS},
        "verification": {
            "record_count": VERIFY_RECORD_COUNT,
            "operation_count": VERIFY_OPERATION_COUNT,
            "hot_keys": VERIFY_HOT_KEYS,
            "random_rows": VERIFY_RANDOM_ROWS,
            "runs": VERIFY_RUNS,
        },
        "commands": {
            "verification_replay": verification_replay_command,
            "full_replay": full_replay_command,
        },
        "artifacts": {
            "verification_breakpoints": str(PART_B_VERIFICATION_BREAKPOINTS_PATH),
            "verification_results": str(PART_B_VERIFICATION_RESULTS_PATH),
            "verification_runtime_log": str(PART_B_VERIFICATION_LOG_PATH),
            "full_breakpoints": str(PART_B_BREAKPOINTS_PATH),
            "full_results": str(PART_B_RESULTS_PATH),
            "full_runtime_log": str(PART_B_RUNTIME_LOG_PATH),
            "html": str(PART_B_HTML_PATH),
            "runtime_png": str(PART_B_PNG_PATH),
            "split_png": str(PART_B_SPLIT_PNG_PATH),
            "markdown": str(PART_B_REPORT_PATH),
        },
        "summary": summary,
        "notes": [
            "Runs 1-4 use inline break dispatch and runs 5-8 use threaded dispatch.",
            "Replay remains runtime-only and does not collect dTLB or other hardware counters.",
            "Threaded dispatch intentionally allows the triggering access to continue before the split finishes.",
        ],
    }
    write_json(PART_B_MANIFEST_PATH, manifest)
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--part-a-manifest",
        type=Path,
        default=DEFAULT_PART_A_MANIFEST,
        help="Path to the part A manifest that chooses the replay config.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run part B from the command line."""

    args = _build_parser().parse_args(argv)
    manifest = run_part_b(args.part_a_manifest)
    print(
        pl.DataFrame(
            [
                {
                    "config_source": manifest["config_source"],
                    "full_results": manifest["artifacts"]["full_results"],
                }
            ]
        )
    )


if __name__ == "__main__":
    main()
