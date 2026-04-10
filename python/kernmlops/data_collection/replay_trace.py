"""Replay captured redis-cli MONITOR logs against a live Redis instance.

Pipeline overview::

We assume that a redis-server is started via
``redis-server ./config/redis.conf'' before the capture script. 

    1. Capture  -- ``scripts/capture_redis_trace.sh``
                   Runs YCSB load, saves an RDB snapshot to
                   ``data/redis_traces/snapshot.rdb``, then records
                   ``redis-cli monitor`` output for the run phase to
                   ``data/redis_traces/monitor_run.log``.

    2. Generate -- ``python generate_breakpoints.py <monitor_run_log>``
                   Parses the monitor log, counts per-key accesses, and writes
                   a Parquet file of breakpoint combinations to
                   ``data/breakpoints.parquet``.
                   
    3. Replay   -- ``python replay_trace.py <snapshot.rdb> <run_log> --breakpoints bp.parquet``
                   For each breakpoint combination in the Parquet file:
                     a. Restore the RDB snapshot (exact key-value layout from load).
                     b. MEMORY PURGE to reset allocator state.
                     c. Time the replay of the run trace with the combination's
                        per-key breakpoints.
                   Saves a result Parquet with the breakpoint vectors and
                   measured runtimes.

Usage::

    python replay_trace.py data/redis_traces/snapshot.rdb data/redis_traces/monitor_run.log \\
        --host 127.0.0.1 --port 6379 \\
        --breakpoints data/breakpoints.parquet \\
        --output data/results.parquet
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import logging
import os
import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl
import redis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex for a redis-cli MONITOR line, e.g.:
#   1234567890.123456 [0 127.0.0.1:12345] "HSET" "user1234" "field0" "val"
_MONITOR_LINE_RE: re.Pattern[str] = re.compile(
    r"^(?P<timestamp>[\d.]+)"
    r"\s+\[(?P<db_id>\d+)\s+(?P<client_addr>\S+)\]"
    r"\s+(?P<tokens>.+)$",
)

# Extracts individual double-quoted tokens (handles backslash escapes).
_QUOTED_TOKEN_RE: re.Pattern[str] = re.compile(r'"((?:[^"\\]|\\.)*)"')

EXECUTE_MAX_ATTEMPTS: int = 2
BREAK_PAGE_MAX_ATTEMPTS: int = 2
SPLIT_THP_SYSCALL_NR: int = 462
VAPTR_FIELD_NAME: str = "field0"
REDIS_RESTORE_TIMEOUT_S: float = 300.0

_THP_PATH = Path("/sys/kernel/mm/transparent_hugepage/enabled")
_THP_DEFRAG_PATH = Path("/sys/kernel/mm/transparent_hugepage/defrag")
_KHUGEPAGED_SLEEP_PATH = Path(
    "/sys/kernel/mm/transparent_hugepage/khugepaged/scan_sleep_millisecs"
)
_NUMA_BALANCING_PATH = Path("/proc/sys/kernel/numa_balancing")
_SWAPPINESS_PATH = Path("/proc/sys/vm/swappiness")
_OVERCOMMIT_PATH = Path("/proc/sys/vm/overcommit_memory")
_COMPACTION_PROACTIVENESS_PATH = Path("/proc/sys/vm/compaction_proactiveness")
_KSM_PATH = Path("/sys/kernel/mm/ksm/run")

_LIBC = ctypes.CDLL(None, use_errno=True)
_LIBC.syscall.restype = ctypes.c_long


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RedisCommand:
    """A single parsed command from a redis-cli MONITOR log line.

    Attributes:
        command: The Redis command verb (e.g. ``HSET``, ``GET``).
        args: All arguments following the command verb.
        key: The key being operated on (``args[0]``, or empty string).
    """

    command: str
    args: list[str] = field(default_factory=list)
    key: str = ""


@dataclass(frozen=True)
class SystemConfig:
    """Saved state of kernel tunables modified during benchmarking.

    Optional fields are ``None`` when the corresponding sysfs/procfs
    path does not exist on the host kernel.
    """

    thp_enabled: str
    thp_defrag: str
    khugepaged_scan_sleep_ms: str
    numa_balancing: str
    swappiness: str
    overcommit_memory: str
    compaction_proactiveness: str | None
    ksm_run: str | None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_line(line: str) -> RedisCommand | None:
    """Parse a single redis-cli MONITOR log line into a *RedisCommand*.

    Args:
        line: A raw line from the monitor log file.

    Returns:
        A populated ``RedisCommand`` if the line matches, or ``None``
        for lines that do not match (e.g. blank lines, ``OK`` responses).
    """
    line = line.strip()
    if not line:
        return None

    m = _MONITOR_LINE_RE.match(line)
    if m is None:
        return None

    tokens: list[str] = _QUOTED_TOKEN_RE.findall(m.group("tokens"))
    if not tokens:
        return None

    args = tokens[1:]
    return RedisCommand(
        command=tokens[0],
        args=args,
        key=args[0] if args else "",
    )


# ---------------------------------------------------------------------------
# Break-page helpers
# ---------------------------------------------------------------------------


def _get_redis_pid(client: redis.Redis) -> int:
    """Return the active Redis server pid.

    This helper expects the standard `INFO server` shape from `redis-py`
    with `decode_responses=True`.

    Example `INFO server` fragment:
        {"process_id": 608669, "tcp_port": 6379}

    Args:
        client: Active Redis connection to the target server.

    Returns:
        Redis server process ID.

    Raises:
        RuntimeError: If Redis does not report a usable process ID.
    """
    info = client.info("server")
    process_id = info.get("process_id")
    if not isinstance(process_id, int) or process_id <= 0:
        raise RuntimeError(
            f"Redis INFO server did not return a valid process_id: {info!r}"
        )
    return process_id



def _redis_process_exited(pid: int) -> bool:
    """Return whether *pid* has exited and no longer needs waiting.

    Replay starts Redis in two different ways over its lifetime:
    the initial Redis process already exists before replay starts, while later
    restored Redis processes are children of this Python process. A child can
    therefore be "dead but not yet reaped", which still makes `os.kill(pid, 0)`
    succeed even though Redis has finished shutting down.

    Example output:
        True

    Args:
        pid: Process ID of the Redis server being shut down.

    Returns:
        `True` when the process is gone or has been reaped, else `False`.
    """
    try:
        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        waited_pid = 0

    if waited_pid == pid:
        return True

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False

    return False


def _wait_for_redis_exit(pid: int, timeout: float = 30.0) -> None:
    """Block until the target Redis process has stopped.

    Args:
        pid: Process ID of the Redis server being shut down.
        timeout: Maximum seconds to wait before raising `TimeoutError`.

    Raises:
        TimeoutError: If Redis still appears alive after *timeout* seconds.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _redis_process_exited(pid):
            return
        time.sleep(0.1)
    raise TimeoutError(f"Redis pid {pid} did not exit after SHUTDOWN")


