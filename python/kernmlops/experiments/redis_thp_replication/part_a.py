"""Part A: replicate and retune the Redis THP benchmark with raw load/run timing."""

from __future__ import annotations

import re
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _PACKAGE_ROOT = Path(__file__).resolve().parents[3]
    _PACKAGE_ROOT_STR = str(_PACKAGE_ROOT)
    if _PACKAGE_ROOT_STR not in sys.path:
        sys.path.insert(0, _PACKAGE_ROOT_STR)

import polars as pl

from experiments.redis_thp_replication.common import (
    PART_A_DIR,
    RawBenchmarkConfig,
    TEMP_ANALYSIS_DIR,
    apply_benchmark_system_config,
    build_ycsb_command,
    container_available_memory_bytes,
    ensure_experiment_directories,
    force_rdb_and_measure,
    now_text,
    pin_process,
    projected_dump_bytes,
    read_process_affinity,
    redis_memory_purge,
    repo_free_bytes,
    restore_system,
    snapshot_system,
    start_repo_redis,
    stop_repo_redis,
    timed_run_command,
    write_dataframe,
    write_json,
    write_markdown,
)
from experiments.redis_thp_replication.reporting import (
    grouped_bar_png,
    write_html_report,
)


YCSB_RUNTIME_RE = re.compile(r"^\[OVERALL\], RunTime\(ms\), (\d+)$", re.MULTILINE)
PART_A_SUMMARY_PATH = PART_A_DIR / "part_a_results.parquet"
PART_A_CYCLE_PATH = PART_A_DIR / "part_a_cycle_results.parquet"
PART_A_MANIFEST_PATH = PART_A_DIR / "part_a_manifest.json"
PART_A_PNG_PATH = TEMP_ANALYSIS_DIR / "redis_thp_part_a_runtime_compare.png"
PART_A_SUPPLEMENTAL_PNG_PATH = TEMP_ANALYSIS_DIR / "redis_thp_part_a_size_compare.png"
PART_A_HTML_PATH = TEMP_ANALYSIS_DIR / "redis_thp_part_a_runtime_compare.html"
PART_A_REPORT_PATH = PART_A_DIR / "part_a_summary.md"
SEARCH_BUDGET_S = 90 * 60
RUN_TARGET_S = 120.0
RUN_TARGET_MIN_S = 110.0
RUN_TARGET_MAX_S = 130.0
RESOURCE_HEADROOM = 0.60
BASELINE_RECORD_COUNT = 4096


def parse_ycsb_overall_runtime_ms(log_path: Path) -> list[int]:
    """Return all ``[OVERALL], RunTime(ms)`` values found in one YCSB log."""

    return [int(match) for match in YCSB_RUNTIME_RE.findall(log_path.read_text(encoding="utf-8"))]


def _screenshot_config(*, thp_mode: str) -> RawBenchmarkConfig:
    return RawBenchmarkConfig(
        name="screenshot_exact",
        thp_mode=thp_mode,
        record_count=4096,
        operation_count=4096,
        outer_repeat=10,
        read_proportion=0.05,
        delete_proportion=0.95,
    )


def _candidate_configs() -> list[RawBenchmarkConfig]:
    return [
        RawBenchmarkConfig(
            name="same_mix_equal_records",
            thp_mode="always",
            record_count=4096,
            operation_count=4096,
            outer_repeat=1,
            read_proportion=0.05,
            delete_proportion=0.95,
        ),
        RawBenchmarkConfig(
            name="read80_delete20",
            thp_mode="always",
            record_count=4096,
            operation_count=8192,
            outer_repeat=1,
            read_proportion=0.80,
            delete_proportion=0.20,
        ),
        RawBenchmarkConfig(
            name="read70_delete20_insert10",
            thp_mode="always",
            record_count=4096,
            operation_count=8192,
            outer_repeat=1,
            read_proportion=0.70,
            delete_proportion=0.20,
            insert_proportion=0.10,
        ),
    ]


def _cycle_runtime_record(
    *,
    stage: str,
    config: RawBenchmarkConfig,
    repeat_index: int,
    cycle_index: int,
    load_s: float,
    run_s: float,
    load_runtime_ms: int | None,
    run_runtime_ms: int | None,
    cumulative_record_count: int,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "config_name": config.name,
        "thp_mode": config.thp_mode,
        "repeat_index": repeat_index,
        "cycle_index": cycle_index,
        "load_s": load_s,
        "run_s": run_s,
        "load_runtime_ms": load_runtime_ms,
        "run_runtime_ms": run_runtime_ms,
        "cumulative_record_count": cumulative_record_count,
    }


