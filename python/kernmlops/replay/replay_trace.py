"""Replay captured redis-cli MONITOR logs against a live Redis instance.

Pipeline overview::

We assume that a redis-server is started via
``redis-server ./config/redis.conf'' before the capture script. 

    1. Capture  -- ``python/kernmlops/replay/capture_redis_trace.sh``
                   Runs YCSB load, saves an RDB snapshot to
                   ``data/redis_traces/snapshot.rdb``, then records
                   ``redis-cli monitor`` output for the run phase to
                   ``data/redis_traces/monitor_run.log``.

    2. Generate -- ``python python/kernmlops/replay/generate_breakpoints.py <monitor_run_log>``
                   Parses the monitor log, counts per-key accesses, and writes
                   a Parquet file of breakpoint combinations to
                   ``data/breakpoints.parquet``.
                   
    3. Replay   -- ``python python/kernmlops/replay/replay_trace.py <snapshot.rdb> <run_log> --breakpoints bp.parquet``
                   For each breakpoint combination in the Parquet file:
                     a. Restore the RDB snapshot (exact key-value layout from load).
                     b. MEMORY PURGE to reset allocator state.
                     c. Optionally collect configured replay counters on the host.
                     d. Time the replay of the run trace with the combination's
                        per-key breakpoints.
                   Saves a result Parquet with the breakpoint vectors and
                   measured runtimes.

Usage::

    python python/kernmlops/replay/replay_trace.py \\
        data/redis_traces/snapshot.rdb data/redis_traces/monitor_run.log \\
        --breakpoints data/breakpoints.parquet \\
        --output data/results.parquet

Replay reads the Redis bind and port from ``config/redis.conf`` so the replay
client always targets the same endpoint as the repo-managed Redis server.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

if __package__ in {None, ""}:
    _PACKAGE_ROOT = Path(__file__).resolve().parents[1]
    _PACKAGE_ROOT_STR = str(_PACKAGE_ROOT)
    if _PACKAGE_ROOT_STR not in sys.path:
        sys.path.insert(0, _PACKAGE_ROOT_STR)

import polars as pl
import redis
import yaml
from replay.hardware_collectors import (
    DTLBCounterMetrics,
    HardwareCollector,
)
from redis_runtime import load_repo_redis_endpoint

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


@dataclass(frozen=True)
class SplitPageResult:
    """Outcome of one replay-time attempt to split a key's THP.

    Attributes:
        vaddr: Resolved value address returned by ``VAPTR``.
        attempts: Number of syscall attempts issued for this split event.
        error: Final syscall error, or ``None`` when the split succeeded.
    """

    vaddr: int
    attempts: int
    error: OSError | None


@dataclass(frozen=True)
class ReplayStats:
    """Aggregate runtime-only replay stats for one timed run.

    Example output:
        {
            "commands_replayed": 4096,
            "split_events": 14,
            "split_successes": 12,
            "split_failures": 2,
            "split_syscall_attempts": 18,
            "split_max_attempts": 2,
        }

    Attributes:
        commands_replayed: Successful Redis commands executed from the trace.
        split_events: Replay-time split triggers encountered.
        split_successes: Split triggers that succeeded.
        split_failures: Split triggers that exhausted retries and failed.
        split_syscall_attempts: Total syscall attempts across all split events.
        split_max_attempts: Largest attempt count used by any single split event.
        split_total_wall_ms: Total wall-clock split time across all split events.
        split_max_wall_ms: Largest wall-clock split time for any single event.
        split_queue_lag_ms_mean: Mean queue lag before a threaded split started.
        split_queue_lag_ms_max: Largest queue lag before a threaded split started.
    """

    commands_replayed: int
    split_events: int
    split_successes: int
    split_failures: int
    split_syscall_attempts: int
    split_max_attempts: int
    split_total_wall_ms: float = 0.0
    split_max_wall_ms: float = 0.0
    split_queue_lag_ms_mean: float = 0.0
    split_queue_lag_ms_max: float = 0.0


@dataclass(frozen=True)
class _SplitRequest:
    """One queued replay-time split request for threaded dispatch.

    Attributes:
        key: Redis key whose THP should be split.
        line_no: Monitor-log line number that triggered the split.
        breakpoint: Access count at which the split fired.
        enqueued_at: ``perf_counter`` timestamp when the hot path queued it.
    """

    key: str
    line_no: int
    breakpoint: int
    enqueued_at: float


@dataclass(frozen=True)
class _CompletedSplit:
    """One completed split request plus timing metadata."""

    result: SplitPageResult
    wall_ms: float
    queue_lag_ms: float


class _ThreadedBreakWorker:
    """Process replay split requests on a dedicated background thread.

    The worker owns its own Redis client so the replay hot path never shares
    the main client object across threads.
    """

    def __init__(
        self,
        *,
        client_kwargs: dict[str, object],
        redis_pid: int,
    ) -> None:
        self._client_kwargs = client_kwargs
        self._redis_pid = redis_pid
        self._queue: queue.Queue[_SplitRequest | None] = queue.Queue()
        self._completed: list[_CompletedSplit] = []
        self._exception: BaseException | None = None
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="replay-break-worker",
            daemon=True,
        )

    def start(self) -> None:
        """Start the background split worker."""

        self._thread.start()

    def enqueue(self, request: _SplitRequest) -> None:
        """Queue one split request, failing immediately on worker errors."""

        self.raise_if_failed()
        self._queue.put(request)

    def raise_if_failed(self) -> None:
        """Raise the first worker exception if one was recorded."""

        if self._exception is not None:
            raise RuntimeError(
                "Threaded break dispatch failed during replay."
            ) from self._exception

    def finish(self) -> tuple[_CompletedSplit, ...]:
        """Drain the queue, stop the worker, and return completed splits."""

        self._queue.put(None)
        self._queue.join()
        self._thread.join()
        self.raise_if_failed()
        return tuple(self._completed)

    def abort(self) -> None:
        """Stop the worker after the current item and discard future work."""

        self._stop_event.set()
        self._queue.put(None)
        self._queue.join()
        self._thread.join()

    def _run(self) -> None:
        """Process queued split requests until a sentinel is received."""

        client = redis.Redis(**self._client_kwargs)
        while True:
            request = self._queue.get()
            try:
                if request is None:
                    return
                if self._stop_event.is_set():
                    continue

                started_at = time.perf_counter()
                queue_lag_ms = (started_at - request.enqueued_at) * 1000.0
                result = _invoke_break_page(
                    client,
                    self._redis_pid,
                    request.key,
                    request.line_no,
                    request.breakpoint,
                )
                wall_ms = (time.perf_counter() - started_at) * 1000.0
                self._completed.append(
                    _CompletedSplit(
                        result=result,
                        wall_ms=wall_ms,
                        queue_lag_ms=queue_lag_ms,
                    )
                )
            except BaseException as exc:  # pragma: no cover - fail-fast path
                self._exception = exc
                self._stop_event.set()
            finally:
                self._queue.task_done()


def _resolve_break_dispatch(
    break_dispatch: Literal["inline", "threaded", "mixed"],
    *,
    run_number: int,
    runs: int,
) -> Literal["inline", "threaded"]:
    """Resolve the effective break-dispatch mode for one timed run.

    ``mixed`` means the first half of runs are inline and the second half are
    threaded. This requires an even total run count.

    Raises:
        RuntimeError: If ``mixed`` was requested with an odd run count.
    """

    if break_dispatch == "mixed":
        if runs % 2 != 0:
            raise RuntimeError(
                "Break dispatch mode 'mixed' requires an even --runs value."
            )
        return "inline" if run_number <= runs // 2 else "threaded"
    return break_dispatch


def _summarize_completed_splits(
    completed_splits: tuple[_CompletedSplit, ...],
) -> tuple[float, float, float, float]:
    """Return total/max split wall time and mean/max queue lag in milliseconds."""

    if not completed_splits:
        return (0.0, 0.0, 0.0, 0.0)

    wall_times = [split.wall_ms for split in completed_splits]
    queue_lags = [split.queue_lag_ms for split in completed_splits]
    return (
        sum(wall_times),
        max(wall_times),
        sum(queue_lags) / len(queue_lags),
        max(queue_lags),
    )


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


def _require_root_for_counter_collection(counter_names: tuple[str, ...]) -> None:
    """Fail fast when replay counters are requested without root privileges.

    Replay opens direct ``perf_event_open`` counters for the Redis thread set on
    the host. In the environments used by this repo that path is run under
    ``sudo`` so replay fails immediately when direct replay counters are
    requested without an effective root user.

    Example input:
        ("dtlb_loads", "dtlb_misses")

    Raises:
        PermissionError: If the current process is not running as root.
    """
    if not counter_names:
        return
    if os.geteuid() != 0:
        counters = ", ".join(counter_names)
        raise PermissionError(
            f"Replay collectors [{counters}] require sudo/root because the "
            "replay counters use direct perf_event counters on the host."
        )


def _load_counter_config(config_path: Path | None) -> tuple[str, ...]:
    """Load replay counter names from a small YAML collector config.

    Expected YAML shape:
        {"collectors": ["dtlb_loads", "dtlb_misses"]}

    Args:
        config_path: Optional path to the replay collector config YAML.

    Returns:
        Ordered replay counter names to collect during each timed run.

    Raises:
        RuntimeError: If the YAML shape is invalid or requests an unknown
            replay counter.
    """
    if config_path is None:
        return ()

    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if raw_config is None:
        return ()
    if not isinstance(raw_config, dict):
        raise RuntimeError(
            "Replay collector config must be a YAML mapping with a "
            "'collectors' list."
        )

    raw_collectors = raw_config.get("collectors", [])
    if not isinstance(raw_collectors, list) or any(
        not isinstance(counter_name, str) for counter_name in raw_collectors
    ):
        raise RuntimeError(
            "Replay collector config must define 'collectors' as a list of "
            "counter names."
        )

    normalized_collectors: list[str] = []
    seen: set[str] = set()
    supported = set(HardwareCollector.supported_counter_names())
    for raw_counter_name in raw_collectors:
        counter_name = raw_counter_name.strip()
        if not counter_name:
            raise RuntimeError(
                "Replay collector config cannot include empty counter names."
            )
        if counter_name not in supported:
            supported_text = ", ".join(HardwareCollector.supported_counter_names())
            raise RuntimeError(
                f"Unsupported replay counter {counter_name!r}. Supported "
                f"counters: {supported_text}."
            )
        if counter_name in seen:
            continue
        normalized_collectors.append(counter_name)
        seen.add(counter_name)
    return tuple(normalized_collectors)


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
        FileNotFoundError: If the key no longer has a direct pointer to split.
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
        raise FileNotFoundError(
            errno.ENOENT,
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
    result = _break_page_for_pid(client, redis_pid, key)
    if result.error is not None:
        logger.warning(
            "break_page: split_thp(pid=%d, vaddr=0x%x) failed for key %r after %d attempts: errno=%d (%s)",
            redis_pid,
            result.vaddr,
            key,
            result.attempts,
            result.error.errno,
            result.error.strerror,
        )
        return False

    return True


def _break_page_for_pid(
    client: redis.Redis,
    redis_pid: int,
    key: str,
) -> SplitPageResult:
    """Resolve one Redis key and retry the split syscall for its value address.

    This resolves ``field0`` exactly once, then retries only the
    ``split_thp(pid, vaddr)`` syscall. Redis/VAPTR problems are still treated
    as configuration errors and raised immediately.

    Example result:
        {"vaddr": 0x7fffef580009, "attempts": 1, "error": None}

    Args:
        client: Active Redis connection with ``decode_responses=True``.
        redis_pid: Cached process ID of the Redis server for this replay run.
        key: Redis key whose ``field0`` value should be split.

    Returns:
        A ``SplitPageResult`` describing the resolved address, how many syscall
        attempts were used, and the final syscall error if the split failed.

    Raises:
        RuntimeError: If VAPTR returns malformed metadata.
    """
    try:
        vaddr = _resolve_key_vaddr(client, key)
    except FileNotFoundError as exc:
        return SplitPageResult(vaddr=0, attempts=0, error=exc)
    last_error: OSError | None = None

    for attempt in range(1, BREAK_PAGE_MAX_ATTEMPTS + 1):
        try:
            _invoke_split_thp_syscall(redis_pid, vaddr)
            return SplitPageResult(vaddr=vaddr, attempts=attempt, error=None)
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

    return SplitPageResult(
        vaddr=vaddr,
        attempts=BREAK_PAGE_MAX_ATTEMPTS,
        error=last_error,
    )


# ---------------------------------------------------------------------------
# System configuration
# ---------------------------------------------------------------------------


def _sysfs_write(path: Path, value: str) -> None:
    """Write *value* to a sysfs/procfs file via a privileged bash redirect."""
    subprocess.check_call(
        ["sudo", "bash", "-c", f"echo {value} > {path}"],
        stdout=subprocess.DEVNULL,
    )


def _set_thp_enabled_mode(mode: str) -> None:
    """Set the host Transparent Huge Page enabled mode for the next restore.

    Replay uses two THP modes:

    - ``always`` for the intact-THP and split-only replay rows
    - ``never`` for the ``base_pages`` sentinel row

    Example input:
        "never"

    Args:
        mode: THP enabled mode to apply before the next Redis restore.

    Raises:
        RuntimeError: If *mode* is not one of the supported replay modes.
    """
    if mode not in {"always", "never"}:
        raise RuntimeError(
            f"Unsupported replay THP mode {mode!r}; expected 'always' or 'never'."
        )
    _sysfs_write(_THP_PATH, mode)


def _stage_snapshot_for_restore(snapshot_path: Path, rdb_path: Path) -> None:
    """Place the preserved snapshot at Redis's active ``dump.rdb`` path.

    Replay stages the preserved snapshot with a hard link. This is a fail-fast
    setup step: if the snapshot and Redis data directory are not link-compatible,
    replay should stop instead of silently taking a different restore path.

    Args:
        snapshot_path: Preserved snapshot file owned by the experiment.
        rdb_path: Active Redis ``dump.rdb`` location.
    """
    subprocess.run(["sudo", "rm", "-f", str(rdb_path)], check=True)
    subprocess.run(
        ["sudo", "ln", str(snapshot_path), str(rdb_path)],
        check=True,
    )



def setup_system() -> SystemConfig:
    """Snapshot kernel tunables and apply benchmark-optimal settings.

    Sets THP to ``always`` so all Redis allocations begin as huge pages
    by default. Individual replay rows may later switch THP to ``never`` before
    restoring Redis for the ``base_pages`` baseline. Disables background memory
    management that would otherwise add timing noise:
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
    logger.info("System configuration applied.")

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


