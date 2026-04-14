"""Shared helpers for the one-off Redis THP replication experiments."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _PACKAGE_ROOT = Path(__file__).resolve().parents[3]
    _PACKAGE_ROOT_STR = str(_PACKAGE_ROOT)
    if _PACKAGE_ROOT_STR not in sys.path:
        sys.path.insert(0, _PACKAGE_ROOT_STR)

import polars as pl

from replay.run_overnight_runtime_matrix import (
    DATA_DIR,
    MANIFEST_DIR,
    REPO_ROOT,
    TEMP_ANALYSIS_DIR,
    TRACE_DIR,
    _capture_output,
    _chown_path,
    _repo_redis_cli_command,
    _run_command,
    _safe_unlink,
    _shell_join,
    _utc_now_text,
)
from redis_runtime import load_repo_redis_endpoint


EXPERIMENT_DIR = DATA_DIR / "redis_thp_replication"
PART_A_DIR = EXPERIMENT_DIR / "part_a"
PART_B_DIR = EXPERIMENT_DIR / "part_b"
PART_C_DIR = EXPERIMENT_DIR / "part_c"

THP_ENABLED_PATH = Path("/sys/kernel/mm/transparent_hugepage/enabled")
KHUGEPAGED_SLEEP_PATH = Path(
    "/sys/kernel/mm/transparent_hugepage/khugepaged/scan_sleep_millisecs"
)
OVERCOMMIT_PATH = Path("/proc/sys/vm/overcommit_memory")
REDIS_FILENAME = "dump.rdb"


@dataclass(frozen=True)
class SystemSnapshot:
    """Original host tunables that the one-off experiments temporarily change."""

    thp_enabled: str
    khugepaged_scan_sleep_ms: str
    overcommit_memory: str


@dataclass(frozen=True)
class RawBenchmarkConfig:
    """Raw YCSB/Redis benchmark parameters for part A.

    Attributes:
        name: Short stable config identifier.
        thp_mode: One of ``always``, ``madvise``, or ``never``.
        record_count: Records inserted by each load phase.
        operation_count: Operations issued by each run phase.
        outer_repeat: Number of load/run cycles in one benchmark repeat.
        read_proportion: YCSB read proportion.
        delete_proportion: YCSB delete proportion.
        insert_proportion: YCSB insert proportion.
    """

    name: str
    thp_mode: str
    record_count: int
    operation_count: int
    outer_repeat: int
    read_proportion: float
    delete_proportion: float
    insert_proportion: float = 0.0
    update_proportion: float = 0.0
    scan_proportion: float = 0.0
    readmodifywrite_proportion: float = 0.0
    field_count: int = 1
    field_length: int = 2_097_152
    min_field_length: int = 4_096
    thread_count: int = 16
    request_distribution: str = "zipfian"
    explicit_purge: bool = True
    server_sleep_seconds: int = 10
    target: int = 10_000
    insert_order: str = "hashed"
    zero_padding: int = 1
    field_length_distribution: str = "uniform"
    khugepaged_scan_sleep_millis: int = 500
    redis_timeout_millis: int = 120_000

    def manifest_dict(self) -> dict[str, Any]:
        """Return a JSON-safe description of this config."""

        return {
            "name": self.name,
            "thp_mode": self.thp_mode,
            "record_count": self.record_count,
            "operation_count": self.operation_count,
            "outer_repeat": self.outer_repeat,
            "read_proportion": self.read_proportion,
            "delete_proportion": self.delete_proportion,
            "insert_proportion": self.insert_proportion,
            "update_proportion": self.update_proportion,
            "scan_proportion": self.scan_proportion,
            "readmodifywrite_proportion": self.readmodifywrite_proportion,
            "field_count": self.field_count,
            "field_length": self.field_length,
            "min_field_length": self.min_field_length,
            "thread_count": self.thread_count,
            "request_distribution": self.request_distribution,
            "explicit_purge": self.explicit_purge,
            "server_sleep_seconds": self.server_sleep_seconds,
            "target": self.target,
            "insert_order": self.insert_order,
            "zero_padding": self.zero_padding,
            "field_length_distribution": self.field_length_distribution,
            "khugepaged_scan_sleep_millis": self.khugepaged_scan_sleep_millis,
            "redis_timeout_millis": self.redis_timeout_millis,
        }


def ensure_experiment_directories() -> None:
    """Create the one-off experiment output directories."""

    for path in (
        EXPERIMENT_DIR,
        PART_A_DIR,
        PART_B_DIR,
        PART_C_DIR,
        MANIFEST_DIR,
        TEMP_ANALYSIS_DIR,
        TRACE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
        _chown_path(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write one formatted JSON file with host-friendly ownership."""

    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _chown_path(path)


def write_dataframe(path: Path, df: pl.DataFrame) -> None:
    """Write one parquet file with host-friendly ownership."""

    df.write_parquet(path)
    _chown_path(path)