def _resolve_key_vaddr(client: redis.Redis, key: str) -> int:
    """Return the value address that `VAPTR` reports for one Redis key.

    This script assumes the `capture_redis_trace.sh` workload shape:
    exactly one YCSB field per key, so the only supported VAPTR lookup here is
    `FIELD field0`.

    Expected VAPTR reply for one key:
        [["user-proof", "0x7fffef580009"]]

    Args:
        client: Active Redis connection with `decode_responses=True`.
        key: Redis key whose `field0` value should be resolved.

    Returns:
        Virtual address of that value as an integer.

    Raises:
        RuntimeError: If VAPTR is unavailable or returns an unexpected shape.
    """
    reply = client.execute_command("VAPTR", "FIELD", VAPTR_FIELD_NAME, key)
    try:
        reply_key, reply_vaddr = reply[0]
    except (IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"VAPTR returned an unexpected reply for key {key!r}: {reply!r}"
        ) from exc

    if reply_key != key:
        raise RuntimeError(
            f"VAPTR returned key {reply_key!r} while resolving {key!r}: {reply!r}"
        )
    if reply_vaddr == "(nil)":
        raise RuntimeError(
            f"VAPTR did not resolve a direct pointer for key {key!r} field {VAPTR_FIELD_NAME!r}"
        )

    try:
        return int(reply_vaddr, 16)
    except ValueError as exc:
        raise RuntimeError(
            f"VAPTR returned a malformed address for key {key!r}: {reply_vaddr!r}"
        ) from exc


