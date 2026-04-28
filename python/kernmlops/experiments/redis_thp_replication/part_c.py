"""Part C: compare replay runtime with Redis unpinned versus pinned."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _PACKAGE_ROOT = Path(__file__).resolve().parents[3]
    _PACKAGE_ROOT_STR = str(_PACKAGE_ROOT)
    if _PACKAGE_ROOT_STR not in sys.path:
        sys.path.insert(0, _PACKAGE_ROOT_STR)

import polars as pl
import redis

from experiments.redis_thp_replication.common import (
    PART_C_DIR,
    TEMP_ANALYSIS_DIR,
    RawBenchmarkConfig,
    ensure_experiment_directories,
    now_text,
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
from replay.replay_trace import (
    BREAK_PAGE_MAX_ATTEMPTS,
    _KHUGEPAGED_SLEEP_PATH,
    _get_redis_pid,
    _set_thp_enabled_mode,
    _sysfs_write,
    memory_purge,
    replay,
    restore_snapshot,
    setup_system,
    teardown_system,
)
from replay.run_overnight_runtime_matrix import (
    WorkloadMix,
    _assert_runtime_only_results,
    _capture_output,
    _capture_trace,
    _chown_path,
    _generate_breakpoints,
    _repo_redis_cli_command,
    _safe_unlink,
    _shell_join,
    _utc_now_text,
)
from redis_runtime import load_repo_redis_endpoint


DEFAULT_PART_A_MANIFEST = PART_C_DIR.parent / "part_a" / "part_a_manifest.json"
PART_C_VERIFICATION_RESULTS_PATH = PART_C_DIR / "part_c_verification_results_4rows.parquet"
PART_C_RESULTS_PATH = PART_C_DIR / "part_c_results_8rows.parquet"
PART_C_VERIFICATION_LOG_PATH = PART_C_DIR / "part_c_verification_runtime.log"
PART_C_RUNTIME_LOG_PATH = PART_C_DIR / "part_c_runtime.log"
PART_C_VERIFICATION_BREAKPOINTS_PATH = PART_C_DIR / "part_c_verification_breakpoints_4rows.parquet"
PART_C_BREAKPOINTS_PATH = PART_C_DIR / "part_c_breakpoints_8rows.parquet"
PART_C_MANIFEST_PATH = PART_C_DIR / "part_c_manifest.json"
PART_C_REPORT_PATH = PART_C_DIR / "part_c_summary.md"
PART_C_PNG_PATH = TEMP_ANALYSIS_DIR / "redis_thp_part_c_runtime_compare.png"
PART_C_SPLIT_PNG_PATH = TEMP_ANALYSIS_DIR / "redis_thp_part_c_split_latency.png"
PART_C_HTML_PATH = TEMP_ANALYSIS_DIR / "redis_thp_part_c_runtime_compare.html"

PART_C_VERIFICATION_SLUG = "redis_thp_part_c_verification"
PART_C_FULL_SLUG = "redis_thp_part_c_pinning_26_8runs"

VERIFY_RECORD_COUNT = 1024
VERIFY_OPERATION_COUNT = 4096
VERIFY_HOT_KEYS = 2
VERIFY_RANDOM_ROWS = 0
VERIFY_RUNS = 2
FULL_HOT_KEYS = 6
FULL_RANDOM_ROWS = 0
FULL_RUNS = 8
UNPINNED_VERIFY_RUNS = [1]
PINNED_VERIFY_RUNS = [2]
UNPINNED_FULL_RUNS = [1, 2, 3, 4]
PINNED_FULL_RUNS = [5, 6, 7, 8]


def _load_part_a_manifest(path: Path) -> dict[str, Any]:
    """Load part A's manifest so part C can reuse its chosen config."""

    if not path.exists():
        raise RuntimeError(
            f"Part A manifest not found at {path}. Run part_a.py before part_c.py."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_capture_config(part_a_manifest: dict[str, Any]) -> tuple[RawBenchmarkConfig, str]:
    """Return the replay config selected by part A."""

    payload = part_a_manifest.get("recommended_capture_config")
    if not isinstance(payload, dict):
        raise RuntimeError("Part A manifest is missing recommended_capture_config.")
    return RawBenchmarkConfig(**payload), str(
        part_a_manifest.get("recommended_capture_source", "unknown")
    )


def _workload_mix(config: RawBenchmarkConfig) -> WorkloadMix:
    """Convert one chosen config into capture-script proportions."""

    return WorkloadMix(
        read=config.read_proportion,
        delete=config.delete_proportion,
        insert=config.insert_proportion,
        update=config.update_proportion,
        scan=config.scan_proportion,
        readmodifywrite=config.readmodifywrite_proportion,
    )


def _all_cpu_spec() -> str:
    """Return the CPU mask used for the unpinned Redis runs."""

    cpu_count = os.cpu_count() or 1
    return "0" if cpu_count == 1 else f"0-{cpu_count - 1}"


def _set_process_affinity(pid: int, cpu_spec: str) -> str:
    """Apply one `taskset -cp` mask and return the resulting affinity text."""

    output = _capture_output(["taskset", "-cp", cpu_spec, str(pid)]).strip()
    if not output:
        raise RuntimeError(f"taskset did not report an affinity change for pid {pid}.")
    return _capture_output(["taskset", "-cp", str(pid)]).strip()


def _run_pinning_replay(
    *,
    snapshot_path: Path,
    trace_path: Path,
    breakpoints_path: Path,
    results_path: Path,
    runs: int,
    pinned_runs: list[int],
    log_path: Path,
) -> pl.DataFrame:
    """Run a custom replay matrix while toggling Redis CPU pinning.

    This wrapper keeps the standard replay THP controls but re-enables
    ``khugepaged`` at ``500 ms`` so the pinning comparison runs in the
    user-requested environment.
    """

    _safe_unlink(results_path)
    _safe_unlink(log_path)
    breakpoints_df = pl.read_parquet(breakpoints_path)
    redis_endpoint = load_repo_redis_endpoint()
    client = redis.Redis(
        host=redis_endpoint.host,
        port=redis_endpoint.port,
        decode_responses=True,
    )
    system_config = setup_system()
    _sysfs_write(_KHUGEPAGED_SLEEP_PATH, "500")
    results: list[dict[str, Any]] = []
    n_combos = len(breakpoints_df)
    unpinned_spec = _all_cpu_spec()

    with open(log_path, "w", encoding="utf-8") as log_file:
        def log(message: str) -> None:
            line = f"[{_utc_now_text()}] {message}"
            print(line, flush=True)
            log_file.write(line + "\n")
            log_file.flush()

        try:
            for idx, row in enumerate(breakpoints_df.iter_rows(named=True)):
                breakpoints: dict[str, int] = dict(row)
                row_result: dict[str, Any] = {**breakpoints}
                is_base_pages = idx == 0
                thp_mode = "never" if is_base_pages else "always"
                effective_breakpoints = {} if is_base_pages else breakpoints

                for run_number in range(1, runs + 1):
                    pin_mode = "pinned" if run_number in pinned_runs else "unpinned"
                    log(
                        f"[{idx + 1}/{n_combos} run {run_number}/{runs}] Restoring snapshot "
                        f"(THP {thp_mode}, pin mode {pin_mode})"
                    )
                    _set_thp_enabled_mode(thp_mode)
                    restore_snapshot(client, snapshot_path)
                    memory_purge(client)
                    redis_pid = _get_redis_pid(client)
                    affinity = _set_process_affinity(
                        redis_pid,
                        "0" if pin_mode == "pinned" else unpinned_spec,
                    )

                    started = time.perf_counter()
                    replay_stats = replay(
                        trace_path,
                        client,
                        breakpoints=effective_breakpoints,
                        break_dispatch="inline",
                    )
                    runtime_s = time.perf_counter() - started
                    log(
                        f"[{idx + 1}/{n_combos} run {run_number}/{runs}] Done in "
                        f"{runtime_s:.3f}s with affinity {affinity}"
                    )

                    row_result["row_index"] = idx
                    row_result["thp_mode"] = thp_mode
                    row_result[f"pin_mode_{run_number}"] = pin_mode
                    row_result[f"redis_affinity_{run_number}"] = affinity
                    row_result[f"runtime_s_{run_number}"] = runtime_s
                    row_result[f"commands_replayed_{run_number}"] = replay_stats.commands_replayed
                    row_result[f"split_events_{run_number}"] = replay_stats.split_events
                    row_result[f"split_successes_{run_number}"] = replay_stats.split_successes
                    row_result[f"split_failures_{run_number}"] = replay_stats.split_failures
                    row_result[f"split_syscall_attempts_{run_number}"] = (
                        replay_stats.split_syscall_attempts
                    )
                    row_result[f"split_max_attempts_{run_number}"] = (
                        replay_stats.split_max_attempts
                    )
                    row_result[f"split_total_wall_ms_{run_number}"] = (
                        replay_stats.split_total_wall_ms
                    )
                    row_result[f"split_max_wall_ms_{run_number}"] = (
                        replay_stats.split_max_wall_ms
                    )
                    row_result[f"split_queue_lag_ms_mean_{run_number}"] = (
                        replay_stats.split_queue_lag_ms_mean
                    )
                    row_result[f"split_queue_lag_ms_max_{run_number}"] = (
                        replay_stats.split_queue_lag_ms_max
                    )

                results.append(row_result)

            result_df = pl.DataFrame(results)
            result_df.write_parquet(results_path)
            _chown_path(results_path)
            return result_df
        finally:
            try:
                redis_pid = _get_redis_pid(client)
                _set_process_affinity(redis_pid, unpinned_spec)
            except Exception:
                pass
            teardown_system(system_config)
            _chown_path(log_path)


def _assert_pinning_results(
    results_path: Path,
    *,
    runs: int,
    expected_rows: int,
    pinned_runs: list[int],
) -> pl.DataFrame:
    """Validate the custom pinning replay result and its affinity metadata."""

    df = _assert_runtime_only_results(results_path, runs=runs)
    if df.height != expected_rows:
        raise RuntimeError(
            f"Expected {expected_rows} replay rows, found {df.height} in {results_path}."
        )
    for run_number in range(1, runs + 1):
        expected_mode = "pinned" if run_number in pinned_runs else "unpinned"
        column = f"pin_mode_{run_number}"
        if column not in df.columns:
            raise RuntimeError(f"Missing pin-mode column {column} in {results_path}.")
        if set(df[column].to_list()) != {expected_mode}:
            raise RuntimeError(
                f"{column} was not uniformly {expected_mode} in {results_path}."
            )

    differing_affinity = False
    if runs >= 2:
        left_column = "redis_affinity_1"
        right_column = f"redis_affinity_{runs}"
        if left_column in df.columns and right_column in df.columns:
            for left, right in zip(df[left_column].to_list(), df[right_column].to_list(), strict=True):
                if left != right:
                    differing_affinity = True
                    break
    if not differing_affinity:
        raise RuntimeError("Pinning verification did not observe a different affinity mask.")
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
    """Render the part C HTML/PNG outputs and return summary metadata."""

    records = classify_breakpoint_rows(
        results_df,
        trace_path=trace_path,
        hot_key_rows=FULL_HOT_KEYS,
    )
    categories = [record["label"] for record in records]
    unpinned_means = [
        sample_mean(
            [float(value) for value in slice_runs(record["runtime_samples"], UNPINNED_FULL_RUNS)]
        )
        for record in records
    ]
    unpinned_stds = [
        sample_std(
            [float(value) for value in slice_runs(record["runtime_samples"], UNPINNED_FULL_RUNS)]
        )
        for record in records
    ]
    pinned_means = [
        sample_mean(
            [float(value) for value in slice_runs(record["runtime_samples"], PINNED_FULL_RUNS)]
        )
        for record in records
    ]
    pinned_stds = [
        sample_std(
            [float(value) for value in slice_runs(record["runtime_samples"], PINNED_FULL_RUNS)]
        )
        for record in records
    ]

    unpinned_split_wall_means = [
        sample_mean(
            [
                float(value)
                for value in slice_runs(record["split_total_wall_ms_samples"], UNPINNED_FULL_RUNS)
            ]
        )
        for record in records
    ]
    pinned_split_wall_means = [
        sample_mean(
            [
                float(value)
                for value in slice_runs(record["split_total_wall_ms_samples"], PINNED_FULL_RUNS)
            ]
        )
        for record in records
    ]
    unpinned_split_wall_stds = [
        sample_std(
            [
                float(value)
                for value in slice_runs(record["split_total_wall_ms_samples"], UNPINNED_FULL_RUNS)
            ]
        )
        for record in records
    ]
    pinned_split_wall_stds = [
        sample_std(
            [
                float(value)
                for value in slice_runs(record["split_total_wall_ms_samples"], PINNED_FULL_RUNS)
            ]
        )
        for record in records
    ]

    grouped_bar_png(
        categories=categories,
        series=[
            {
                "label": "Unpinned",
                "means": unpinned_means,
                "stds": unpinned_stds,
                "color": "#1f77b4",
            },
            {
                "label": "Pinned to CPU 0",
                "means": pinned_means,
                "stds": pinned_stds,
                "color": "#d62728",
            },
        ],
        title="Part C: Replay Runtime With and Without Redis Pinning",
        ylabel="Seconds",
        output_path=PART_C_PNG_PATH,
    )
    grouped_bar_png(
        categories=categories,
        series=[
            {
                "label": "Unpinned split wall time",
                "means": unpinned_split_wall_means,
                "stds": unpinned_split_wall_stds,
                "color": "#2ca02c",
            },
            {
                "label": "Pinned split wall time",
                "means": pinned_split_wall_means,
                "stds": pinned_split_wall_stds,
                "color": "#9467bd",
            },
        ],
        title="Part C: Split Wall Time With and Without Pinning",
        ylabel="Milliseconds",
        output_path=PART_C_SPLIT_PNG_PATH,
    )

    summary_rows = []
    for record in records:
        unpinned_runtime_samples = [
            float(value) for value in slice_runs(record["runtime_samples"], UNPINNED_FULL_RUNS)
        ]
        pinned_runtime_samples = [
            float(value) for value in slice_runs(record["runtime_samples"], PINNED_FULL_RUNS)
        ]
        unpinned_affinity = ", ".join(slice_runs(record["redis_affinity_samples"], UNPINNED_FULL_RUNS))
        pinned_affinity = ", ".join(slice_runs(record["redis_affinity_samples"], PINNED_FULL_RUNS))
        summary_rows.append(
            {
                "label": record["label"],
                "row_type": record["row_type"],
                "access_count": record["access_count"],
                "unpinned_mean_s": sample_mean(unpinned_runtime_samples),
                "unpinned_std_s": sample_std(unpinned_runtime_samples),
                "pinned_mean_s": sample_mean(pinned_runtime_samples),
                "pinned_std_s": sample_std(pinned_runtime_samples),
                "unpinned_runtimes": _format_runtimes(unpinned_runtime_samples),
                "pinned_runtimes": _format_runtimes(pinned_runtime_samples),
                "unpinned_affinity": unpinned_affinity,
                "pinned_affinity": pinned_affinity,
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
                "khugepaged_scan_sleep_millis": 500,
                "break_page_max_attempts": BREAK_PAGE_MAX_ATTEMPTS,
                "verification_layout": "2-2, 2 runs (unpinned then pinned)",
                "full_layout": "2-6, 8 runs (4 unpinned then 4 pinned)",
            }
        ]
    )

    speedup_pct = None
    base_row = next((record for record in records if record["label"] == "base_pages"), None)
    if base_row is not None:
        base_unpinned = sample_mean(
            [float(value) for value in slice_runs(base_row["runtime_samples"], UNPINNED_FULL_RUNS)]
        )
        base_pinned = sample_mean(
            [float(value) for value in slice_runs(base_row["runtime_samples"], PINNED_FULL_RUNS)]
        )
        if base_unpinned:
            speedup_pct = ((base_unpinned - base_pinned) / base_unpinned) * 100.0

    bullets = [
        f"Started: {started_at_utc}",
        f"Finished: {finished_at_utc}",
        f"Verification runtime log: {verification_artifacts['runtime_log']}",
        f"Full runtime log: {full_artifacts['runtime_log']}",
        (
            f"Base-pages pinning delta: {speedup_pct:.2f}% faster pinned."
            if speedup_pct is not None
            else "Base-pages pinning delta was not available."
        ),
    ]
    write_html_report(
        title="Part C: Replay Runtime With and Without Redis Pinning",
        subtitle=(
            "This experiment keeps khugepaged enabled at 500 ms and compares "
            "four unpinned runs against four runs with redis-server pinned to CPU 0."
        ),
        sections=[
            {
                "title": "Configuration",
                "description": "Pinning is treated as a scheduler-noise question, not as the main expected source of speedup.",
                "table": config_df,
                "bullets": bullets,
            },
            {
                "title": "Runtime Comparison",
                "description": "Grouped means with sample-standard-deviation error bars for unpinned versus pinned replay runs.",
                "image_path": PART_C_PNG_PATH,
            },
            {
                "title": "Split Wall Time",
                "description": "This supplemental chart shows whether pinning noticeably changed replay-time split wall cost.",
                "image_path": PART_C_SPLIT_PNG_PATH,
            },
            {
                "title": "Per-Row Summary",
                "description": "Each row includes all runtime samples and the observed Redis affinity masks.",
                "table": summary_df,
            },
        ],
        output_path=PART_C_HTML_PATH,
    )
    write_markdown(
        PART_C_REPORT_PATH,
        "\n".join(
            [
                "# Part C Summary",
                "",
                f"- started: `{started_at_utc}`",
                f"- finished: `{finished_at_utc}`",
                f"- config source: `{config_source}`",
                f"- verification results: `{verification_artifacts['results']}`",
                f"- full results: `{full_artifacts['results']}`",
                f"- html: `{PART_C_HTML_PATH}`",
                f"- runtime png: `{PART_C_PNG_PATH}`",
                f"- split png: `{PART_C_SPLIT_PNG_PATH}`",
                "",
            ]
        ),
    )
    return {
        "records": len(records),
        "unpinned_overall_mean_s": sample_mean(unpinned_means),
        "pinned_overall_mean_s": sample_mean(pinned_means),
        "base_pages_pinning_delta_pct": speedup_pct,
        "summary_rows": summary_df.to_dicts(),
    }


