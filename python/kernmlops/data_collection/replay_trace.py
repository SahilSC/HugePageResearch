"""Replay captured redis-cli MONITOR logs against a live Redis instance.

Pipeline overview::

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
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl
import redis

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

BREAK_PAGE_MAX_ATTEMPTS: int = 2
EXECUTE_MAX_ATTEMPTS: int = 2

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
# Break-page placeholder
# ---------------------------------------------------------------------------


def break_page(key: str) -> bool:
    """Placeholder for future page-fault / memory-pressure logic.

    This function will eventually cause a page fault on the memory page
    backing *key*'s data inside Redis. For now it is a no-op stub.

    Args:
        key: The Redis key whose backing page should be broken.

    Returns:
        ``False`` unconditionally. A real implementation returns ``True``
        on success.
    """
    # TODO add syscall and return status
    return False


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

    print("Setting up system configuration ...", file=sys.stderr)
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
    print("System configuration applied.", file=sys.stderr)

    return config


def teardown_system(config: SystemConfig) -> None:
    """Restore kernel tunables to the values saved by :func:`setup_system`.

    Args:
        config: The ``SystemConfig`` returned by :func:`setup_system`.
    """
    print("Restoring system configuration ...", file=sys.stderr)

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

    print("System configuration restored.", file=sys.stderr)


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

    Copies *snapshot_path* over Redis's configured RDB file using ``sudo``,
    then restarts the Redis service so it loads from that file on startup.
    Blocks until Redis is reachable again before returning.

    Args:
        client: An active Redis connection.
        snapshot_path: Path to the ``snapshot.rdb`` file produced by
            ``capture_redis_trace.sh``.
    """
    rdb_dir = client.config_get("dir")["dir"]
    rdb_file = client.config_get("dbfilename")["dbfilename"]
    rdb_path = Path(rdb_dir) / rdb_file
    subprocess.run(["sudo", "cp", str(snapshot_path), str(rdb_path)], check=True)
    subprocess.run(["sudo", "systemctl", "restart", "redis"], check=True)
    _wait_for_redis(client)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def replay(
    trace_path: Path,
    client: redis.Redis | None,
    breakpoints: dict[str, int] | None = None,
) -> int:
    """Replay a redis-cli MONITOR log against a live Redis instance.

    Args:
        trace_path: Path to the redis-cli monitor log file.
        client: Active Redis connection, or ``None`` for a dry run.
        breakpoints: Optional ``{key: access_num}`` mapping. ``None``
            disables breakpoint checking.

    Returns:
        Total number of commands replayed.
    """
    if breakpoints is None:
        breakpoints = {}

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
                    _invoke_break_page(key, line_no, breakpoints[key])

            # --- Execute against Redis ------------------------------------
            if _execute(client, cmd, line_no) and key:
                access_counts[key] += 1

    return sum(access_counts.values())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _invoke_break_page(key: str, line_no: int, breakpoint: int) -> None:
    """Call ``break_page`` with retry logic.

    Retries up to ``BREAK_PAGE_MAX_ATTEMPTS`` times total. Prints a
    warning to stderr if all attempts fail.

    Args:
        key: The Redis key to break.
        line_no: Current line number in the trace (for diagnostics).
        breakpoint: The access number at which the break was triggered.
    """
    for _ in range(BREAK_PAGE_MAX_ATTEMPTS):
        if break_page(key):
            return

    print(
        f"WARNING (line {line_no}): break_page failed for key "
        f"'{key}' at access {breakpoint} after {BREAK_PAGE_MAX_ATTEMPTS} attempts; continuing.",
        file=sys.stderr,
    )


def _execute(
    client: redis.Redis | None,
    cmd: RedisCommand,
    line_no: int,
) -> bool:
    """Execute a single Redis command, retrying once on failure.

    Args:
        client: An active ``redis.Redis`` connection, or ``None`` for dry run.
        cmd: The parsed command to execute.
        line_no: Current line number in the trace (for diagnostics).

    Returns:
        ``True`` if the command succeeded (or dry-run), ``False`` otherwise.
    """
    if client is None:
        return True

    for attempt in range(EXECUTE_MAX_ATTEMPTS):
        try:
            client.execute_command(cmd.command, *cmd.args)
            return True
        except redis.RedisError as exc:
            if attempt == EXECUTE_MAX_ATTEMPTS - 1:
                print(
                    f"WARNING (line {line_no}): Redis error on "
                    f"{cmd.command} '{cmd.key}': {exc}",
                    file=sys.stderr,
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
    client = redis.Redis(host=host, port=port)

    results: list[dict] = []
    n_combos = len(breakpoints_df)

    sys_config = setup_system()
    try:
        for idx, row in enumerate(breakpoints_df.iter_rows(named=True)):
            breakpoints: dict[str, int] = dict(row)
            row_result: dict = {**breakpoints}

            for run in range(1, runs + 1):
                # 1. Restore snapshot
                print(
                    f"[{idx + 1}/{n_combos} run {run}/{runs}] Restoring snapshot ...",
                    file=sys.stderr,
                )
                restore_snapshot(client, snapshot_path)

                # 2. Post-restore memory purge
                memory_purge(client)

                # 3. Timed run replay
                print(
                    f"[{idx + 1}/{n_combos} run {run}/{runs}] Timing run trace ...",
                    file=sys.stderr,
                )
                t0 = time.perf_counter()
                total_cmds = replay(run_trace, client, breakpoints=breakpoints)
                runtime_s = time.perf_counter() - t0

                print(
                    f"[{idx + 1}/{n_combos} run {run}/{runs}] Done — "
                    f"{total_cmds} commands in {runtime_s:.3f}s",
                    file=sys.stderr,
                )
                row_result[f"runtime_s_{run}"] = runtime_s

            results.append(row_result)

        output.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(results).write_parquet(output)
        print(f"Results written to {output}", file=sys.stderr)
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
        default=None,
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
    return parser


def main() -> None:
    """Entry point for the replay_trace CLI."""
    parser = _build_parser()
    args = parser.parse_args()

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