def _invoke_split_thp_syscall(pid: int, vaddr: int) -> None:
    """Invoke the custom `split_thp(pid, vaddr)` syscall.

    Args:
        pid: Target Redis process ID.
        vaddr: Virtual address inside the THP to split.

    Raises:
        OSError: If the kernel rejects the syscall.
    """
    ctypes.set_errno(0)
    rc = _LIBC.syscall(
        ctypes.c_long(SPLIT_THP_SYSCALL_NR),
        ctypes.c_long(pid),
        ctypes.c_ulong(vaddr),
    )
    if rc == 0:
        return

    err = ctypes.get_errno() or errno.EIO
    raise OSError(err, os.strerror(err))


def break_page(client: redis.Redis, redis_pid: int, key: str) -> bool:
    """Resolve one Redis key and try to split the THP holding its value.

    This helper assumes the replay workload shape from
    ``capture_redis_trace.sh``: each Redis hash has exactly one YCSB value
    field, so the only supported lookup here is ``VAPTR FIELD field0 <key>``.

    Example result:
        True

    Args:
        client: Active Redis connection with ``decode_responses=True``.
        redis_pid: Process ID of the live Redis server being targeted.
        key: Redis key whose ``field0`` value should be split out of its THP.

    Returns:
        ``True`` when the split syscall succeeds, or ``False`` after
        ``BREAK_PAGE_MAX_ATTEMPTS`` failed syscall attempts.

    Raises:
        RuntimeError: If Redis metadata or VAPTR resolution is not usable.
    """
    vaddr, error = _break_page_for_pid(client, redis_pid, key)
    if error is not None:
        logger.warning(
            "break_page: split_thp(pid=%d, vaddr=0x%x) failed for key %r after %d attempts: errno=%d (%s)",
            redis_pid,
            vaddr,
            key,
            BREAK_PAGE_MAX_ATTEMPTS,
            error.errno,
            error.strerror,
        )
        return False

    return True


def _break_page_for_pid(
    client: redis.Redis,
    redis_pid: int,
    key: str,
) -> tuple[int, OSError | None]:
    """Resolve one Redis key and retry the split syscall for its value address.

    This resolves ``field0`` exactly once, then retries only the
    ``split_thp(pid, vaddr)`` syscall. Redis/VAPTR problems are still treated
    as configuration errors and raised immediately.

    Example result:
        (0x7fffef580009, None)

    Args:
        client: Active Redis connection with ``decode_responses=True``.
        redis_pid: Cached process ID of the Redis server for this replay run.
        key: Redis key whose ``field0`` value should be split.

    Returns:
        A pair ``(vaddr, error)`` where ``error`` is ``None`` on success or
        the final ``OSError`` after ``BREAK_PAGE_MAX_ATTEMPTS`` failed syscall
        attempts.

    Raises:
        RuntimeError: If VAPTR does not resolve a usable address.
    """
    vaddr = _resolve_key_vaddr(client, key)
    last_error: OSError | None = None

    for attempt in range(1, BREAK_PAGE_MAX_ATTEMPTS + 1):
        try:
            _invoke_split_thp_syscall(redis_pid, vaddr)
            return vaddr, None
        except OSError as exc:
            last_error = exc
            if attempt < BREAK_PAGE_MAX_ATTEMPTS:
                logger.info(
                    "break_page: retrying split_thp(pid=%d, vaddr=0x%x) for key %r after attempt %d/%d failed: errno=%d (%s)",
                    redis_pid,
                    vaddr,
                    key,
                    attempt,
                    BREAK_PAGE_MAX_ATTEMPTS,
                    exc.errno,
                    exc.strerror,
                )

    return vaddr, last_error


# ---------------------------------------------------------------------------
# System configuration
# ---------------------------------------------------------------------------


def _sysfs_write(path: Path, value: str) -> None:
    """Write *value* to a sysfs/procfs file via a privileged bash redirect."""
    subprocess.check_call(
        ["sudo", "bash", "-c", f"echo {value} > {path}"],
        stdout=subprocess.DEVNULL,
    )