def _run_single_repeat(
    *,
    stage: str,
    config: RawBenchmarkConfig,
    repeat_index: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run one fresh Redis load/run repeat and return summary plus per-cycle rows."""

    repeat_slug = f"{stage}_{config.thp_mode}_r{repeat_index}"
    run_dir = PART_A_DIR / repeat_slug
    apply_benchmark_system_config(
        thp_mode=config.thp_mode,
        khugepaged_scan_sleep_millis=config.khugepaged_scan_sleep_millis,
    )
    redis_pid = start_repo_redis(run_dir=run_dir)
    affinity_before = read_process_affinity(redis_pid)
    time.sleep(config.server_sleep_seconds)

    total_load_s = 0.0
    total_run_s = 0.0
    first_load_dump_bytes = 0
    first_load_dbsize = 0
    first_load_memory_info: dict[str, Any] = {}
    cycle_rows: list[dict[str, Any]] = []

    try:
        for cycle_index in range(config.outer_repeat):
            load_log_path = run_dir / f"load_cycle_{cycle_index + 1}.log"
            run_log_path = run_dir / f"run_cycle_{cycle_index + 1}.log"
            load_s = timed_run_command(
                build_ycsb_command(config, phase="load", cycle_index=cycle_index),
                log_path=load_log_path,
            )
            total_load_s += load_s
            if config.explicit_purge:
                redis_memory_purge()

            if cycle_index == 0:
                first_load_dump_bytes, first_load_dbsize, first_load_memory_info = (
                    force_rdb_and_measure(run_dir)
                )

            run_s = timed_run_command(
                build_ycsb_command(config, phase="run", cycle_index=cycle_index),
                log_path=run_log_path,
            )
            total_run_s += run_s
            if config.explicit_purge:
                redis_memory_purge()

            load_runtimes = parse_ycsb_overall_runtime_ms(load_log_path)
            run_runtimes = parse_ycsb_overall_runtime_ms(run_log_path)
            cycle_rows.append(
                _cycle_runtime_record(
                    stage=stage,
                    config=config,
                    repeat_index=repeat_index,
                    cycle_index=cycle_index + 1,
                    load_s=load_s,
                    run_s=run_s,
                    load_runtime_ms=load_runtimes[-1] if load_runtimes else None,
                    run_runtime_ms=run_runtimes[-1] if run_runtimes else None,
                    cumulative_record_count=(cycle_index + 1) * config.record_count,
                )
            )
    finally:
        stop_repo_redis()

    summary = {
        "stage": stage,
        "config_name": config.name,
        "thp_mode": config.thp_mode,
        "repeat_index": repeat_index,
        "record_count": config.record_count,
        "operation_count": config.operation_count,
        "outer_repeat": config.outer_repeat,
        "read_proportion": config.read_proportion,
        "delete_proportion": config.delete_proportion,
        "insert_proportion": config.insert_proportion,
        "load_s": total_load_s,
        "run_s": total_run_s,
        "load_plus_run_s": total_load_s + total_run_s,
        "first_load_dump_bytes": first_load_dump_bytes,
        "first_load_dbsize": first_load_dbsize,
        "first_load_used_memory_bytes": first_load_memory_info.get("used_memory", 0),
        "first_load_used_memory_peak_bytes": first_load_memory_info.get(
            "used_memory_peak", 0
        ),
        "redis_affinity_before": affinity_before,
        "run_dir": str(run_dir),
    }
    return summary, cycle_rows


def _run_config_repeats(
    *,
    stage: str,
    configs: list[RawBenchmarkConfig],
    repeats: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Run all repeats for a list of mode-specific configs."""

    summary_rows: list[dict[str, Any]] = []
    cycle_rows: list[dict[str, Any]] = []
    for config in configs:
        for repeat_index in range(1, repeats + 1):
            summary, cycles = _run_single_repeat(
                stage=stage,
                config=config,
                repeat_index=repeat_index,
            )
            summary_rows.append(summary)
            cycle_rows.extend(cycles)
    return pl.DataFrame(summary_rows), pl.DataFrame(cycle_rows)


def _mode_summary(df: pl.DataFrame) -> pl.DataFrame:
    """Aggregate per-mode mean/std for load, run, and load+run."""

    return (
        df.group_by(["stage", "config_name", "thp_mode"])
        .agg(
            pl.col("load_s").mean().alias("load_mean_s"),
            pl.col("load_s").std(ddof=1).fill_null(0.0).alias("load_std_s"),
            pl.col("run_s").mean().alias("run_mean_s"),
            pl.col("run_s").std(ddof=1).fill_null(0.0).alias("run_std_s"),
            pl.col("load_plus_run_s").mean().alias("load_plus_run_mean_s"),
            pl.col("load_plus_run_s")
            .std(ddof=1)
            .fill_null(0.0)
            .alias("load_plus_run_std_s"),
            pl.col("first_load_dump_bytes")
            .mean()
            .alias("first_load_dump_bytes_mean"),
            pl.col("first_load_used_memory_bytes")
            .mean()
            .alias("first_load_used_memory_bytes_mean"),
        )
        .sort(["stage", "config_name", "thp_mode"])
    )


def _clear_thp_effect(mode_summary: pl.DataFrame, *, stage: str) -> bool:
    """Return true when ``always`` is clearly faster than ``never`` for run time."""

    stage_summary = mode_summary.filter(pl.col("stage") == stage)
    if stage_summary.height == 0:
        return False
    always_row = stage_summary.filter(pl.col("thp_mode") == "always")
    never_row = stage_summary.filter(pl.col("thp_mode") == "never")
    if always_row.height != 1 or never_row.height != 1:
        return False

    always_mean = float(always_row["run_mean_s"][0])
    never_mean = float(never_row["run_mean_s"][0])
    always_std = float(always_row["run_std_s"][0])
    never_std = float(never_row["run_std_s"][0])
    delta = never_mean - always_mean
    pct = delta / never_mean if never_mean else 0.0
    return pct >= 0.05 and delta > max(always_std, never_std)


def _resource_feasible(
    *,
    projected_record_count: int,
    baseline_dump_bytes: int,
    baseline_used_memory_bytes: int,
) -> tuple[bool, dict[str, int]]:
    """Check whether a projected record count stays inside conservative limits."""

    projected_dump = projected_dump_bytes(
        baseline_dump_bytes=baseline_dump_bytes,
        baseline_record_count=BASELINE_RECORD_COUNT,
        projected_record_count=projected_record_count,
    )
    projected_memory = projected_dump_bytes(
        baseline_dump_bytes=baseline_used_memory_bytes,
        baseline_record_count=BASELINE_RECORD_COUNT,
        projected_record_count=projected_record_count,
    )
    disk_limit = int(repo_free_bytes() * RESOURCE_HEADROOM)
    memory_limit = int(container_available_memory_bytes() * RESOURCE_HEADROOM)
    feasible = projected_dump <= disk_limit and projected_memory <= memory_limit
    return feasible, {
        "projected_dump_bytes": projected_dump,
        "projected_used_memory_bytes": projected_memory,
        "disk_limit_bytes": disk_limit,
        "memory_limit_bytes": memory_limit,
    }


def _calibrate_candidate(
    *,
    template: RawBenchmarkConfig,
    baseline_dump_bytes: int,
    baseline_used_memory_bytes: int,
    budget_deadline: float,
) -> tuple[RawBenchmarkConfig | None, list[dict[str, Any]], pl.DataFrame, pl.DataFrame]:
    """Calibrate one candidate on ``always`` only until run time is near 120s."""

    attempts: list[dict[str, Any]] = []
    summaries: list[pl.DataFrame] = []
    cycles: list[pl.DataFrame] = []
    current = template
    attempt_index = 1
    while time.monotonic() < budget_deadline:
        feasible, resource_projection = _resource_feasible(
            projected_record_count=current.record_count * current.outer_repeat,
            baseline_dump_bytes=baseline_dump_bytes,
            baseline_used_memory_bytes=baseline_used_memory_bytes,
        )
        if not feasible:
            attempts.append(
                {
                    "attempt_index": attempt_index,
                    "record_count": current.record_count,
                    "operation_count": current.operation_count,
                    "accepted": False,
                    "reason": "projected_resource_limit",
                    **resource_projection,
                }
            )
            return None, attempts, pl.DataFrame([]), pl.DataFrame([])

        stage = f"candidate_{template.name}_calibration_{attempt_index}"
        summary_df, cycle_df = _run_config_repeats(
            stage=stage,
            configs=[current],
            repeats=1,
        )
        summaries.append(summary_df)
        cycles.append(cycle_df)
        run_s = float(summary_df["run_s"][0])
        accepted = RUN_TARGET_MIN_S <= run_s <= RUN_TARGET_MAX_S
        attempts.append(
            {
                "attempt_index": attempt_index,
                "record_count": current.record_count,
                "operation_count": current.operation_count,
                "run_s": run_s,
                "accepted": accepted,
                "reason": "within_target_band" if accepted else "retune",
                **resource_projection,
            }
        )
        if accepted:
            return (
                current,
                attempts,
                pl.concat(summaries) if summaries else pl.DataFrame([]),
                pl.concat(cycles) if cycles else pl.DataFrame([]),
            )

        scale = RUN_TARGET_S / run_s
        new_operation_count = max(1, int(round(current.operation_count * scale)))
        if template.name == "same_mix_equal_records":
            new_record_count = new_operation_count
        else:
            new_record_count = max(1, int(round(new_operation_count / 2)))
        current = replace(
            current,
            record_count=new_record_count,
            operation_count=new_operation_count,
        )
        attempt_index += 1

    return None, attempts, pl.concat(summaries) if summaries else pl.DataFrame([]), pl.concat(cycles) if cycles else pl.DataFrame([])


def _render_part_a_artifacts(
    *,
    summary_df: pl.DataFrame,
    manifest: dict[str, Any],
) -> None:
    """Render the main PNG and HTML outputs for part A."""

    mode_summary = _mode_summary(summary_df)
    final_stage = manifest["chosen_stage"]
    final_summary = mode_summary.filter(pl.col("stage") == final_stage)
    categories = ["always", "madvise", "never"]

    def column_for_mode(column: str) -> list[float]:
        values: list[float] = []
        for mode in categories:
            row = final_summary.filter(pl.col("thp_mode") == mode)
            values.append(float(row[column][0]) if row.height == 1 else 0.0)
        return values

    grouped_bar_png(
        categories=categories,
        series=[
            {
                "label": "Run only",
                "means": column_for_mode("run_mean_s"),
                "stds": column_for_mode("run_std_s"),
                "color": "#1f77b4",
            },
            {
                "label": "Load + run",
                "means": column_for_mode("load_plus_run_mean_s"),
                "stds": column_for_mode("load_plus_run_std_s"),
                "color": "#d62728",
            },
        ],
        title="Part A: Redis THP Runtime Comparison",
        ylabel="Seconds",
        output_path=PART_A_PNG_PATH,
    )

    grouped_bar_png(
        categories=categories,
        series=[
            {
                "label": "Load only",
                "means": column_for_mode("load_mean_s"),
                "stds": column_for_mode("load_std_s"),
                "color": "#2ca02c",
            },
            {
                "label": "RDB size (GiB)",
                "means": [
                    value / float(1024**3)
                    for value in column_for_mode("first_load_dump_bytes_mean")
                ],
                "stds": [0.0, 0.0, 0.0],
                "color": "#9467bd",
            },
            {
                "label": "Redis used memory (GiB)",
                "means": [
                    value / float(1024**3)
                    for value in column_for_mode("first_load_used_memory_bytes_mean")
                ],
                "stds": [0.0, 0.0, 0.0],
                "color": "#ff7f0e",
            },
        ],
        title="Part A: Load Cost and Footprint",
        ylabel="Seconds / GiB",
        output_path=PART_A_SUPPLEMENTAL_PNG_PATH,
    )

    write_html_report(
        title="Part A: Redis THP Runtime Comparison",
        subtitle=(
            "Blue bars show run-only means, red bars show load+run means, and "
            "all bars include sample-standard-deviation error lines."
        ),
        sections=[
            {
                "title": "Chosen Configuration",
                "description": (
                    f"Chosen stage: <code>{manifest['chosen_stage']}</code>. "
                    f"Search budget used: {manifest['search_budget_seconds']} seconds."
                ),
                "table": final_summary.select(
                    "thp_mode",
                    "load_mean_s",
                    "load_std_s",
                    "run_mean_s",
                    "run_std_s",
                    "load_plus_run_mean_s",
                    "load_plus_run_std_s",
                ),
            },
            {
                "title": "Run vs Load+Run",
                "description": (
                    "This is the main comparison requested for the THP modes "
                    "always, madvise, and never."
                ),
                "image_path": PART_A_PNG_PATH,
                "bullets": manifest["observations"],
            },
            {
                "title": "Load Cost and Footprint",
                "description": (
                    "The first-load RDB size and Redis used-memory snapshot "
                    "show how large the 4096-record baseline really is."
                ),
                "image_path": PART_A_SUPPLEMENTAL_PNG_PATH,
                "table": final_summary.select(
                    "thp_mode",
                    "first_load_dump_bytes_mean",
                    "first_load_used_memory_bytes_mean",
                ),
            },
            {
                "title": "Search History",
                "description": "Every calibration and candidate evaluation recorded by the one-off runner.",
                "table": pl.DataFrame(manifest["attempts"]),
            },
        ],
        output_path=PART_A_HTML_PATH,
    )

    write_markdown(
        PART_A_REPORT_PATH,
        "\n".join(
            [
                "# Part A Summary",
                "",
                f"- started: `{manifest['started_at_utc']}`",
                f"- finished: `{manifest['finished_at_utc']}`",
                f"- chosen stage: `{manifest['chosen_stage']}`",
                f"- chosen config: `{manifest['chosen_config_name']}`",
                f"- clear THP effect found: `{manifest['clear_thp_effect_found']}`",
                "",
                "## Observations",
                "",
                *[f"- {item}" for item in manifest["observations"]],
                "",
            ]
        ),
    )


def run_part_a() -> dict[str, Any]:
    """Run part A and render its summary artifacts."""

    ensure_experiment_directories()
    started_at_utc = now_text()
    system_snapshot = snapshot_system()
    search_started = time.monotonic()
    all_summaries: list[pl.DataFrame] = []
    all_cycles: list[pl.DataFrame] = []
    attempts: list[dict[str, Any]] = []
    accepted_calibrations: dict[str, RawBenchmarkConfig] = {}
    try:
        screenshot_df, screenshot_cycles = _run_config_repeats(
            stage="screenshot_exact",
            configs=[_screenshot_config(thp_mode=mode) for mode in ("always", "madvise", "never")],
            repeats=3,
        )
        all_summaries.append(screenshot_df)
        all_cycles.append(screenshot_cycles)

        always_baseline = screenshot_df.filter(pl.col("thp_mode") == "always")
        baseline_dump_bytes = int(always_baseline["first_load_dump_bytes"][0])
        baseline_used_memory_bytes = int(always_baseline["first_load_used_memory_bytes"][0])

        chosen_stage = "screenshot_exact"
        chosen_config_name = "screenshot_exact"
        clear_found = _clear_thp_effect(_mode_summary(screenshot_df), stage="screenshot_exact")
        chosen_capture_config = _screenshot_config(thp_mode="always")

        budget_deadline = search_started + SEARCH_BUDGET_S
        if not clear_found:
            for template in _candidate_configs():
                calibrated, calibration_attempts, calibration_df, calibration_cycles = (
                    _calibrate_candidate(
                        template=template,
                        baseline_dump_bytes=baseline_dump_bytes,
                        baseline_used_memory_bytes=baseline_used_memory_bytes,
                        budget_deadline=budget_deadline,
                    )
                )
                attempts.extend(
                    [
                        {
                            "stage": template.name,
                            **attempt,
                        }
                        for attempt in calibration_attempts
                    ]
                )
                if calibration_df.height:
                    all_summaries.append(calibration_df)
                if calibration_cycles.height:
                    all_cycles.append(calibration_cycles)
                if calibrated is None:
                    continue
                accepted_calibrations[template.name] = calibrated

                stage_name = f"candidate_eval_{template.name}"
                candidate_df, candidate_cycles = _run_config_repeats(
                    stage=stage_name,
                    configs=[
                        replace(calibrated, thp_mode=mode) for mode in ("always", "madvise", "never")
                    ],
                    repeats=3,
                )
                all_summaries.append(candidate_df)
                all_cycles.append(candidate_cycles)
                if _clear_thp_effect(_mode_summary(candidate_df), stage=stage_name):
                    clear_found = True
                    chosen_stage = stage_name
                    chosen_config_name = calibrated.name
                    chosen_capture_config = calibrated
                    confirm_stage = f"{stage_name}_confirm"
                    confirm_df, confirm_cycles = _run_config_repeats(
                        stage=confirm_stage,
                        configs=[
                            replace(calibrated, thp_mode=mode)
                            for mode in ("always", "madvise", "never")
                        ],
                        repeats=5,
                    )
                    all_summaries.append(confirm_df)
                    all_cycles.append(confirm_cycles)
                    chosen_stage = confirm_stage
                    break
                if time.monotonic() >= budget_deadline:
                    break

        recommended_capture_config = chosen_capture_config
        recommended_capture_source = "clear_effect" if clear_found else "screenshot_exact"
        if not clear_found:
            fallback_config = accepted_calibrations.get("read80_delete20")
            if fallback_config is not None:
                chosen_config_name = "read80_delete20"
                chosen_stage = "candidate_eval_read80_delete20"
                recommended_capture_config = fallback_config
                recommended_capture_source = "fallback_read80_delete20"
            else:
                chosen_config_name = "screenshot_exact"
                chosen_stage = "screenshot_exact"
                recommended_capture_config = RawBenchmarkConfig(
                    name="read80_delete20",
                    thp_mode="always",
                    record_count=4096,
                    operation_count=8192,
                    outer_repeat=1,
                    read_proportion=0.80,
                    delete_proportion=0.20,
                )
                recommended_capture_source = "static_read80_delete20_fallback"

        summary_df = pl.concat(all_summaries) if all_summaries else pl.DataFrame([])
        cycle_df = pl.concat(all_cycles) if all_cycles else pl.DataFrame([])
        write_dataframe(PART_A_SUMMARY_PATH, summary_df)
        write_dataframe(PART_A_CYCLE_PATH, cycle_df)

        chosen_mode_summary = _mode_summary(summary_df).filter(pl.col("stage") == chosen_stage)
        observations = []
        if chosen_mode_summary.height:
            always_row = chosen_mode_summary.filter(pl.col("thp_mode") == "always")
            never_row = chosen_mode_summary.filter(pl.col("thp_mode") == "never")
            if always_row.height == 1 and never_row.height == 1:
                delta_pct = (
                    (float(never_row["run_mean_s"][0]) - float(always_row["run_mean_s"][0]))
                    / float(never_row["run_mean_s"][0])
                    * 100.0
                )
                observations.append(
                    f"Always versus never run-time delta: {delta_pct:.2f}%."
                )
            screenshot_dump_gib = baseline_dump_bytes / float(1024**3)
            observations.append(
                f"Measured 4096-record first-load dump size: {screenshot_dump_gib:.2f} GiB."
            )
            observations.append(
                f"Measured 4096-record first-load Redis used memory: "
                f"{baseline_used_memory_bytes / float(1024**3):.2f} GiB."
            )
            if not clear_found:
                observations.append(
                    "No candidate achieved the clear THP-effect threshold inside the 1.5-hour search budget."
                )

        finished_at_utc = now_text()
        manifest = {
            "part": "A",
            "started_at_utc": started_at_utc,
            "finished_at_utc": finished_at_utc,
            "search_budget_seconds": SEARCH_BUDGET_S,
            "clear_thp_effect_found": clear_found,
            "chosen_stage": chosen_stage,
            "chosen_config_name": chosen_config_name,
            "chosen_capture_config": chosen_capture_config.manifest_dict(),
            "recommended_capture_config": recommended_capture_config.manifest_dict(),
            "recommended_capture_source": recommended_capture_source,
            "observations": observations,
            "attempts": attempts,
            "artifacts": {
                "summary_parquet": str(PART_A_SUMMARY_PATH),
                "cycle_parquet": str(PART_A_CYCLE_PATH),
                "runtime_png": str(PART_A_PNG_PATH),
                "supplemental_png": str(PART_A_SUPPLEMENTAL_PNG_PATH),
                "html": str(PART_A_HTML_PATH),
                "markdown": str(PART_A_REPORT_PATH),
            },
        }
        write_json(PART_A_MANIFEST_PATH, manifest)
        _render_part_a_artifacts(summary_df=summary_df, manifest=manifest)
        return manifest
    finally:
        stop_repo_redis()
        restore_system(system_snapshot)


def main() -> None:
    """Run part A end to end."""

    manifest = run_part_a()
    print(pl.DataFrame([manifest]).select("chosen_stage", "chosen_config_name"))


if __name__ == "__main__":
    main()