def snapshot_system() -> SystemSnapshot:
    """Capture the tunables that the one-off benchmarks will modify."""

    return SystemSnapshot(
        thp_enabled=THP_ENABLED_PATH.read_text(encoding="utf-8").strip(),
        khugepaged_scan_sleep_ms=KHUGEPAGED_SLEEP_PATH.read_text(
            encoding="utf-8"
        ).strip(),
        overcommit_memory=OVERCOMMIT_PATH.read_text(encoding="utf-8").strip(),
    )


def _sysfs_write(path: Path, value: str) -> None:
    """Write one value into sysfs or procfs and fail fast on errors."""

    subprocess.run(
        ["bash", "-lc", f"echo {shlex.quote(value)} > {shlex.quote(str(path))}"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def apply_benchmark_system_config(
    *,
    thp_mode: str,
    khugepaged_scan_sleep_millis: int,
) -> None:
    """Apply the limited THP-related settings used by the raw benchmarks."""

    _run_command(["bash", "-lc", "sync && echo 3 > /proc/sys/vm/drop_caches"])
    _sysfs_write(THP_ENABLED_PATH, thp_mode)
    _sysfs_write(KHUGEPAGED_SLEEP_PATH, str(khugepaged_scan_sleep_millis))
    _sysfs_write(OVERCOMMIT_PATH, "1")


def restore_system(snapshot: SystemSnapshot) -> None:
    """Restore the THP-related settings touched by the raw benchmarks."""

    active_mode = snapshot.thp_enabled
    if "[" in snapshot.thp_enabled and "]" in snapshot.thp_enabled:
        active_mode = snapshot.thp_enabled.split("[", 1)[1].split("]", 1)[0]
    _sysfs_write(THP_ENABLED_PATH, active_mode)
    _sysfs_write(KHUGEPAGED_SLEEP_PATH, snapshot.khugepaged_scan_sleep_ms)
    _sysfs_write(OVERCOMMIT_PATH, snapshot.overcommit_memory)


def start_repo_redis(*, run_dir: Path) -> int:
    """Start the repo-managed Redis server in daemon mode for one repeat."""

    endpoint = load_repo_redis_endpoint()
    run_dir.mkdir(parents=True, exist_ok=True)
    _chown_path(run_dir)
    stop_repo_redis()
    _safe_unlink(run_dir / REDIS_FILENAME)
    vaptr_module = REPO_ROOT / "redis-module" / "vaptr.so"
    cmd = [
        "redis-server",
        "./config/redis.conf",
        "--dir",
        str(run_dir),
        "--dbfilename",
        REDIS_FILENAME,
        "--daemonize",
        "yes",
    ]
    if vaptr_module.exists():
        cmd.extend(["--loadmodule", str(vaptr_module)])
    _run_command(cmd)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            pong = _capture_output(_repo_redis_cli_command("ping")).strip()
        except RuntimeError:
            time.sleep(0.25)
            continue
        if pong == "PONG":
            break
        time.sleep(0.25)
    else:  # pragma: no cover - live environment guard
        raise RuntimeError("Redis did not respond to PING after startup.")

    time.sleep(1.0)
    pid_text = _capture_output(_repo_redis_cli_command("INFO", "server"))
    for line in pid_text.splitlines():
        if line.startswith("process_id:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError("Redis INFO server did not report process_id.")


def stop_repo_redis() -> None:
    """Stop the repo-managed Redis server if one is running."""

    try:
        subprocess.run(
            _repo_redis_cli_command("SHUTDOWN", "NOSAVE"),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - live environment guard
        pass
    time.sleep(1.0)


def redis_memory_purge() -> None:
    """Issue ``MEMORY PURGE`` against the repo-managed Redis endpoint."""

    _capture_output(_repo_redis_cli_command("MEMORY", "PURGE"))


def parse_info(info_text: str) -> dict[str, Any]:
    """Parse a Redis INFO response into a JSON-safe mapping."""

    parsed: dict[str, Any] = {}
    for raw_line in info_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.isdigit():
            parsed[key] = int(value)
            continue
        try:
            parsed[key] = float(value)
        except ValueError:
            parsed[key] = value
    return parsed


def force_rdb_and_measure(run_dir: Path) -> tuple[int, int, dict[str, Any]]:
    """Save Redis state to RDB and return ``(bytes, dbsize, memory_info)``."""

    lastsave_before = int(_capture_output(_repo_redis_cli_command("LASTSAVE")).strip())
    _capture_output(_repo_redis_cli_command("BGSAVE"))
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        lastsave_now = int(_capture_output(_repo_redis_cli_command("LASTSAVE")).strip())
        if lastsave_now > lastsave_before:
            break
        time.sleep(0.5)
    else:  # pragma: no cover - live environment guard
        raise RuntimeError("Redis BGSAVE did not complete within 120s.")

    dump_path = run_dir / REDIS_FILENAME
    if not dump_path.exists():
        raise RuntimeError(f"Expected Redis dump at {dump_path} after BGSAVE.")
    dbsize = int(_capture_output(_repo_redis_cli_command("DBSIZE")).strip())
    memory_info = parse_info(_capture_output(_repo_redis_cli_command("INFO", "memory")))
    return dump_path.stat().st_size, dbsize, memory_info


def timed_run_command(
    cmd: list[str],
    *,
    log_path: Path,
    env: dict[str, str] | None = None,
) -> float:
    """Run one command, teeing stdout/stderr to a log file, and return wall time."""

    merged_env = os.environ.copy()
    if env is not None:
        merged_env.update(env)
    _safe_unlink(log_path)
    started = time.perf_counter()
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
    elapsed = time.perf_counter() - started
    _chown_path(log_path)
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {completed.returncode}: {_shell_join(cmd)} "
            f"(see {log_path})"
        )
    return elapsed


def container_available_memory_bytes() -> int:
    """Return MemAvailable from ``/proc/meminfo`` in bytes."""

    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            kib = int(line.split()[1])
            return kib * 1024
    raise RuntimeError("Could not read MemAvailable from /proc/meminfo.")


def repo_free_bytes() -> int:
    """Return free bytes on the bind-mounted repo volume."""

    return shutil.disk_usage(REPO_ROOT).free


def projected_dump_bytes(
    *,
    baseline_dump_bytes: int,
    baseline_record_count: int,
    projected_record_count: int,
) -> int:
    """Project RDB size linearly from a measured baseline."""

    return int(
        baseline_dump_bytes * (projected_record_count / float(baseline_record_count))
    )


def ycsb_dir() -> Path:
    """Return the installed YCSB directory inside the container."""

    candidate = Path.home() / "kernmlops-benchmark" / "ycsb" / "YCSB"
    if not (candidate / "bin" / "ycsb").exists():
        raise RuntimeError(f"Expected YCSB at {candidate}/bin/ycsb.")
    return candidate


def build_ycsb_command(
    config: RawBenchmarkConfig,
    *,
    phase: str,
    cycle_index: int,
) -> list[str]:
    """Build one raw YCSB command for the load or run phase."""

    endpoint = load_repo_redis_endpoint()
    ycsb_root = ycsb_dir()
    command = [
        "python3" if phase == "load" else str(ycsb_root / "bin" / "ycsb"),
    ]
    if phase == "load":
        command.extend(
            [
                str(ycsb_root / "bin" / "ycsb"),
                "load",
                "redis",
                "-s",
                "-P",
                str(ycsb_root / "workloads" / "workloada"),
            ]
        )
    elif phase == "run":
        command.extend(
            [
                "run",
                "redis",
                "-s",
                "-P",
                str(ycsb_root / "workloads" / "workloada"),
            ]
        )
    else:
        raise RuntimeError(f"Unsupported YCSB phase {phase!r}.")

    properties = [
        ("redis.host", endpoint.host),
        ("redis.port", str(endpoint.port)),
        ("redis.timeout", str(config.redis_timeout_millis)),
        ("fieldcount", str(config.field_count)),
        ("fieldlength", str(config.field_length)),
        ("minfieldlength", str(config.min_field_length)),
        ("insertorder", config.insert_order),
        ("zeropadding", str(config.zero_padding)),
        ("fieldlengthdistribution", config.field_length_distribution),
        ("threadcount", str(config.thread_count)),
        ("target", str(config.target)),
        ("requestdistribution", config.request_distribution),
    ]
    if phase == "load":
        properties.extend(
            [
                ("recordcount", str(config.record_count)),
                ("insertstart", str(cycle_index * config.record_count)),
            ]
        )
    else:
        properties.extend(
            [
                ("operationcount", str(config.operation_count)),
                ("recordcount", str((cycle_index + 1) * config.record_count)),
                ("workload", "site.ycsb.workloads.CoreWorkload"),
                ("readproportion", f"{config.read_proportion:.2f}"),
                ("deleteproportion", f"{config.delete_proportion:.2f}"),
                ("insertproportion", f"{config.insert_proportion:.2f}"),
                ("updateproportion", f"{config.update_proportion:.2f}"),
                (
                    "readmodifywriteproportion",
                    f"{config.readmodifywrite_proportion:.2f}",
                ),
                ("scanproportion", f"{config.scan_proportion:.2f}"),
            ]
        )
    for key, value in properties:
        command.extend(["-p", f"{key}={value}"])
    return command


def pin_process(pid: int, cpu: int) -> str:
    """Pin one process to a CPU and return the resulting affinity string."""

    _capture_output(["taskset", "-cp", str(cpu), str(pid)])
    return _capture_output(["taskset", "-cp", str(pid)]).strip()


def read_process_affinity(pid: int) -> str:
    """Return the current ``taskset -cp`` output for one process."""

    return _capture_output(["taskset", "-cp", str(pid)]).strip()


def write_markdown(path: Path, text: str) -> None:
    """Write one markdown file with host-friendly ownership."""

    path.write_text(text, encoding="utf-8")
    _chown_path(path)


def now_text() -> str:
    """Return a UTC timestamp string for manifests and reports."""

    return _utc_now_text()
