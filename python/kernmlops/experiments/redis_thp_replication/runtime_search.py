"""Overnight Redis THP runtime search with bounded per-mode runs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _PACKAGE_ROOT = Path(__file__).resolve().parents[2]
    _PACKAGE_ROOT_STR = str(_PACKAGE_ROOT)
    if _PACKAGE_ROOT_STR not in sys.path:
        sys.path.insert(0, _PACKAGE_ROOT_STR)

import polars as pl

from experiments.redis_thp_replication.common import (
    EXPERIMENT_DIR,
    TEMP_ANALYSIS_DIR,
    THP_ENABLED_PATH,
    KHUGEPAGED_SLEEP_PATH,
    OVERCOMMIT_PATH,
    RawBenchmarkConfig,
    apply_benchmark_system_config,
    build_ycsb_command,
    container_available_memory_bytes,
    ensure_experiment_directories,
    read_process_affinity,
    redis_memory_purge,
    restore_system,
    snapshot_system,
    start_repo_redis,
    stop_repo_redis,
    write_dataframe,
    write_json,
    write_markdown,
)
from experiments.redis_thp_replication.helper_pressure import PAGE_SIZE
from experiments.redis_thp_replication.part_a import parse_ycsb_overall_runtime_ms
from experiments.redis_thp_replication.reporting import grouped_bar_png, write_html_report


REPO_ROOT = Path(__file__).resolve().parents[4]
RUNTIME_SEARCH_PARENT_DIR = EXPERIMENT_DIR / "runtime_search"
LATEST_STATUS_PATH = TEMP_ANALYSIS_DIR / "redis_runtime_search_latest_status.json"
LATEST_MANIFEST_PATH = TEMP_ANALYSIS_DIR / "redis_runtime_search_latest_manifest.json"
LATEST_HTML_PATH = TEMP_ANALYSIS_DIR / "redis_runtime_search_latest.html"
LATEST_MARKDOWN_PATH = TEMP_ANALYSIS_DIR / "redis_runtime_search_latest.md"
LATEST_RUNTIME_PNG_PATH = TEMP_ANALYSIS_DIR / "redis_runtime_search_best_runtime.png"
LATEST_DELTA_PNG_PATH = TEMP_ANALYSIS_DIR / "redis_runtime_search_delta_timeline.png"
LATEST_MONITOR_LOG_PATH = TEMP_ANALYSIS_DIR / "redis_runtime_search_monitor.log"
LATEST_COMMANDS_PATH = TEMP_ANALYSIS_DIR / "redis_runtime_search_commands.txt"
LATEST_CONTAINER_PATH = TEMP_ANALYSIS_DIR / "redis_runtime_search_container.txt"
RUNNER_STATUS_PATH = TEMP_ANALYSIS_DIR / "redis_runtime_search_runner.status"

SEARCH_SCHEMA_VERSION = 1
RUN_TARGET_MIN_S = 8 * 60.0
RUN_TARGET_MAX_S = 12 * 60.0
RUN_TARGET_MID_S = 10 * 60.0
CALIBRATION_ATTEMPTS = 3
FINAL_CONFIRM_REPEATS = 3
SCREENING_REPEATS = 1
MAX_PROJECTED_MEMORY_RATIO = 0.60
HELPER_PRESSURE_TARGET_BYTES = 1 * 1024**3
HELPER_CHUNK_MIN_BYTES = int(1.5 * 1024**2)
HELPER_CHUNK_MAX_BYTES = int(2.5 * 1024**2)
REPORT_PATH = REPO_ROOT / "reports" / "collector" / "redis_runtime_search_2026-04-12.md"
DROP_CACHES_PATH = Path("/proc/sys/vm/drop_caches")


@dataclass(frozen=True)
class HelperPressureSpec:
    """One bounded external-memory-pressure setup."""

    name: str
    duration_seconds: int
    target_bytes: int
    chunk_min_bytes: int
    chunk_max_bytes: int

    def manifest_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation of the helper spec."""

        return asdict(self)