def restore_snapshot(
    client: redis.Redis,
    snapshot_path: Path,
) -> None:
    """Restore Redis to the state captured in an RDB snapshot.

    Copies *snapshot_path* to ``dump.rdb`` in the current directory, stops the
    active Redis process, and restarts it.

    Args:
        client: An active Redis connection.
        snapshot_path: Path to the ``snapshot.rdb`` file produced by
            ``capture_redis_trace.sh``.
    """
    pid = _get_redis_pid(client)
    rdb_dir = client.config_get("dir")["dir"]
    rdb_file = client.config_get("dbfilename")["dbfilename"]
    rdb_path = Path(rdb_dir) / rdb_file
    _stage_snapshot_for_restore(snapshot_path, rdb_path)
    try:
        client.execute_command("SHUTDOWN", "NOSAVE")
    except redis.ConnectionError:
        pass

    _wait_for_redis_exit(pid)

    start_redis = [
        "redis-server",
        "./config/redis.conf",
        "--dir",
        rdb_dir,
        "--dbfilename",
        rdb_file,
    ]
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
    *,
    break_dispatch: Literal["inline", "threaded"] = "inline",
) -> ReplayStats:
    """Replay a redis-cli MONITOR log against a live Redis instance.

    Args:
        trace_path: Path to the redis-cli monitor log file.
        client: Active Redis connection to the server being replayed.
        breakpoints: Optional ``{key: access_num}`` mapping. ``None``
            disables breakpoint checking.
        break_dispatch: Split-dispatch mode for this run. ``inline`` breaks on
            the hot path; ``threaded`` queues split requests to a background
            worker.

    Returns:
        Runtime-only replay stats for this timed run.
    """
    if breakpoints is None:
        breakpoints = {}
    if break_dispatch not in {"inline", "threaded"}:
        raise RuntimeError(
            f"Unsupported replay break dispatch {break_dispatch!r}; expected "
            "'inline' or 'threaded'."
        )

    redis_pid = _get_redis_pid(client)
    threaded_worker: _ThreadedBreakWorker | None = None
    if break_dispatch == "threaded":
        threaded_worker = _ThreadedBreakWorker(
            client_kwargs=dict(client.connection_pool.connection_kwargs),
            redis_pid=redis_pid,
        )
        threaded_worker.start()

    access_counts: Counter[str] = Counter()
    commands_replayed = 0
    split_events = 0
    split_successes = 0
    split_failures = 0
    split_syscall_attempts = 0
    split_max_attempts = 0
    completed_splits: list[_CompletedSplit] = []

    try:
        with open(trace_path, encoding="utf-8") as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                cmd = parse_line(raw_line)
                if cmd is None:
                    continue

                key = cmd.key

                # --- Breakpoint check (fires before the N-th access) ----------
                if key and key in breakpoints:
                    if access_counts[key] == breakpoints[key]:
                        split_events += 1
                        if break_dispatch == "inline":
                            started_at = time.perf_counter()
                            split_result = _invoke_break_page(
                                client,
                                redis_pid,
                                key,
                                line_no,
                                breakpoints[key],
                            )
                            wall_ms = (time.perf_counter() - started_at) * 1000.0
                            completed_splits.append(
                                _CompletedSplit(
                                    result=split_result,
                                    wall_ms=wall_ms,
                                    queue_lag_ms=0.0,
                                )
                            )
                        else:
                            threaded_worker.enqueue(
                                _SplitRequest(
                                    key=key,
                                    line_no=line_no,
                                    breakpoint=breakpoints[key],
                                    enqueued_at=time.perf_counter(),
                                )
                            )

                # --- Execute against Redis ------------------------------------
                if _execute(client, cmd, line_no):
                    commands_replayed += 1
                    if key:
                        access_counts[key] += 1

                if threaded_worker is not None:
                    threaded_worker.raise_if_failed()
    finally:
        if threaded_worker is not None:
            completed_splits.extend(threaded_worker.finish())

    for completed_split in completed_splits:
        split_syscall_attempts += completed_split.result.attempts
        split_max_attempts = max(split_max_attempts, completed_split.result.attempts)
        if completed_split.result.error is None:
            split_successes += 1
        else:
            split_failures += 1

    split_total_wall_ms, split_max_wall_ms, split_queue_lag_ms_mean, split_queue_lag_ms_max = (
        _summarize_completed_splits(tuple(completed_splits))
    )

    return ReplayStats(
        commands_replayed=commands_replayed,
        split_events=split_events,
        split_successes=split_successes,
        split_failures=split_failures,
        split_syscall_attempts=split_syscall_attempts,
        split_max_attempts=split_max_attempts,
        split_total_wall_ms=split_total_wall_ms,
        split_max_wall_ms=split_max_wall_ms,
        split_queue_lag_ms_mean=split_queue_lag_ms_mean,
        split_queue_lag_ms_max=split_queue_lag_ms_max,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _invoke_break_page(
    client: redis.Redis,
    redis_pid: int,
    key: str,
    line_no: int,
    breakpoint: int,
) -> SplitPageResult:
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
    result = _break_page_for_pid(client, redis_pid, key)
    if result.error is not None:
        logger.warning(
            "line %d: break_page failed for key '%s' at access %d after %d attempts: pid=%d vaddr=0x%x errno=%d (%s)",
            line_no,
            key,
            breakpoint,
            result.attempts,
            redis_pid,
            result.vaddr,
            result.error.errno,
            result.error.strerror,
        )
    return result

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
    output: Path,
    runs: int = 3,
    collectors: tuple[str, ...] = (),
    break_dispatch: Literal["inline", "threaded", "mixed"] = "inline",
) -> None:
    """For each breakpoint combination: restore snapshot, then time the run.

    Calls :func:`setup_system` once before the loop to set THP to ``always``
    and silence background memory management noise. Any non-empty all-zero
    breakpoint row is treated as the ``base_pages`` sentinel: replay flips THP
    to ``never`` before restoring Redis for that row and replays it without
    replay-time split syscalls. Original host settings are restored
    unconditionally via :func:`teardown_system` in a ``finally`` block.

    Each combination is replayed *runs* times. The output Parquet file
    contains the breakpoint vector plus ``runtime_s_1``, ``runtime_s_2``,
    etc. columns — one per run.

    Args:
        snapshot_path: Path to ``snapshot.rdb`` from ``capture_redis_trace.sh``.
        run_trace: Path to the monitor_run.log file.
        breakpoints_df: Parquet-derived DataFrame; each row is one combo.
        output: Destination Parquet file for results.
        runs: Number of times to replay each breakpoint combination.
        collectors: Replay counters to collect for each timed run. Each
            configured counter adds ``<counter_name>_<run>`` columns to the
            results parquet.
        break_dispatch: Split-dispatch policy for timed runs. ``mixed`` means
            the first half of runs are inline and the second half are threaded.
    """
    redis_endpoint = load_repo_redis_endpoint()
    client = redis.Redis(
        host=redis_endpoint.host,
        port=redis_endpoint.port,
        decode_responses=True,
    )
    hw_collector = HardwareCollector()

    results: list[dict] = []
    n_combos = len(breakpoints_df)

    sys_config = setup_system()
    try:
        for idx, row in enumerate(breakpoints_df.iter_rows(named=True)):
            breakpoints: dict[str, int] = dict(row)
            row_result: dict = {**breakpoints}
            is_base_pages = idx == 0
            thp_mode = "never" if is_base_pages else "always"
            effective_breakpoints = {} if is_base_pages else breakpoints

            for run in range(1, runs + 1):
                effective_break_dispatch = _resolve_break_dispatch(
                    break_dispatch,
                    run_number=run,
                    runs=runs,
                )
                # 1. Restore snapshot
                logger.info(
                    "[%d/%d run %d/%d] Restoring snapshot (THP %s, break dispatch %s) ...",
                    idx + 1,
                    n_combos,
                    run,
                    runs,
                    thp_mode,
                    effective_break_dispatch,
                )
                _set_thp_enabled_mode(thp_mode)
                restore_snapshot(client, snapshot_path)

                # 2. Post-restore memory purge
                memory_purge(client)
                redis_tgid = _get_redis_pid(client)

                # 3. Timed run replay
                logger.info(
                    "[%d/%d run %d/%d] Timing run trace ...",
                    idx + 1,
                    n_combos,
                    run,
                    runs,
                )
                counters_started = False
                if collectors:
                    hw_collector.start_counters(list(collectors), redis_tgid)
                    counters_started = True

                t0 = time.perf_counter()
                replay_stats = replay(
                    run_trace,
                    client,
                    breakpoints=effective_breakpoints,
                    break_dispatch=effective_break_dispatch,
                )
                runtime_s = time.perf_counter() - t0

                counter_totals: dict[str, int] = {}
                if collectors and counters_started:
                    counter_totals = hw_collector.stop_counters(list(collectors))

                logger.info(
                    "[%d/%d run %d/%d] Done — %d commands in %.3fs",
                    idx + 1,
                    n_combos,
                    run,
                    runs,
                    replay_stats.commands_replayed,
                    runtime_s,
                )
                row_result["row_index"] = idx
                row_result["thp_mode"] = thp_mode
                row_result[f"break_dispatch_{run}"] = effective_break_dispatch
                row_result[f"runtime_s_{run}"] = runtime_s
                row_result[f"commands_replayed_{run}"] = replay_stats.commands_replayed
                row_result[f"split_events_{run}"] = replay_stats.split_events
                row_result[f"split_successes_{run}"] = replay_stats.split_successes
                row_result[f"split_failures_{run}"] = replay_stats.split_failures
                row_result[f"split_syscall_attempts_{run}"] = (
                    replay_stats.split_syscall_attempts
                )
                row_result[f"split_max_attempts_{run}"] = replay_stats.split_max_attempts
                row_result[f"split_total_wall_ms_{run}"] = (
                    replay_stats.split_total_wall_ms
                )
                row_result[f"split_max_wall_ms_{run}"] = (
                    replay_stats.split_max_wall_ms
                )
                row_result[f"split_queue_lag_ms_mean_{run}"] = (
                    replay_stats.split_queue_lag_ms_mean
                )
                row_result[f"split_queue_lag_ms_max_{run}"] = (
                    replay_stats.split_queue_lag_ms_max
                )
                for counter_name, total in counter_totals.items():
                    row_result[f"{counter_name}_{run}"] = total

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
        "--break-dispatch",
        choices=("inline", "threaded", "mixed"),
        default="inline",
        help=(
            "How replay should issue page splits. 'inline' breaks on the hot "
            "path, 'threaded' queues work to a background thread, and "
            "'mixed' uses inline for the first half of runs and threaded for "
            "the second half."
        ),
    )
    parser.add_argument(
        "--collector-config",
        type=Path,
        default=None,
        help=(
            "YAML file describing which replay counters to collect. Example: "
            "collectors: [dtlb_loads, dtlb_misses]. This currently requires "
            "running replay under sudo on the host."
        ),
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

    if args.collector_config is not None and not args.collector_config.is_file():
        parser.error(f"Collector config file not found: {args.collector_config}")
    if args.break_dispatch == "mixed" and args.runs % 2 != 0:
        parser.error("--break-dispatch mixed requires an even --runs value.")

    try:
        collector_names = _load_counter_config(args.collector_config)
    except RuntimeError as exc:
        parser.error(str(exc))

    if collector_names:
        try:
            _require_root_for_counter_collection(collector_names)
        except PermissionError as exc:
            parser.error(str(exc))

    snapshot_path: Path = args.snapshot
    run_trace: Path = args.run_trace

    for p in (snapshot_path, run_trace):
        if not p.is_file():
            parser.error(f"File not found: {p}")

    try:
        load_repo_redis_endpoint()
    except RuntimeError as exc:
        parser.error(str(exc))

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
        output=args.output,
        runs=args.runs,
        collectors=collector_names,
        break_dispatch=args.break_dispatch,
    )


if __name__ == "__main__":
    main()