def run_part_c(part_a_manifest_path: Path = DEFAULT_PART_A_MANIFEST) -> dict[str, Any]:
    """Run part C end to end and return its manifest payload."""

    ensure_experiment_directories()
    part_a_manifest = _load_part_a_manifest(part_a_manifest_path)
    config, config_source = _resolve_capture_config(part_a_manifest)
    mix = _workload_mix(config)
    started_at_utc = now_text()

    verification_snapshot, verification_trace, verification_capture_log = _capture_trace(
        PART_C_VERIFICATION_SLUG,
        record_count=VERIFY_RECORD_COUNT,
        operation_count=VERIFY_OPERATION_COUNT,
        mix=mix,
    )
    _generate_breakpoints(
        verification_trace,
        PART_C_VERIFICATION_BREAKPOINTS_PATH,
        hot_keys=VERIFY_HOT_KEYS,
        random_rows=VERIFY_RANDOM_ROWS,
    )
    _run_pinning_replay(
        snapshot_path=verification_snapshot,
        trace_path=verification_trace,
        breakpoints_path=PART_C_VERIFICATION_BREAKPOINTS_PATH,
        results_path=PART_C_VERIFICATION_RESULTS_PATH,
        runs=VERIFY_RUNS,
        pinned_runs=PINNED_VERIFY_RUNS,
        log_path=PART_C_VERIFICATION_LOG_PATH,
    )
    _assert_pinning_results(
        PART_C_VERIFICATION_RESULTS_PATH,
        runs=VERIFY_RUNS,
        expected_rows=4,
        pinned_runs=PINNED_VERIFY_RUNS,
    )

    full_snapshot, full_trace, full_capture_log = _capture_trace(
        PART_C_FULL_SLUG,
        record_count=config.record_count,
        operation_count=config.operation_count,
        mix=mix,
    )
    _generate_breakpoints(
        full_trace,
        PART_C_BREAKPOINTS_PATH,
        hot_keys=FULL_HOT_KEYS,
        random_rows=FULL_RANDOM_ROWS,
    )
    _run_pinning_replay(
        snapshot_path=full_snapshot,
        trace_path=full_trace,
        breakpoints_path=PART_C_BREAKPOINTS_PATH,
        results_path=PART_C_RESULTS_PATH,
        runs=FULL_RUNS,
        pinned_runs=PINNED_FULL_RUNS,
        log_path=PART_C_RUNTIME_LOG_PATH,
    )
    results_df = _assert_pinning_results(
        PART_C_RESULTS_PATH,
        runs=FULL_RUNS,
        expected_rows=8,
        pinned_runs=PINNED_FULL_RUNS,
    )
    try:
        redis_pid = int(
            next(
                line.split(":", 1)[1].strip()
                for line in _capture_output(_repo_redis_cli_command("INFO", "server")).splitlines()
                if line.startswith("process_id:")
            )
        )
        _set_process_affinity(redis_pid, _all_cpu_spec())
    except Exception:
        pass
    finished_at_utc = now_text()

    summary = _render_artifacts(
        results_df=results_df,
        trace_path=full_trace,
        config=config,
        config_source=config_source,
        verification_artifacts={
            "snapshot": str(verification_snapshot),
            "trace": str(verification_trace),
            "capture_log": str(verification_capture_log),
            "runtime_log": str(PART_C_VERIFICATION_LOG_PATH),
            "results": str(PART_C_VERIFICATION_RESULTS_PATH),
        },
        full_artifacts={
            "snapshot": str(full_snapshot),
            "trace": str(full_trace),
            "capture_log": str(full_capture_log),
            "runtime_log": str(PART_C_RUNTIME_LOG_PATH),
            "results": str(PART_C_RESULTS_PATH),
        },
        started_at_utc=started_at_utc,
        finished_at_utc=finished_at_utc,
    )

    manifest = {
        "part": "C",
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
        "artifacts": {
            "verification_breakpoints": str(PART_C_VERIFICATION_BREAKPOINTS_PATH),
            "verification_results": str(PART_C_VERIFICATION_RESULTS_PATH),
            "verification_runtime_log": str(PART_C_VERIFICATION_LOG_PATH),
            "full_breakpoints": str(PART_C_BREAKPOINTS_PATH),
            "full_results": str(PART_C_RESULTS_PATH),
            "full_runtime_log": str(PART_C_RUNTIME_LOG_PATH),
            "html": str(PART_C_HTML_PATH),
            "runtime_png": str(PART_C_PNG_PATH),
            "split_png": str(PART_C_SPLIT_PNG_PATH),
            "markdown": str(PART_C_REPORT_PATH),
        },
        "summary": summary,
        "notes": [
            "This part keeps khugepaged enabled at 500 ms and compares unpinned versus pinned redis-server runs.",
            "Replay remains runtime-only and does not collect dTLB or other hardware counters.",
            "Pinning is restored to the normal all-CPU mask after the experiment.",
        ],
    }
    write_json(PART_C_MANIFEST_PATH, manifest)
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
    """Run part C from the command line."""

    args = _build_parser().parse_args(argv)
    manifest = run_part_c(args.part_a_manifest)
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