@dataclass(frozen=True)
class RuntimeSearchCandidate:
    """One candidate measured workload plus optional setup steps.

    Attributes:
        name: Stable candidate identifier used in paths and manifests.
        stage: Search stage name such as ``stage1_seed`` or ``stage2_warmup``.
        measured: The measured Redis+YCSB workload.
        warmup: Optional pre-measurement fragmentation workload.
        helper_pressure: Optional external pressure that runs alongside warmup
            and the measured run.
        notes: Short plain-English notes copied into reports.
    """

    name: str
    stage: str
    measured: RawBenchmarkConfig
    warmup: RawBenchmarkConfig | None = None
    helper_pressure: HelperPressureSpec | None = None
    notes: tuple[str, ...] = ()

    def measured_for_mode(self, thp_mode: str) -> RawBenchmarkConfig:
        """Return the measured config with the requested THP mode."""

        return replace(self.measured, thp_mode=thp_mode)

    def warmup_for_mode(self, thp_mode: str) -> RawBenchmarkConfig | None:
        """Return the warmup config with the requested THP mode."""

        if self.warmup is None:
            return None
        return replace(self.warmup, thp_mode=thp_mode)

    def manifest_dict(self) -> dict[str, Any]:
        """Return a JSON-safe candidate description."""

        return {
            "name": self.name,
            "stage": self.stage,
            "measured": self.measured.manifest_dict(),
            "warmup": None if self.warmup is None else self.warmup.manifest_dict(),
            "helper_pressure": (
                None
                if self.helper_pressure is None
                else self.helper_pressure.manifest_dict()
            ),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class SearchSettings:
    """User-facing runner settings."""

    container_name: str
    branch_name: str
    total_budget_hours: float
    stage1_hours: float
    max_mode_runtime_minutes: float
    resume_manifest: str | None = None
    no_execute: bool = False

    @property
    def max_mode_runtime_seconds(self) -> float:
        return self.max_mode_runtime_minutes * 60.0

    def manifest_dict(self) -> dict[str, Any]:
        """Return a JSON-safe settings mapping."""

        return {
            "container_name": self.container_name,
            "branch_name": self.branch_name,
            "total_budget_hours": self.total_budget_hours,
            "stage1_hours": self.stage1_hours,
            "max_mode_runtime_minutes": self.max_mode_runtime_minutes,
            "resume_manifest": self.resume_manifest,
            "no_execute": self.no_execute,
        }


def _utc_now_text() -> str:
    """Return an ISO-8601 UTC timestamp with second resolution."""

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _status_row(
    *,
    state: str,
    candidate: RuntimeSearchCandidate | None,
    stage: str,
    artifact_root: Path,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one status payload for the stable latest-status file."""

    payload: dict[str, Any] = {
        "timestamp_utc": _utc_now_text(),
        "state": state,
        "stage": stage,
        "artifact_root": str(artifact_root),
        "candidate_name": None if candidate is None else candidate.name,
        "candidate_stage": None if candidate is None else candidate.stage,
    }
    if extra:
        payload.update(extra)
    return payload


def _write_status(
    *,
    state: str,
    candidate: RuntimeSearchCandidate | None,
    stage: str,
    artifact_root: Path,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write the current runtime-search status to the stable latest path."""

    write_json(
        LATEST_STATUS_PATH,
        _status_row(
            state=state,
            candidate=candidate,
            stage=stage,
            artifact_root=artifact_root,
            extra=extra,
        ),
    )


def _mode_std(values: list[float]) -> float:
    """Return a sample stddev, or zero when there is only one sample."""

    if len(values) < 2:
        return 0.0
    mean_value = sum(values) / float(len(values))
    variance = sum((value - mean_value) ** 2 for value in values) / float(len(values) - 1)
    return variance**0.5


def _mix_sum(config: RawBenchmarkConfig) -> float:
    """Return the total YCSB operation proportion for one config."""

    return (
        config.read_proportion
        + config.delete_proportion
        + config.insert_proportion
        + config.update_proportion
        + config.scan_proportion
        + config.readmodifywrite_proportion
    )


def _assert_valid_runtime_config(config: RawBenchmarkConfig) -> None:
    """Fail fast when a measured runtime config breaks the search constraints."""

    if config.delete_proportion < 0.05:
        raise RuntimeError(
            f"{config.name} must keep delete proportion >= 0.05, found {config.delete_proportion:.2f}"
        )
    total = _mix_sum(config)
    if abs(total - 1.0) > 1e-9:
        raise RuntimeError(f"{config.name} proportions must sum to 1.0, found {total:.4f}")
    if config.outer_repeat != 1:
        raise RuntimeError(f"{config.name} must keep outer_repeat=1, found {config.outer_repeat}")
    if config.field_length != 2_097_152:
        raise RuntimeError(f"{config.name} must keep field_length=2097152")


def _assert_valid_candidate(candidate: RuntimeSearchCandidate) -> None:
    """Validate the measured and warmup configs for one candidate."""

    _assert_valid_runtime_config(candidate.measured)
    if candidate.warmup is not None:
        total = _mix_sum(candidate.warmup)
        if abs(total - 1.0) > 1e-9:
            raise RuntimeError(
                f"{candidate.name} warmup proportions must sum to 1.0, found {total:.4f}"
            )
        if candidate.warmup.outer_repeat != 1:
            raise RuntimeError(f"{candidate.name} warmup must keep outer_repeat=1")


def _projected_memory_feasible(config: RawBenchmarkConfig) -> tuple[bool, int, int]:
    """Check whether the projected loaded value bytes fit inside a safe budget."""

    projected_value_bytes = config.record_count * config.field_count * config.field_length
    memory_limit_bytes = int(container_available_memory_bytes() * MAX_PROJECTED_MEMORY_RATIO)
    return projected_value_bytes <= memory_limit_bytes, projected_value_bytes, memory_limit_bytes


def _build_measured_config(
    *,
    name: str,
    request_distribution: str,
    field_length_distribution: str,
    min_field_length: int,
    read: float,
    update: float,
    delete: float,
    insert: float,
    readmodifywrite: float,
) -> RawBenchmarkConfig:
    """Build one stage-1 measured config template."""

    return RawBenchmarkConfig(
        name=name,
        thp_mode="always",
        record_count=4096,
        operation_count=524288,
        outer_repeat=1,
        read_proportion=read,
        update_proportion=update,
        delete_proportion=delete,
        insert_proportion=insert,
        readmodifywrite_proportion=readmodifywrite,
        request_distribution=request_distribution,
        field_length_distribution=field_length_distribution,
        min_field_length=min_field_length,
        field_length=2_097_152,
        explicit_purge=True,
        thread_count=16,
        target=10_000,
        khugepaged_scan_sleep_millis=500,
    )


def build_stage1_seed_candidates() -> list[RuntimeSearchCandidate]:
    """Return the broad stage-1 candidate family list."""

    families = [
        ("r45_u45_d5_i5", 0.45, 0.45, 0.05, 0.05, 0.0),
        ("r40_u40_d10_i10", 0.40, 0.40, 0.10, 0.10, 0.0),
        ("r45_rmw45_d5_i5", 0.45, 0.0, 0.05, 0.05, 0.45),
        ("r40_rmw40_d10_i10", 0.40, 0.0, 0.10, 0.10, 0.40),
        ("r45_u45_d10", 0.45, 0.45, 0.10, 0.0, 0.0),
        ("r40_u50_d10", 0.40, 0.50, 0.10, 0.0, 0.0),
    ]
    value_families = [
        ("const", "constant", 2_097_152),
        ("largevar", "uniform", 1_048_576),
    ]
    candidates: list[RuntimeSearchCandidate] = []
    for family_name, read, update, delete, insert, readmodifywrite in families:
        for request_distribution in ("zipfian", "uniform"):
            for value_name, field_distribution, min_field_length in value_families:
                name = f"{family_name}_{request_distribution}_{value_name}"
                measured = _build_measured_config(
                    name=name,
                    request_distribution=request_distribution,
                    field_length_distribution=field_distribution,
                    min_field_length=min_field_length,
                    read=read,
                    update=update,
                    delete=delete,
                    insert=insert,
                    readmodifywrite=readmodifywrite,
                )
                candidate = RuntimeSearchCandidate(
                    name=name,
                    stage="stage1_seed",
                    measured=measured,
                    notes=(
                        "Broad stage-1 seed candidate.",
                        f"request_distribution={request_distribution}",
                        f"value_family={value_name}",
                    ),
                )
                _assert_valid_candidate(candidate)
                candidates.append(candidate)
    return candidates


def build_stage1_neighborhood_candidates(seed: RuntimeSearchCandidate) -> list[RuntimeSearchCandidate]:
    """Return the focused delete/update neighborhood around one promising seed."""

    base = seed.measured
    delete_options = sorted({0.05, 0.10, 0.20, base.delete_proportion})
    primary_name = (
        "update_proportion"
        if base.update_proportion > 0.0
        else "readmodifywrite_proportion"
    )
    base_primary = (
        base.update_proportion
        if primary_name == "update_proportion"
        else base.readmodifywrite_proportion
    )
    primary_options = sorted(
        {
            round(max(0.05, base_primary - 0.10), 2),
            round(base_primary, 2),
            round(min(0.80, base_primary + 0.10), 2),
        }
    )
    candidates: list[RuntimeSearchCandidate] = []
    for delete_value in delete_options:
        for primary_value in primary_options:
            insert_value = base.insert_proportion
            read_value = round(1.0 - delete_value - insert_value - primary_value, 2)
            if read_value < 0.05:
                continue
            measured = replace(
                base,
                name=(
                    f"{seed.name}_nb_d{int(delete_value * 100)}_"
                    f"{'u' if primary_name == 'update_proportion' else 'rmw'}{int(primary_value * 100)}"
                ),
                read_proportion=read_value,
                delete_proportion=delete_value,
                update_proportion=primary_value if primary_name == "update_proportion" else 0.0,
                readmodifywrite_proportion=(
                    primary_value if primary_name == "readmodifywrite_proportion" else 0.0
                ),
            )
            candidate = RuntimeSearchCandidate(
                name=measured.name,
                stage="stage1_neighborhood",
                measured=measured,
                notes=(
                    f"Neighborhood of {seed.name}.",
                    f"delete={delete_value:.2f}",
                    f"{primary_name}={primary_value:.2f}",
                ),
            )
            _assert_valid_candidate(candidate)
            candidates.append(candidate)
    return candidates


def build_stage2_candidates(seeds: list[RuntimeSearchCandidate]) -> list[RuntimeSearchCandidate]:
    """Return the stage-2 warmup and helper-pressure variants."""

    candidates: list[RuntimeSearchCandidate] = []
    helper = HelperPressureSpec(
        name="bounded_helper_pressure",
        duration_seconds=20 * 60,
        target_bytes=HELPER_PRESSURE_TARGET_BYTES,
        chunk_min_bytes=HELPER_CHUNK_MIN_BYTES,
        chunk_max_bytes=HELPER_CHUNK_MAX_BYTES,
    )
    for seed in seeds:
        warmups = [
            replace(
                seed.measured,
                name=f"{seed.name}_warmup_u40_i40_d20",
                read_proportion=0.0,
                update_proportion=0.40,
                delete_proportion=0.20,
                insert_proportion=0.40,
                readmodifywrite_proportion=0.0,
                request_distribution=seed.measured.request_distribution,
                field_length_distribution="uniform",
                min_field_length=1_048_576,
                operation_count=max(262144, seed.measured.operation_count // 2),
                explicit_purge=False,
            ),
            replace(
                seed.measured,
                name=f"{seed.name}_warmup_r10_rmw35_i35_d20",
                read_proportion=0.10,
                update_proportion=0.0,
                delete_proportion=0.20,
                insert_proportion=0.35,
                readmodifywrite_proportion=0.35,
                request_distribution=seed.measured.request_distribution,
                field_length_distribution="uniform",
                min_field_length=1_048_576,
                operation_count=max(262144, seed.measured.operation_count // 2),
                explicit_purge=False,
            ),
        ]
        for warmup in warmups:
            warmup_only = RuntimeSearchCandidate(
                name=f"{seed.name}__{warmup.name}",
                stage="stage2_warmup",
                measured=seed.measured,
                warmup=warmup,
                notes=("Stage-2 warmup candidate.",),
            )
            _assert_valid_candidate(warmup_only)
            candidates.append(warmup_only)
            helper_variant = RuntimeSearchCandidate(
                name=f"{seed.name}__{warmup.name}__helper",
                stage="stage2_helper",
                measured=seed.measured,
                warmup=warmup,
                helper_pressure=helper,
                notes=("Stage-2 helper-pressure candidate.",),
            )
            _assert_valid_candidate(helper_variant)
            candidates.append(helper_variant)
    return candidates


def is_clear_thp_effect(
    *,
    always_mean_s: float,
    never_mean_s: float,
    always_std_s: float,
    never_std_s: float,
    threshold_pct: float = 0.05,
) -> bool:
    """Return true when always is clearly faster than never."""

    if never_mean_s <= 0.0:
        return False
    delta = never_mean_s - always_mean_s
    pct = delta / never_mean_s
    return pct >= threshold_pct and delta > max(always_std_s, never_std_s)


def rank_attempt_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort candidate summaries by delta strength and cost."""

    return sorted(
        rows,
        key=lambda row: (
            -float(row.get("delta_pct", -999.0)),
            -float(row.get("delta_s", -999.0)),
            float(row.get("combined_std_s", 999.0)),
            float(row.get("eval_total_mean_s", 999999.0)),
        ),
    )


def should_pivot_stage(
    *,
    started_epoch_s: float,
    now_epoch_s: float,
    stage1_hours: float,
    current_stage: str,
) -> bool:
    """Return true when the run should pivot away from stage 1."""

    if current_stage.startswith("stage2"):
        return False
    return now_epoch_s - started_epoch_s >= stage1_hours * 3600.0


def _stage1_budget_exhausted(
    *,
    manifest: dict[str, Any],
    settings: SearchSettings,
) -> bool:
    """Return true when stage 1 should stop launching new work."""

    return should_pivot_stage(
        started_epoch_s=float(manifest["started_epoch_s"]),
        now_epoch_s=time.time(),
        stage1_hours=settings.stage1_hours,
        current_stage=str(manifest["current_stage"]),
    )


def _safe_name(name: str) -> str:
    """Return a filesystem-safe candidate slug."""

    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name)


def _remaining_seconds(deadline: float) -> float:
    """Return non-negative remaining seconds until one deadline."""

    return max(0.0, deadline - time.monotonic())


def _parse_last_runtime_s(log_path: Path) -> float:
    """Return the final YCSB overall runtime from one phase log."""

    runtimes_ms = parse_ycsb_overall_runtime_ms(log_path)
    if not runtimes_ms:
        raise RuntimeError(f"No YCSB [OVERALL] runtime found in {log_path}")
    return float(runtimes_ms[-1]) / 1000.0


def _parse_last_runtime_s_or_none(log_path: Path) -> float | None:
    """Return the final YCSB overall runtime when present.

    A missing ``[OVERALL], RunTime(ms)`` line means the YCSB process did not
    finish cleanly enough to emit its summary, even if the wrapper exited 0.
    The runtime search should reject that candidate mode instead of crashing.
    """

    runtimes_ms = parse_ycsb_overall_runtime_ms(log_path)
    if not runtimes_ms:
        return None
    return float(runtimes_ms[-1]) / 1000.0


def _timed_run_command_with_timeout(
    cmd: list[str],
    *,
    log_path: Path,
    timeout_seconds: float,
    env: dict[str, str] | None = None,
) -> tuple[float, bool]:
    """Run one command with a hard timeout.

    Returns:
        ``(elapsed_seconds, timed_out)``.

    Raises:
        RuntimeError: If the command exits non-zero without timing out.
    """

    merged_env = os.environ.copy()
    if env is not None:
        merged_env.update(env)
    if log_path.exists():
        log_path.unlink()
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write("$ " + " ".join(cmd) + "\n\n")
        process = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            env=merged_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout_seconds)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            log_file.write(
                f"\nTimed out after {timeout_seconds:.2f}s and terminated the process group.\n"
            )
            returncode = process.returncode
    elapsed = time.perf_counter() - started
    if timed_out:
        return elapsed, True
    if returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {returncode}: {' '.join(cmd)} (see {log_path})"
        )
    return elapsed, False


def _preflight_runtime_environment() -> None:
    """Fail fast when the runtime search lacks writable THP tuning paths.

    Raises:
        RuntimeError: If the current environment cannot write the sysfs/procfs
            knobs required by the raw Redis runtime experiments.
    """

    required_paths = (
        THP_ENABLED_PATH,
        KHUGEPAGED_SLEEP_PATH,
        OVERCOMMIT_PATH,
        DROP_CACHES_PATH,
    )
    failures = [
        str(path)
        for path in required_paths
        if not path.exists() or not os.access(path, os.W_OK)
    ]
    if failures:
        raise RuntimeError(
            "Redis runtime search needs writable THP/procfs knobs. "
            "Missing or read-only paths: "
            + ", ".join(failures)
            + ". Use the privileged benchmark container or another environment "
            "where these files are writable."
        )


def _helper_cpu() -> int | None:
    """Pick one CPU for helper pressure without stealing the whole machine."""

    if not hasattr(os, "sched_getaffinity"):
        return None
    cpus = sorted(os.sched_getaffinity(0))
    return cpus[-1] if cpus else None


def _start_helper_pressure(
    *,
    spec: HelperPressureSpec,
    run_dir: Path,
) -> subprocess.Popen[str]:
    """Start the bounded helper-pressure process for one candidate mode run."""

    helper_log_path = run_dir / "helper_pressure.log"
    cmd = [
        sys.executable,
        str(Path(__file__).with_name("helper_pressure.py")),
        "--duration-seconds",
        str(spec.duration_seconds),
        "--target-bytes",
        str(spec.target_bytes),
        "--chunk-min-bytes",
        str(spec.chunk_min_bytes),
        "--chunk-max-bytes",
        str(spec.chunk_max_bytes),
        "--seed",
        "0",
        "--log-path",
        str(helper_log_path),
    ]
    helper_cpu = _helper_cpu()
    if helper_cpu is not None:
        cmd.extend(["--cpu", str(helper_cpu)])
    return subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )


def _stop_process(process: subprocess.Popen[Any] | None) -> None:
    """Terminate one background helper process if it is still alive."""

    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def _mode_row(
    *,
    candidate: RuntimeSearchCandidate,
    mode: str,
    repeat_index: int,
    stage: str,
    artifact_root: Path,
) -> dict[str, Any]:
    """Return a partially populated per-mode result row."""

    return {
        "timestamp_utc": _utc_now_text(),
        "candidate_name": candidate.name,
        "candidate_stage": candidate.stage,
        "execution_stage": stage,
        "thp_mode": mode,
        "repeat_index": repeat_index,
        "artifact_root": str(artifact_root),
        "request_distribution": candidate.measured.request_distribution,
        "field_length_distribution": candidate.measured.field_length_distribution,
        "min_field_length": candidate.measured.min_field_length,
        "record_count": candidate.measured.record_count,
        "operation_count": candidate.measured.operation_count,
        "read_proportion": candidate.measured.read_proportion,
        "update_proportion": candidate.measured.update_proportion,
        "delete_proportion": candidate.measured.delete_proportion,
        "insert_proportion": candidate.measured.insert_proportion,
        "readmodifywrite_proportion": candidate.measured.readmodifywrite_proportion,
        "helper_pressure": candidate.helper_pressure.name if candidate.helper_pressure else "",
        "warmup_name": "" if candidate.warmup is None else candidate.warmup.name,
    }


def _execute_candidate_mode(
    *,
    candidate: RuntimeSearchCandidate,
    mode: str,
    repeat_index: int,
    artifact_root: Path,
    max_mode_runtime_seconds: float,
) -> dict[str, Any]:
    """Run one candidate for one THP mode and return the measured metrics."""

    measured = candidate.measured_for_mode(mode)
    feasible, projected_bytes, memory_limit = _projected_memory_feasible(measured)
    row = _mode_row(
        candidate=candidate,
        mode=mode,
        repeat_index=repeat_index,
        stage=candidate.stage,
        artifact_root=artifact_root,
    )
    row["projected_value_bytes"] = projected_bytes
    row["memory_limit_bytes"] = memory_limit
    if not feasible:
        row["status"] = "rejected_resource_limit"
        row["rejection_reason"] = "projected_resource_limit"
        return row

    run_dir = artifact_root / f"{_safe_name(candidate.stage)}__{_safe_name(candidate.name)}__{mode}_r{repeat_index}"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_status(
        state="running",
        candidate=candidate,
        stage=candidate.stage,
        artifact_root=artifact_root,
        extra={"mode": mode, "repeat_index": repeat_index},
    )

    helper_process: subprocess.Popen[str] | None = None
    apply_benchmark_system_config(
        thp_mode=mode,
        khugepaged_scan_sleep_millis=measured.khugepaged_scan_sleep_millis,
    )
    redis_pid = start_repo_redis(run_dir=run_dir)
    row["redis_affinity_before"] = read_process_affinity(redis_pid)
    time.sleep(measured.server_sleep_seconds)
    deadline = time.monotonic() + max_mode_runtime_seconds

    try:
        load_log_path = run_dir / "load.log"
        remaining = _remaining_seconds(deadline)
        if remaining <= 0.0:
            row["status"] = "timed_out"
            row["rejection_reason"] = "mode_deadline_before_load"
            return row
        load_wall_s, load_timed_out = _timed_run_command_with_timeout(
            build_ycsb_command(measured, phase="load", cycle_index=0),
            log_path=load_log_path,
            timeout_seconds=remaining,
        )
        row["load_wall_s"] = load_wall_s
        if load_timed_out:
            row["status"] = "timed_out"
            row["rejection_reason"] = "load_timeout"
            return row
        load_ycsb_s = _parse_last_runtime_s_or_none(load_log_path)
        if load_ycsb_s is None:
            row["status"] = "rejected"
            row["rejection_reason"] = "load_missing_overall_runtime"
            return row
        row["load_ycsb_s"] = load_ycsb_s
        if measured.explicit_purge:
            redis_memory_purge()

        if candidate.helper_pressure is not None:
            helper_process = _start_helper_pressure(spec=candidate.helper_pressure, run_dir=run_dir)
            row["helper_page_size"] = PAGE_SIZE

        warmup_config = candidate.warmup_for_mode(mode)
        if warmup_config is not None:
            warmup_log_path = run_dir / "warmup.log"
            remaining = _remaining_seconds(deadline)
            if remaining <= 0.0:
                row["status"] = "timed_out"
                row["rejection_reason"] = "mode_deadline_before_warmup"
                return row
            warmup_wall_s, warmup_timed_out = _timed_run_command_with_timeout(
                build_ycsb_command(warmup_config, phase="run", cycle_index=0),
                log_path=warmup_log_path,
                timeout_seconds=remaining,
            )
            row["warmup_wall_s"] = warmup_wall_s
            if warmup_timed_out:
                row["status"] = "timed_out"
                row["rejection_reason"] = "warmup_timeout"
                return row
            warmup_ycsb_s = _parse_last_runtime_s_or_none(warmup_log_path)
            if warmup_ycsb_s is None:
                row["status"] = "rejected"
                row["rejection_reason"] = "warmup_missing_overall_runtime"
                return row
            row["warmup_ycsb_s"] = warmup_ycsb_s

        run_log_path = run_dir / "run.log"
        remaining = _remaining_seconds(deadline)
        if remaining <= 0.0:
            row["status"] = "timed_out"
            row["rejection_reason"] = "mode_deadline_before_run"
            return row
        run_wall_s, run_timed_out = _timed_run_command_with_timeout(
            build_ycsb_command(measured, phase="run", cycle_index=0),
            log_path=run_log_path,
            timeout_seconds=remaining,
        )
        row["run_wall_s"] = run_wall_s
        if run_timed_out:
            row["status"] = "timed_out"
            row["rejection_reason"] = "run_timeout"
            return row
        run_ycsb_s = _parse_last_runtime_s_or_none(run_log_path)
        if run_ycsb_s is None:
            row["status"] = "rejected"
            row["rejection_reason"] = "run_missing_overall_runtime"
            return row
        row["run_ycsb_s"] = run_ycsb_s
        row["load_plus_run_ycsb_s"] = row["load_ycsb_s"] + row["run_ycsb_s"]
        row["load_plus_run_wall_s"] = row["load_wall_s"] + row["run_wall_s"]
        row["status"] = "completed"
        return row
    finally:
        _stop_process(helper_process)
        stop_repo_redis()


def _calibration_attempt_row(
    *,
    candidate_name: str,
    stage: str,
    attempt_index: int,
    operation_count: int,
    record_count: int,
    run_ycsb_s: float | None,
    status: str,
    rejection_reason: str,
) -> dict[str, Any]:
    """Build one calibration-attempt summary row."""

    return {
        "timestamp_utc": _utc_now_text(),
        "candidate_name": candidate_name,
        "candidate_stage": stage,
        "attempt_index": attempt_index,
        "record_count": record_count,
        "operation_count": operation_count,
        "run_ycsb_s": run_ycsb_s,
        "status": status,
        "rejection_reason": rejection_reason,
    }


def calibrate_candidate(
    *,
    candidate: RuntimeSearchCandidate,
    artifact_root: Path,
    settings: SearchSettings,
) -> tuple[RuntimeSearchCandidate | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """Tune operation count until the always-mode run lands near 10 minutes."""

    attempts: list[dict[str, Any]] = []
    mode_rows: list[dict[str, Any]] = []
    current = candidate
    for attempt_index in range(1, CALIBRATION_ATTEMPTS + 1):
        result = _execute_candidate_mode(
            candidate=current,
            mode="always",
            repeat_index=attempt_index,
            artifact_root=artifact_root,
            max_mode_runtime_seconds=settings.max_mode_runtime_seconds,
        )
        mode_rows.append(result)
        run_ycsb_s = float(result.get("run_ycsb_s", 0.0)) if result.get("run_ycsb_s") else None
        attempts.append(
            _calibration_attempt_row(
                candidate_name=current.name,
                stage=current.stage,
                attempt_index=attempt_index,
                operation_count=current.measured.operation_count,
                record_count=current.measured.record_count,
                run_ycsb_s=run_ycsb_s,
                status=str(result.get("status", "unknown")),
                rejection_reason=str(result.get("rejection_reason", "")),
            )
        )
        if result.get("status") != "completed" or run_ycsb_s is None:
            return None, attempts, mode_rows
        if RUN_TARGET_MIN_S <= run_ycsb_s <= RUN_TARGET_MAX_S:
            return current, attempts, mode_rows

        scale = RUN_TARGET_MID_S / run_ycsb_s
        new_operation_count = max(1, int(round(current.measured.operation_count * scale)))
        if new_operation_count == current.measured.operation_count:
            if run_ycsb_s < RUN_TARGET_MIN_S:
                new_operation_count += max(1, current.measured.operation_count // 10)
            else:
                new_operation_count = max(1, current.measured.operation_count - max(1, current.measured.operation_count // 10))
        current = replace(
            current,
            measured=replace(current.measured, operation_count=new_operation_count),
        )
    return None, attempts, mode_rows


def _summarize_modes(
    *,
    candidate: RuntimeSearchCandidate,
    execution_stage: str,
    mode_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate repeated mode results into one candidate summary row."""

    always_rows = [row for row in mode_rows if row["thp_mode"] == "always" and row["status"] == "completed"]
    never_rows = [row for row in mode_rows if row["thp_mode"] == "never" and row["status"] == "completed"]
    always_runs = [float(row["run_ycsb_s"]) for row in always_rows]
    never_runs = [float(row["run_ycsb_s"]) for row in never_rows]
    always_loads = [float(row["load_ycsb_s"]) for row in always_rows]
    never_loads = [float(row["load_ycsb_s"]) for row in never_rows]
    always_totals = [float(row["load_plus_run_ycsb_s"]) for row in always_rows]
    never_totals = [float(row["load_plus_run_ycsb_s"]) for row in never_rows]
    always_mean = sum(always_runs) / float(len(always_runs)) if always_runs else 0.0
    never_mean = sum(never_runs) / float(len(never_runs)) if never_runs else 0.0
    always_std = _mode_std(always_runs)
    never_std = _mode_std(never_runs)
    delta_s = never_mean - always_mean
    delta_pct = delta_s / never_mean if never_mean else 0.0
    combined_std_s = max(always_std, never_std)
    success = is_clear_thp_effect(
        always_mean_s=always_mean,
        never_mean_s=never_mean,
        always_std_s=always_std,
        never_std_s=never_std,
    )
    return {
        "timestamp_utc": _utc_now_text(),
        "candidate_name": candidate.name,
        "candidate_stage": candidate.stage,
        "execution_stage": execution_stage,
        "screening_repeats": len(always_rows),
        "record_count": candidate.measured.record_count,
        "operation_count": candidate.measured.operation_count,
        "request_distribution": candidate.measured.request_distribution,
        "field_length_distribution": candidate.measured.field_length_distribution,
        "min_field_length": candidate.measured.min_field_length,
        "read_proportion": candidate.measured.read_proportion,
        "update_proportion": candidate.measured.update_proportion,
        "delete_proportion": candidate.measured.delete_proportion,
        "insert_proportion": candidate.measured.insert_proportion,
        "readmodifywrite_proportion": candidate.measured.readmodifywrite_proportion,
        "warmup_name": "" if candidate.warmup is None else candidate.warmup.name,
        "helper_pressure": "" if candidate.helper_pressure is None else candidate.helper_pressure.name,
        "always_run_mean_s": always_mean,
        "always_run_std_s": always_std,
        "never_run_mean_s": never_mean,
        "never_run_std_s": never_std,
        "always_load_mean_s": sum(always_loads) / float(len(always_loads)) if always_loads else 0.0,
        "never_load_mean_s": sum(never_loads) / float(len(never_loads)) if never_loads else 0.0,
        "always_total_mean_s": sum(always_totals) / float(len(always_totals)) if always_totals else 0.0,
        "never_total_mean_s": sum(never_totals) / float(len(never_totals)) if never_totals else 0.0,
        "delta_s": delta_s,
        "delta_pct": delta_pct,
        "combined_std_s": combined_std_s,
        "eval_total_mean_s": (
            (
                (sum(always_totals) / float(len(always_totals)) if always_totals else 0.0)
                + (sum(never_totals) / float(len(never_totals)) if never_totals else 0.0)
            )
            / 2.0
        ),
        "passed_threshold": success,
        "status": "completed" if always_rows and never_rows else "incomplete",
        "notes": "; ".join(candidate.notes),
    }


def evaluate_candidate(
    *,
    candidate: RuntimeSearchCandidate,
    artifact_root: Path,
    settings: SearchSettings,
    repeats: int,
    modes: tuple[str, ...],
    execution_stage: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run one candidate for the requested modes and aggregate the results."""

    mode_rows: list[dict[str, Any]] = []
    for repeat_index in range(1, repeats + 1):
        for mode in modes:
            row = _execute_candidate_mode(
                candidate=candidate,
                mode=mode,
                repeat_index=repeat_index,
                artifact_root=artifact_root,
                max_mode_runtime_seconds=settings.max_mode_runtime_seconds,
            )
            mode_rows.append(row)
            if row.get("status") != "completed":
                summary = _summarize_modes(
                    candidate=candidate,
                    execution_stage=execution_stage,
                    mode_rows=mode_rows,
                )
                summary["status"] = "rejected"
                summary["rejection_reason"] = row.get("rejection_reason", "mode_failed")
                return summary, mode_rows
    summary = _summarize_modes(
        candidate=candidate,
        execution_stage=execution_stage,
        mode_rows=mode_rows,
    )
    return summary, mode_rows


def _completed_candidate_keys(manifest: dict[str, Any]) -> set[tuple[str, str]]:
    """Return the set of already completed ``(stage, name)`` candidate keys."""

    completed = set[tuple[str, str]]()
    for row in manifest.get("attempt_summaries", []):
        if row.get("status") in {"completed", "confirmed_success", "rejected"}:
            completed.add((str(row["candidate_stage"]), str(row["candidate_name"])))
    return completed


def _top_stage_candidates(
    summaries: list[dict[str, Any]],
    *,
    stages: set[str],
    top_n: int,
) -> list[dict[str, Any]]:
    """Return the top candidate summaries for the requested stages."""

    filtered = [row for row in summaries if row.get("candidate_stage") in stages and row.get("status") != "rejected"]
    return rank_attempt_summaries(filtered)[:top_n]


def _update_latest_artifacts(
    *,
    manifest_path: Path,
    html_path: Path,
    markdown_path: Path,
    runtime_png_path: Path,
    delta_png_path: Path,
) -> None:
    """Copy the current run outputs into the stable latest paths."""

    shutil.copyfile(manifest_path, LATEST_MANIFEST_PATH)
    shutil.copyfile(html_path, LATEST_HTML_PATH)
    shutil.copyfile(markdown_path, LATEST_MARKDOWN_PATH)
    shutil.copyfile(runtime_png_path, LATEST_RUNTIME_PNG_PATH)
    shutil.copyfile(delta_png_path, LATEST_DELTA_PNG_PATH)


def _monitor_entries() -> list[str]:
    """Return human-readable monitor timestamps when the monitor log exists."""

    if not LATEST_MONITOR_LOG_PATH.exists():
        return []
    entries: list[str] = []
    for line in LATEST_MONITOR_LOG_PATH.read_text(encoding="utf-8").splitlines():
        if "status=" in line:
            entries.append(line)
    return entries


def _runner_commands() -> list[str]:
    """Return the exact runner commands when the command file exists."""

    if not LATEST_COMMANDS_PATH.exists():
        return []
    return [
        line.strip()
        for line in LATEST_COMMANDS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _render_delta_timeline(
    *,
    attempt_summaries_df: pl.DataFrame,
    output_path: Path,
) -> None:
    """Render a simple delta timeline for completed candidate attempts."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    complete = (
        attempt_summaries_df
        .filter(pl.col("status") != "rejected")
        .select("candidate_name", "delta_pct")
        .with_row_index(name="attempt_index")
    )
    fig, ax = plt.subplots(figsize=(11, 5))
    if complete.height:
        xs = complete["attempt_index"].to_list()
        ys = [value * 100.0 for value in complete["delta_pct"].to_list()]
        ax.plot(xs, ys, color="#1f77b4", linewidth=1.8)
        ax.scatter(xs, ys, color="#d62728", s=40)
        for x, name in zip(xs, complete["candidate_name"].to_list()):
            ax.annotate(name, (x, ys[x]), xytext=(4, 4), textcoords="offset points", fontsize=7)
    ax.axhline(5.0, color="#2ca02c", linestyle="--", linewidth=1.2)
    ax.set_title("Redis runtime search delta timeline")
    ax.set_xlabel("Attempt index")
    ax.set_ylabel("Always faster than never (%)")
    ax.grid(True, axis="y", linestyle="--", alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def _render_outputs(
    *,
    artifact_root: Path,
    manifest: dict[str, Any],
    attempt_summaries_df: pl.DataFrame,
    mode_results_df: pl.DataFrame,
) -> tuple[Path, Path, Path]:
    """Write the PNG, HTML, and markdown summary outputs."""

    runtime_png_path = artifact_root / "best_runtime_compare.png"
    delta_png_path = artifact_root / "delta_timeline.png"
    html_path = artifact_root / "runtime_search_dashboard.html"
    markdown_path = artifact_root / "runtime_search_summary.md"

    metric_rows = [
        row
        for row in manifest["attempt_summaries"]
        if "always_run_mean_s" in row and "never_run_mean_s" in row
    ]
    best_row = None
    ranked = rank_attempt_summaries(metric_rows)
    if ranked:
        best_row = ranked[0]
    if best_row is not None:
        categories = ["always", "never"]
        means = [
            [float(best_row["always_run_mean_s"]), float(best_row["never_run_mean_s"])],
            [float(best_row["always_load_mean_s"]), float(best_row["never_load_mean_s"])],
            [float(best_row["always_total_mean_s"]), float(best_row["never_total_mean_s"])],
        ]
        stds = [
            [float(best_row["always_run_std_s"]), float(best_row["never_run_std_s"])],
            [0.0, 0.0],
            [0.0, 0.0],
        ]
        grouped_bar_png(
            categories=categories,
            series=[
                {"label": "Run", "means": means[0], "stds": stds[0], "color": "#1f77b4"},
                {"label": "Load", "means": means[1], "stds": stds[1], "color": "#2ca02c"},
                {"label": "Total", "means": means[2], "stds": stds[2], "color": "#d62728"},
            ],
            title=f"Best candidate: {best_row['candidate_name']}",
            ylabel="Seconds",
            output_path=runtime_png_path,
        )
    else:
        grouped_bar_png(
            categories=["always", "never"],
            series=[
                {"label": "Run", "means": [0.0, 0.0], "stds": [0.0, 0.0], "color": "#1f77b4"},
            ],
            title="Best candidate: none yet",
            ylabel="Seconds",
            output_path=runtime_png_path,
        )

    _render_delta_timeline(
        attempt_summaries_df=attempt_summaries_df,
        output_path=delta_png_path,
    )

    finalist_columns = [
        "candidate_name",
        "candidate_stage",
        "always_run_mean_s",
        "never_run_mean_s",
        "delta_s",
        "delta_pct",
        "status",
        "rejection_reason",
    ]
    if metric_rows:
        finalists_df = pl.DataFrame(metric_rows).sort("delta_pct", descending=True).head(10)
        for column in finalist_columns:
            if column not in finalists_df.columns:
                finalists_df = finalists_df.with_columns(pl.lit(None).alias(column))
        finalists_df = finalists_df.select(finalist_columns)
    else:
        finalists_df = pl.DataFrame(
            {column: [] for column in finalist_columns}
        )
    checkpoint_df = (
        pl.DataFrame(manifest.get("checkpoints", []))
        if manifest.get("checkpoints")
        else pl.DataFrame([])
    )
    write_html_report(
        title="Redis THP runtime search",
        subtitle=(
            "Searches for a realistic Redis+YCSB configuration where THP always "
            "beats THP never on YCSB run [OVERALL] runtime."
        ),
        sections=[
            {
                "title": "Summary",
                "description": (
                    f"Branch: <code>{manifest['branch_name']}</code>. "
                    f"Container: <code>{manifest['container_name']}</code>. "
                    f"Status: <code>{manifest['status']}</code>."
                ),
                "bullets": manifest.get("observations", []),
            },
            {
                "title": "Best candidate runtime view",
                "description": "Run, load, and total YCSB means for the current best candidate.",
                "image_path": runtime_png_path,
            },
            {
                "title": "Delta timeline",
                "description": "Each point is one completed candidate evaluation.",
                "image_path": delta_png_path,
            },
            {
                "title": "Finalists",
                "description": "Top candidate attempts ranked by run-time delta strength.",
                "table": finalists_df,
            },
            {
                "title": "Stage checkpoints",
                "description": "Recorded pivots and checkpoints, including the 4.5-hour report if reached.",
                "table": checkpoint_df,
            },
            {
                "title": "Attempt log",
                "description": "Every candidate summary recorded so far.",
                "table": attempt_summaries_df,
            },
            {
                "title": "Per-mode raw results",
                "description": "Per-repeat mode rows used to build the candidate summaries.",
                "table": mode_results_df,
            },
        ],
        output_path=html_path,
    )

    lines = [
        "# Redis THP Runtime Search",
        "",
        f"- branch: `{manifest['branch_name']}`",
        f"- container: `{manifest['container_name']}`",
        f"- started: `{manifest['started_at_utc']}`",
        f"- status: `{manifest['status']}`",
        f"- latest stage: `{manifest['current_stage']}`",
        "",
        "## Commands",
        "",
        *[f"- `{command}`" for command in _runner_commands()],
        "",
        "## Observations",
        "",
        *[f"- {item}" for item in manifest.get("observations", [])],
        "",
        "## Monitor timestamps",
        "",
        *[f"- `{line}`" for line in _monitor_entries()],
        "",
        "## Best attempts",
        "",
        finalists_df.write_csv(separator="|"),
        "",
    ]
    write_markdown(markdown_path, "\n".join(lines))
    return runtime_png_path, delta_png_path, html_path


def _write_checkpoint_report(
    *,
    artifact_root: Path,
    checkpoint_row: dict[str, Any],
    top_attempts: list[dict[str, Any]],
) -> Path:
    """Write one stage checkpoint report, such as the 4.5-hour pivot note."""

    report_path = artifact_root / f"{_safe_name(checkpoint_row['checkpoint_name'])}.md"
    lines = [
        f"# {checkpoint_row['checkpoint_name']}",
        "",
        f"- timestamp: `{checkpoint_row['timestamp_utc']}`",
        f"- stage_before: `{checkpoint_row['stage_before']}`",
        f"- stage_after: `{checkpoint_row['stage_after']}`",
        f"- reason: `{checkpoint_row['reason']}`",
        "",
        "## Top attempts so far",
        "",
    ]
    for row in top_attempts:
        lines.append(
            f"- `{row['candidate_name']}`: delta={row['delta_pct'] * 100.0:.2f}%, "
            f"always={row['always_run_mean_s']:.3f}s, never={row['never_run_mean_s']:.3f}s"
        )
    write_markdown(report_path, "\n".join(lines))
    return report_path


def _record_checkpoint(
    *,
    manifest: dict[str, Any],
    checkpoint_name: str,
    stage_before: str,
    stage_after: str,
    reason: str,
    artifact_root: Path,
) -> None:
    """Append one checkpoint row and write its companion markdown report."""

    checkpoint_row = {
        "timestamp_utc": _utc_now_text(),
        "checkpoint_name": checkpoint_name,
        "stage_before": stage_before,
        "stage_after": stage_after,
        "reason": reason,
    }
    manifest.setdefault("checkpoints", []).append(checkpoint_row)
    top_attempts = rank_attempt_summaries(manifest.get("attempt_summaries", []))[:5]
    checkpoint_report = _write_checkpoint_report(
        artifact_root=artifact_root,
        checkpoint_row=checkpoint_row,
        top_attempts=top_attempts,
    )
    checkpoint_row["report_path"] = str(checkpoint_report)


def _persist_state(
    *,
    artifact_root: Path,
    manifest: dict[str, Any],
    attempt_summaries: list[dict[str, Any]],
    calibration_attempts: list[dict[str, Any]],
    mode_results: list[dict[str, Any]],
) -> tuple[Path, Path, Path]:
    """Write the manifest, parquet tables, and latest dashboard outputs."""

    manifest_path = artifact_root / "runtime_search_manifest.json"
    attempt_summary_path = artifact_root / "attempt_summaries.parquet"
    mode_results_path = artifact_root / "mode_results.parquet"
    calibration_path = artifact_root / "calibration_attempts.parquet"
    attempt_summaries_df = pl.DataFrame(attempt_summaries) if attempt_summaries else pl.DataFrame([])
    mode_results_df = pl.DataFrame(mode_results) if mode_results else pl.DataFrame([])
    calibration_df = pl.DataFrame(calibration_attempts) if calibration_attempts else pl.DataFrame([])
    write_json(manifest_path, manifest)
    write_dataframe(attempt_summary_path, attempt_summaries_df)
    write_dataframe(mode_results_path, mode_results_df)
    write_dataframe(calibration_path, calibration_df)
    runtime_png_path, delta_png_path, html_path = _render_outputs(
        artifact_root=artifact_root,
        manifest=manifest,
        attempt_summaries_df=attempt_summaries_df,
        mode_results_df=mode_results_df,
    )
    markdown_path = artifact_root / "runtime_search_summary.md"
    _update_latest_artifacts(
        manifest_path=manifest_path,
        html_path=html_path,
        markdown_path=markdown_path,
        runtime_png_path=runtime_png_path,
        delta_png_path=delta_png_path,
    )
    write_markdown(REPORT_PATH, markdown_path.read_text(encoding="utf-8"))
    _write_status(
        state="checkpointed" if manifest.get("status") == "running" else str(manifest.get("status")),
        candidate=None,
        stage=str(manifest.get("current_stage", "unknown")),
        artifact_root=artifact_root,
    )
    return manifest_path, attempt_summary_path, mode_results_path


def _load_resume_manifest(path: Path) -> dict[str, Any]:
    """Load one previous manifest so the search can resume."""

    return json.loads(path.read_text(encoding="utf-8"))


def _initial_manifest(
    *,
    settings: SearchSettings,
    artifact_root: Path,
) -> dict[str, Any]:
    """Return the initial manifest payload for a fresh run."""

    return {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "branch_name": settings.branch_name,
        "container_name": settings.container_name,
        "started_at_utc": _utc_now_text(),
        "started_epoch_s": time.time(),
        "finished_at_utc": None,
        "status": "running",
        "current_stage": "stage1_seed",
        "artifact_root": str(artifact_root),
        "settings": settings.manifest_dict(),
        "attempt_summaries": [],
        "calibration_attempts": [],
        "mode_results": [],
        "checkpoints": [],
        "observations": [],
        "final_candidate": None,
    }


def _command_line(settings: SearchSettings) -> str:
    """Return the command line for the report when known."""

    return " ".join(
        [
            ".venv/bin/python",
            "python/kernmlops/experiments/redis_thp_replication/runtime_search.py",
            f"--container-name {settings.container_name}",
            f"--branch-name {settings.branch_name}",
            f"--total-budget-hours {settings.total_budget_hours}",
            f"--stage1-hours {settings.stage1_hours}",
            f"--max-mode-runtime-minutes {settings.max_mode_runtime_minutes}",
        ]
        + ([] if settings.resume_manifest is None else [f"--resume-manifest {settings.resume_manifest}"])
    )


def run_runtime_search(settings: SearchSettings) -> dict[str, Any]:
    """Run the full overnight Redis THP runtime search."""

    ensure_experiment_directories()
    RUNTIME_SEARCH_PARENT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    if settings.resume_manifest is not None:
        manifest = _load_resume_manifest(Path(settings.resume_manifest))
        artifact_root = Path(manifest["artifact_root"])
    else:
        artifact_root = RUNTIME_SEARCH_PARENT_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        artifact_root.mkdir(parents=True, exist_ok=True)
        manifest = _initial_manifest(settings=settings, artifact_root=artifact_root)

    if settings.no_execute:
        stage1_candidates = build_stage1_seed_candidates()
        summary = {
            "stage1_candidates": len(stage1_candidates),
            "first_three": [candidate.manifest_dict() for candidate in stage1_candidates[:3]],
        }
        print(json.dumps(summary, indent=2))
        return summary

    write_markdown(REPORT_PATH, "# Redis runtime search\n\nRun in progress.\n")
    LATEST_CONTAINER_PATH.write_text(settings.container_name + "\n", encoding="utf-8")
    LATEST_COMMANDS_PATH.write_text(_command_line(settings) + "\n", encoding="utf-8")
    _write_status(
        state="starting",
        candidate=None,
        stage=str(manifest["current_stage"]),
        artifact_root=artifact_root,
    )

    system_snapshot = None
    _preflight_runtime_environment()
    system_snapshot = snapshot_system()
    attempt_summaries = list(manifest.get("attempt_summaries", []))
    calibration_attempts = list(manifest.get("calibration_attempts", []))
    mode_results = list(manifest.get("mode_results", []))
    completed_keys = _completed_candidate_keys(manifest)
    stage1_candidates = build_stage1_seed_candidates()

    try:
        total_deadline_epoch = float(manifest["started_epoch_s"]) + settings.total_budget_hours * 3600.0
        clear_found = False

        for candidate in stage1_candidates:
            if time.time() >= total_deadline_epoch:
                break
            if _stage1_budget_exhausted(manifest=manifest, settings=settings):
                break
            if (candidate.stage, candidate.name) in completed_keys:
                continue
            calibrated, calibration_rows, calibration_mode_rows = calibrate_candidate(
                candidate=candidate,
                artifact_root=artifact_root,
                settings=settings,
            )
            calibration_attempts.extend(calibration_rows)
            mode_results.extend(calibration_mode_rows)
            if calibrated is None:
                summary = {
                    "timestamp_utc": _utc_now_text(),
                    "candidate_name": candidate.name,
                    "candidate_stage": candidate.stage,
                    "execution_stage": "calibration",
                    "status": "rejected",
                    "rejection_reason": "calibration_failed",
                    "delta_s": 0.0,
                    "delta_pct": 0.0,
                    "combined_std_s": 0.0,
                    "eval_total_mean_s": 0.0,
                }
                attempt_summaries.append(summary)
                manifest["attempt_summaries"] = attempt_summaries
                manifest["calibration_attempts"] = calibration_attempts
                manifest["mode_results"] = mode_results
                manifest["observations"] = [
                    f"Latest completed candidate: {summary['candidate_name']} ({summary['status']})."
                ]
                _persist_state(
                    artifact_root=artifact_root,
                    manifest=manifest,
                    attempt_summaries=attempt_summaries,
                    calibration_attempts=calibration_attempts,
                    mode_results=mode_results,
                )
                completed_keys.add((candidate.stage, candidate.name))
                continue

            summary, rows = evaluate_candidate(
                candidate=calibrated,
                artifact_root=artifact_root,
                settings=settings,
                repeats=SCREENING_REPEATS,
                modes=("always", "never"),
                execution_stage="screening",
            )
            mode_results.extend(rows)
            attempt_summaries.append(summary)
            manifest["attempt_summaries"] = attempt_summaries
            manifest["calibration_attempts"] = calibration_attempts
            manifest["mode_results"] = mode_results
            manifest["observations"] = [
                f"Latest completed candidate: {summary['candidate_name']} ({summary['status']})."
            ]
            _persist_state(
                artifact_root=artifact_root,
                manifest=manifest,
                attempt_summaries=attempt_summaries,
                calibration_attempts=calibration_attempts,
                mode_results=mode_results,
            )
            completed_keys.add((candidate.stage, candidate.name))

            if summary["status"] != "rejected" and summary["passed_threshold"]:
                if _stage1_budget_exhausted(manifest=manifest, settings=settings):
                    break
                confirm_summary, confirm_rows = evaluate_candidate(
                    candidate=calibrated,
                    artifact_root=artifact_root,
                    settings=settings,
                    repeats=FINAL_CONFIRM_REPEATS,
                    modes=("always", "never"),
                    execution_stage="confirm_always_never",
                )
                mode_results.extend(confirm_rows)
                confirm_summary["status"] = (
                    "confirmed_success" if confirm_summary["passed_threshold"] else "rejected"
                )
                attempt_summaries.append(confirm_summary)
                manifest["attempt_summaries"] = attempt_summaries
                manifest["mode_results"] = mode_results
                if confirm_summary["passed_threshold"]:
                    if _stage1_budget_exhausted(manifest=manifest, settings=settings):
                        break
                    madvise_summary, madvise_rows = evaluate_candidate(
                        candidate=calibrated,
                        artifact_root=artifact_root,
                        settings=settings,
                        repeats=FINAL_CONFIRM_REPEATS,
                        modes=("always", "madvise", "never"),
                        execution_stage="final_with_madvise",
                    )
                    mode_results.extend(madvise_rows)
                    madvise_summary["status"] = (
                        "confirmed_success" if madvise_summary["passed_threshold"] else "completed"
                    )
                    attempt_summaries.append(madvise_summary)
                    manifest["attempt_summaries"] = attempt_summaries
                    manifest["mode_results"] = mode_results
                    manifest["status"] = "completed"
                    manifest["finished_at_utc"] = _utc_now_text()
                    manifest["final_candidate"] = calibrated.manifest_dict()
                    manifest["current_stage"] = "finished"
                    clear_found = True
                    _persist_state(
                        artifact_root=artifact_root,
                        manifest=manifest,
                        attempt_summaries=attempt_summaries,
                        calibration_attempts=calibration_attempts,
                        mode_results=mode_results,
                    )
                    break

            if _stage1_budget_exhausted(manifest=manifest, settings=settings):
                break

        if not clear_found and manifest["status"] == "running":
            manifest["current_stage"] = "stage1_neighborhood"
            top_seed_rows = _top_stage_candidates(
                attempt_summaries,
                stages={"stage1_seed"},
                top_n=2,
            )
            neighborhood_candidates: list[RuntimeSearchCandidate] = []
            for row in top_seed_rows:
                seed = next(candidate for candidate in stage1_candidates if candidate.name == row["candidate_name"])
                neighborhood_candidates.extend(build_stage1_neighborhood_candidates(seed))

            for candidate in neighborhood_candidates:
                if time.time() >= total_deadline_epoch:
                    break
                if _stage1_budget_exhausted(manifest=manifest, settings=settings):
                    break
                if (candidate.stage, candidate.name) in completed_keys:
                    continue
                calibrated, calibration_rows, calibration_mode_rows = calibrate_candidate(
                    candidate=candidate,
                    artifact_root=artifact_root,
                    settings=settings,
                )
                calibration_attempts.extend(calibration_rows)
                mode_results.extend(calibration_mode_rows)
                if calibrated is None:
                    attempt_summaries.append(
                        {
                            "timestamp_utc": _utc_now_text(),
                            "candidate_name": candidate.name,
                            "candidate_stage": candidate.stage,
                            "execution_stage": "calibration",
                            "status": "rejected",
                            "rejection_reason": "calibration_failed",
                            "delta_s": 0.0,
                            "delta_pct": 0.0,
                            "combined_std_s": 0.0,
                            "eval_total_mean_s": 0.0,
                        }
                    )
                    completed_keys.add((candidate.stage, candidate.name))
                    continue
                summary, rows = evaluate_candidate(
                    candidate=calibrated,
                    artifact_root=artifact_root,
                    settings=settings,
                    repeats=SCREENING_REPEATS,
                    modes=("always", "never"),
                    execution_stage="neighborhood",
                )
                mode_results.extend(rows)
                attempt_summaries.append(summary)
                completed_keys.add((candidate.stage, candidate.name))
                manifest["attempt_summaries"] = attempt_summaries
                manifest["calibration_attempts"] = calibration_attempts
                manifest["mode_results"] = mode_results
                _persist_state(
                    artifact_root=artifact_root,
                    manifest=manifest,
                    attempt_summaries=attempt_summaries,
                    calibration_attempts=calibration_attempts,
                    mode_results=mode_results,
                )
                if summary["status"] != "rejected" and summary["passed_threshold"]:
                    if _stage1_budget_exhausted(manifest=manifest, settings=settings):
                        break
                    confirm_summary, confirm_rows = evaluate_candidate(
                        candidate=calibrated,
                        artifact_root=artifact_root,
                        settings=settings,
                        repeats=FINAL_CONFIRM_REPEATS,
                        modes=("always", "never"),
                        execution_stage="confirm_always_never",
                    )
                    mode_results.extend(confirm_rows)
                    confirm_summary["status"] = (
                        "confirmed_success" if confirm_summary["passed_threshold"] else "rejected"
                    )
                    attempt_summaries.append(confirm_summary)
                    manifest["attempt_summaries"] = attempt_summaries
                    manifest["mode_results"] = mode_results
                    if confirm_summary["passed_threshold"]:
                        if _stage1_budget_exhausted(manifest=manifest, settings=settings):
                            break
                        madvise_summary, madvise_rows = evaluate_candidate(
                            candidate=calibrated,
                            artifact_root=artifact_root,
                            settings=settings,
                            repeats=FINAL_CONFIRM_REPEATS,
                            modes=("always", "madvise", "never"),
                            execution_stage="final_with_madvise",
                        )
                        mode_results.extend(madvise_rows)
                        madvise_summary["status"] = (
                            "confirmed_success" if madvise_summary["passed_threshold"] else "completed"
                        )
                        attempt_summaries.append(madvise_summary)
                        manifest["attempt_summaries"] = attempt_summaries
                        manifest["mode_results"] = mode_results
                        manifest["status"] = "completed"
                        manifest["finished_at_utc"] = _utc_now_text()
                        manifest["final_candidate"] = calibrated.manifest_dict()
                        manifest["current_stage"] = "finished"
                        clear_found = True
                        _persist_state(
                            artifact_root=artifact_root,
                            manifest=manifest,
                            attempt_summaries=attempt_summaries,
                            calibration_attempts=calibration_attempts,
                            mode_results=mode_results,
                        )
                        break

        if not clear_found and manifest["status"] == "running":
            manifest["current_stage"] = "stage2_warmup"
            _record_checkpoint(
                manifest=manifest,
                checkpoint_name="4.5-hour checkpoint",
                stage_before="stage1",
                stage_after="stage2_warmup",
                reason="No candidate met the 5% always-vs-never run-time delta threshold during stage 1.",
                artifact_root=artifact_root,
            )
            _persist_state(
                artifact_root=artifact_root,
                manifest=manifest,
                attempt_summaries=attempt_summaries,
                calibration_attempts=calibration_attempts,
                mode_results=mode_results,
            )
            stage2_seed_rows = _top_stage_candidates(
                attempt_summaries,
                stages={"stage1_seed", "stage1_neighborhood"},
                top_n=2,
            )
            stage2_seed_candidates: list[RuntimeSearchCandidate] = []
            all_seeds = build_stage1_seed_candidates()
            all_neighborhood: dict[str, RuntimeSearchCandidate] = {}
            for seed in all_seeds:
                for candidate in build_stage1_neighborhood_candidates(seed):
                    all_neighborhood[candidate.name] = candidate
            for row in stage2_seed_rows:
                name = row["candidate_name"]
                seed = next((candidate for candidate in all_seeds if candidate.name == name), None)
                if seed is None:
                    seed = all_neighborhood.get(name)
                if seed is not None:
                    stage2_seed_candidates.append(seed)
            stage2_candidates = build_stage2_candidates(stage2_seed_candidates)
            for candidate in stage2_candidates:
                if time.time() >= total_deadline_epoch:
                    break
                if (candidate.stage, candidate.name) in completed_keys:
                    continue
                summary, rows = evaluate_candidate(
                    candidate=candidate,
                    artifact_root=artifact_root,
                    settings=settings,
                    repeats=SCREENING_REPEATS,
                    modes=("always", "never"),
                    execution_stage=candidate.stage,
                )
                mode_results.extend(rows)
                attempt_summaries.append(summary)
                manifest["attempt_summaries"] = attempt_summaries
                manifest["mode_results"] = mode_results
                _persist_state(
                    artifact_root=artifact_root,
                    manifest=manifest,
                    attempt_summaries=attempt_summaries,
                    calibration_attempts=calibration_attempts,
                    mode_results=mode_results,
                )
                completed_keys.add((candidate.stage, candidate.name))
                if summary["status"] != "rejected" and summary["passed_threshold"]:
                    confirm_summary, confirm_rows = evaluate_candidate(
                        candidate=candidate,
                        artifact_root=artifact_root,
                        settings=settings,
                        repeats=FINAL_CONFIRM_REPEATS,
                        modes=("always", "never"),
                        execution_stage="confirm_stage2",
                    )
                    mode_results.extend(confirm_rows)
                    confirm_summary["status"] = (
                        "confirmed_success" if confirm_summary["passed_threshold"] else "rejected"
                    )
                    attempt_summaries.append(confirm_summary)
                    manifest["attempt_summaries"] = attempt_summaries
                    manifest["mode_results"] = mode_results
                    if confirm_summary["passed_threshold"]:
                        madvise_summary, madvise_rows = evaluate_candidate(
                            candidate=candidate,
                            artifact_root=artifact_root,
                            settings=settings,
                            repeats=FINAL_CONFIRM_REPEATS,
                            modes=("always", "madvise", "never"),
                            execution_stage="final_with_madvise",
                        )
                        mode_results.extend(madvise_rows)
                        madvise_summary["status"] = (
                            "confirmed_success" if madvise_summary["passed_threshold"] else "completed"
                        )
                        attempt_summaries.append(madvise_summary)
                        manifest["attempt_summaries"] = attempt_summaries
                        manifest["mode_results"] = mode_results
                        manifest["status"] = "completed"
                        manifest["finished_at_utc"] = _utc_now_text()
                        manifest["final_candidate"] = candidate.manifest_dict()
                        manifest["current_stage"] = "finished"
                        clear_found = True
                        _persist_state(
                            artifact_root=artifact_root,
                            manifest=manifest,
                            attempt_summaries=attempt_summaries,
                            calibration_attempts=calibration_attempts,
                            mode_results=mode_results,
                        )
                        break

        if not clear_found and manifest["status"] == "running":
            manifest["status"] = "completed"
            manifest["finished_at_utc"] = _utc_now_text()
            manifest["current_stage"] = "finished"
            manifest["observations"] = [
                "No candidate reached the 5% always-vs-never YCSB run-time threshold inside the overnight budget.",
                "The report and latest dashboard keep the strongest near-miss candidates for the next search.",
            ]
            _persist_state(
                artifact_root=artifact_root,
                manifest=manifest,
                attempt_summaries=attempt_summaries,
                calibration_attempts=calibration_attempts,
                mode_results=mode_results,
            )

        return manifest
    finally:
        stop_repo_redis()
        if system_snapshot is not None:
            restore_system(system_snapshot)
        runner_status = "completed" if manifest.get("status") == "completed" else "failed"
        RUNNER_STATUS_PATH.write_text(runner_status + "\n", encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for the runtime search."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container-name", default=os.uname().nodename)
    parser.add_argument("--branch-name", default="redis_runtimes")
    parser.add_argument("--total-budget-hours", type=float, default=8.0)
    parser.add_argument("--stage1-hours", type=float, default=4.5)
    parser.add_argument("--max-mode-runtime-minutes", type=float, default=30.0)
    parser.add_argument("--resume-manifest", default=None)
    parser.add_argument(
        "--no-execute",
        action="store_true",
        help="Validate candidate generation and print the first candidates without running Redis or YCSB.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the overnight Redis THP runtime search from the command line."""

    args = _build_parser().parse_args(argv)
    settings = SearchSettings(
        container_name=args.container_name,
        branch_name=args.branch_name,
        total_budget_hours=args.total_budget_hours,
        stage1_hours=args.stage1_hours,
        max_mode_runtime_minutes=args.max_mode_runtime_minutes,
        resume_manifest=args.resume_manifest,
        no_execute=args.no_execute,
    )
    manifest = run_runtime_search(settings)
    if settings.no_execute:
        return
    print(json.dumps({"status": manifest["status"], "artifact_root": manifest["artifact_root"]}))


if __name__ == "__main__":
    main()