def setup_system() -> SystemConfig:
    """Snapshot kernel tunables and apply benchmark-optimal settings.

    Sets THP to ``always`` so all Redis allocations begin as huge pages
    (breakpoints are then used to break them up selectively). Disables
    background memory management that would otherwise add timing noise:
    THP defrag, khugepaged, NUMA balancing, KSM, proactive compaction,
    and swap. Pins overcommit_memory to ``1`` (always allow) for
    consistent allocator behaviour.

    Returns:
        A ``SystemConfig`` holding the pre-modification values for use by
        :func:`teardown_system`.
    """
    config = SystemConfig(
        thp_enabled=_THP_PATH.read_text().strip(),
        thp_defrag=_THP_DEFRAG_PATH.read_text().strip(),
        khugepaged_scan_sleep_ms=_KHUGEPAGED_SLEEP_PATH.read_text().strip(),
        numa_balancing=_NUMA_BALANCING_PATH.read_text().strip(),
        swappiness=_SWAPPINESS_PATH.read_text().strip(),
        overcommit_memory=_OVERCOMMIT_PATH.read_text().strip(),
        compaction_proactiveness=(
            _COMPACTION_PROACTIVENESS_PATH.read_text().strip()
            if _COMPACTION_PROACTIVENESS_PATH.exists()
            else None
        ),
        ksm_run=(_KSM_PATH.read_text().strip() if _KSM_PATH.exists() else None),
    )

    logger.info("Setting up system configuration ...")
    _sysfs_write(_THP_PATH, "always")
    _sysfs_write(_THP_DEFRAG_PATH, "never")
    _sysfs_write(_KHUGEPAGED_SLEEP_PATH, "4294967295")  # max uint32 — disables scanning
    _sysfs_write(_NUMA_BALANCING_PATH, "0")
    _sysfs_write(_SWAPPINESS_PATH, "0")
    _sysfs_write(_OVERCOMMIT_PATH, "1")  # always allow
    if config.compaction_proactiveness is not None:
        _sysfs_write(_COMPACTION_PROACTIVENESS_PATH, "0")
    if config.ksm_run is not None:
        _sysfs_write(_KSM_PATH, "0")
    logger.info(
        "System configuration applied: "
        "thp=always, thp_defrag=never, khugepaged_scan_sleep_ms=4294967295, "
        "numa_balancing=0, swappiness=0, overcommit_memory=1%s%s",
        ", compaction_proactiveness=0" if config.compaction_proactiveness is not None else "",
        ", ksm_run=0" if config.ksm_run is not None else "",
    )

    return config


def teardown_system(config: SystemConfig) -> None:
    """Restore kernel tunables to the values saved by :func:`setup_system`.

    Args:
        config: The ``SystemConfig`` returned by :func:`setup_system`.
    """
    logger.info("Restoring system configuration ...")

    # The THP and defrag files store e.g. "always [madvise] never"; restore
    # only the bracketed (active) word.
    for path, raw in [
        (_THP_PATH, config.thp_enabled),
        (_THP_DEFRAG_PATH, config.thp_defrag),
    ]:
        match = re.search(r"\[(\w+)\]", raw)
        _sysfs_write(path, match.group(1) if match else raw)

    _sysfs_write(_KHUGEPAGED_SLEEP_PATH, config.khugepaged_scan_sleep_ms)
    _sysfs_write(_NUMA_BALANCING_PATH, config.numa_balancing)
    _sysfs_write(_SWAPPINESS_PATH, config.swappiness)
    _sysfs_write(_OVERCOMMIT_PATH, config.overcommit_memory)
    if config.compaction_proactiveness is not None:
        _sysfs_write(_COMPACTION_PROACTIVENESS_PATH, config.compaction_proactiveness)
    if config.ksm_run is not None:
        _sysfs_write(_KSM_PATH, config.ksm_run)

    logger.info("System configuration restored.")


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------


def memory_purge(client: redis.Redis) -> None:
    """Run ``MEMORY PURGE`` to release freed memory back to the OS.

    Args:
        client: An active Redis connection.
    """
    client.execute_command("MEMORY", "PURGE")


def _wait_for_redis(client: redis.Redis, timeout: float = 30.0) -> None:
    """Block until Redis is reachable or *timeout* seconds have elapsed.

    Args:
        client: A ``redis.Redis`` instance pointing at the target server.
        timeout: Maximum seconds to wait before raising ``TimeoutError``.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            client.ping()
            return
        except redis.RedisError:
            time.sleep(0.1)
    raise TimeoutError(f"Redis did not become reachable within {timeout}s")


def restore_snapshot(client: redis.Redis, snapshot_path: Path) -> None:
    """Restore Redis to the state captured in an RDB snapshot.

    Copies *snapshot_path* to ``dump.rdb`` in the current directory, stops the
    active Redis process, and restarts with ``redis-server ./config/redis.conf``.

    Args:
        client: An active Redis connection.
        snapshot_path: Path to the ``snapshot.rdb`` file produced by
            ``capture_redis_trace.sh``.
    """
    pid = _get_redis_pid(client)
    rdb_dir = client.config_get("dir")["dir"]
    rdb_file = client.config_get("dbfilename")["dbfilename"]
    rdb_path = Path(rdb_dir) / rdb_file
    subprocess.run(["sudo", "cp", str(snapshot_path), rdb_path], check=True)
    try:
        client.execute_command("SHUTDOWN", "NOSAVE")
    except redis.ConnectionError:
        pass

    _wait_for_redis_exit(pid)

    start_redis = ["redis-server", "./config/redis.conf", "--dir", rdb_dir, "--dbfilename", rdb_file]
    vaptr_module = Path("redis-module/vaptr.so")
    if vaptr_module.exists():
        start_redis += ["--loadmodule", str(vaptr_module.resolve())]

    subprocess.Popen(
        start_redis,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _wait_for_redis(client, timeout=REDIS_RESTORE_TIMEOUT_S)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def replay(
    trace_path: Path,
    client: redis.Redis,
    breakpoints: dict[str, int] | None = None,
) -> int:
    """Replay a redis-cli MONITOR log against a live Redis instance.

    Args:
        trace_path: Path to the redis-cli monitor log file.
        client: Active Redis connection to the server being replayed.
        breakpoints: Optional ``{key: access_num}`` mapping. ``None``
            disables breakpoint checking.

    Returns:
        Total number of commands replayed.
    """
    if breakpoints is None:
        breakpoints = {}

    redis_pid = _get_redis_pid(client)

    access_counts: Counter[str] = Counter()

    with open(trace_path, encoding="utf-8") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            cmd = parse_line(raw_line)
            if cmd is None:
                continue

            key = cmd.key

            # --- Breakpoint check (fires before the N-th access) ----------
            if key and key in breakpoints:
                if access_counts[key] == breakpoints[key]:
                    _invoke_break_page(
                        client,
                        redis_pid,
                        key,
                        line_no,
                        breakpoints[key],
                    )

            # --- Execute against Redis ------------------------------------
            if _execute(client, cmd, line_no) and key:
                access_counts[key] += 1

    return sum(access_counts.values())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _invoke_break_page(
    client: redis.Redis,
    redis_pid: int,
    key: str,
    line_no: int,
    breakpoint: int,
) -> None:
    """Invoke the replay-time THP split for one key.

    Args:
        client: Active Redis connection to the server being replayed.
        redis_pid: Cached Redis process ID for this replay run.
        key: The Redis key to break.
        line_no: Current line number in the trace (for diagnostics).
        breakpoint: The access number at which the break was triggered.

    Raises:
        RuntimeError: If VAPTR resolution fails.
    """
    vaddr, error = _break_page_for_pid(client, redis_pid, key)
    if error is not None:
        logger.warning(
            "line %d: break_page failed for key '%s' at access %d after %d attempts: pid=%d vaddr=0x%x errno=%d (%s)",
            line_no,
            key,
            breakpoint,
            BREAK_PAGE_MAX_ATTEMPTS,
            redis_pid,
            vaddr,
            error.errno,
            error.strerror,
        )
        return

def _execute(
    client: redis.Redis,
    cmd: RedisCommand,
    line_no: int,
) -> bool:
    """Execute a single Redis command, retrying once on failure.

    Args:
        client: Active Redis connection to the server being replayed.
        cmd: The parsed command to execute.
        line_no: Current line number in the trace (for diagnostics).

    Returns:
        ``True`` if the command succeeded, ``False`` otherwise.
    """

    for attempt in range(EXECUTE_MAX_ATTEMPTS):
        try:
            client.execute_command(cmd.command, *cmd.args)
            return True
        except redis.RedisError as exc:
            if attempt == EXECUTE_MAX_ATTEMPTS - 1:
                logger.warning(
                    "line %d: Redis error on %s '%s': %s",
                    line_no,
                    cmd.command,
                    cmd.key,
                    exc,
                )
    return False


# ---------------------------------------------------------------------------
# Benchmark loop
# ---------------------------------------------------------------------------


def run_benchmark(
    snapshot_path: Path,
    run_trace: Path,
    breakpoints_df: pl.DataFrame,
    host: str,
    port: int,
    output: Path,
    runs: int = 3,
) -> None:
    """For each breakpoint combination: restore snapshot, then time the run.

    Calls :func:`setup_system` once before the loop to set THP to ``always``
    and silence background memory management noise, then restores original
    settings unconditionally via :func:`teardown_system` in a ``finally``
    block.

    Each combination is replayed *runs* times. The output Parquet file
    contains the breakpoint vector plus ``runtime_s_1``, ``runtime_s_2``,
    etc. columns — one per run.

    Args:
        snapshot_path: Path to ``snapshot.rdb`` from ``capture_redis_trace.sh``.
        run_trace: Path to the monitor_run.log file.
        breakpoints_df: Parquet-derived DataFrame; each row is one combo.
        host: Redis server hostname or IP.
        port: Redis server port.
        output: Destination Parquet file for results.
        runs: Number of times to replay each breakpoint combination.
    """
    client = redis.Redis(host=host, port=port, decode_responses=True)

    results: list[dict] = []
    n_combos = len(breakpoints_df)

    sys_config = setup_system()
    try:
        for idx, row in enumerate(breakpoints_df.iter_rows(named=True)):
            breakpoints: dict[str, int] = dict(row)
            row_result: dict = {**breakpoints}

            for run in range(1, runs + 1):
                # 1. Restore snapshot
                logger.info(
                    "[%d/%d run %d/%d] Restoring snapshot ...",
                    idx + 1,
                    n_combos,
                    run,
                    runs,
                )
                restore_snapshot(client, snapshot_path)

                # 2. Post-restore memory purge
                memory_purge(client)

                # 3. Timed run replay
                logger.info(
                    "[%d/%d run %d/%d] Timing run trace ...",
                    idx + 1,
                    n_combos,
                    run,
                    runs,
                )
                t0 = time.perf_counter()
                total_cmds = replay(
                    run_trace,
                    client,
                    breakpoints=breakpoints,
                )
                runtime_s = time.perf_counter() - t0

                logger.info(
                    "[%d/%d run %d/%d] Done — %d commands in %.3fs",
                    idx + 1,
                    n_combos,
                    run,
                    runs,
                    total_cmds,
                    runtime_s,
                )
                row_result[f"runtime_s_{run}"] = runtime_s

            results.append(row_result)

        output.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(results).write_parquet(output)
        logger.info("Results written to %s", output)
    finally:
        teardown_system(sys_config)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "For each breakpoint combination: restore an RDB snapshot, then "
            "time the replay of the run trace. Saves a Parquet file with "
            "breakpoint vectors and runtimes."
        ),
    )
    parser.add_argument(
        "snapshot",
        type=Path,
        help="Path to snapshot.rdb produced by capture_redis_trace.sh.",
    )
    parser.add_argument(
        "run_trace",
        type=Path,
        help="Path to the redis-cli monitor log for the YCSB run phase.",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Redis host (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=6379,
        help="Redis port (default: 6379).",
    )
    parser.add_argument(
        "--breakpoints",
        type=Path,
        default=Path("data/breakpoints.parquet"),
        help="Path to breakpoints Parquet file (from generate_breakpoints.py). "
        "If omitted, runs once with no breakpoints.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/results.parquet"),
        help="Output Parquet file for results (default: data/results.parquet).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of times to replay each breakpoint combination (default: 3).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output including warnings.",
    )
    return parser


def main() -> None:
    """Entry point for the replay_trace CLI."""
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        format="%(levelname)s: %(message)s",
        level=logging.INFO if args.verbose else logging.ERROR,
    )

    snapshot_path: Path = args.snapshot
    run_trace: Path = args.run_trace

    for p in (snapshot_path, run_trace):
        if not p.is_file():
            parser.error(f"File not found: {p}")

    if args.breakpoints is not None:
        bp_path: Path = args.breakpoints
        if not bp_path.is_file():
            parser.error(f"Breakpoints file not found: {bp_path}")
        breakpoints_df = pl.read_parquet(bp_path)
    else:
        breakpoints_df = pl.DataFrame([{}])

    run_benchmark(
        snapshot_path=snapshot_path,
        run_trace=run_trace,
        breakpoints_df=breakpoints_df,
        host=args.host,
        port=args.port,
        output=args.output,
        runs=args.runs,
    )


if __name__ == "__main__":
    main()
