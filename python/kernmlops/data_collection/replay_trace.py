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
) -> None:
    """For each breakpoint combination: restore snapshot, then time the run.

    Steps per combination:

    1. Restore the RDB snapshot — exact key-value layout from the load phase.
    2. ``MEMORY PURGE`` — reset allocator state.
    3. Time the replay of *run_trace* with this combination's breakpoints.

    Results are written to *output* as a Parquet file. Each row contains
    the breakpoint vector for that combination plus a ``runtime_s`` column
    with the measured wall-clock duration of the run replay.

    Args:
        snapshot_path: Path to ``snapshot.rdb`` from ``capture_redis_trace.sh``.
        run_trace: Path to the monitor_run.log file.
        breakpoints_df: Parquet-derived DataFrame; each row is one combo.
        host: Redis server hostname or IP.
        port: Redis server port.
        output: Destination Parquet file for results.
    """
    client = redis.Redis(host=host, port=port)

    results: list[dict] = []
    n_combos = len(breakpoints_df)

    for idx, row in enumerate(breakpoints_df.iter_rows(named=True)):
        breakpoints: dict[str, int] = dict(row)

        # 1. Restore snapshot
        print(f"[{idx + 1}/{n_combos}] Restoring snapshot ...", file=sys.stderr)
        restore_snapshot(client, snapshot_path)

        # 2. Post-restore memory purge
        memory_purge(client)

        # 3. Timed run replay
        print(f"[{idx + 1}/{n_combos}] Timing run trace ...", file=sys.stderr)
        t0 = time.perf_counter()
        total_cmds = replay(run_trace, client, breakpoints=breakpoints)
        runtime_s = time.perf_counter() - t0

        print(
            f"[{idx + 1}/{n_combos}] Done — {total_cmds} commands in {runtime_s:.3f}s",
            file=sys.stderr,
        )
        results.append({**breakpoints, "runtime_s": runtime_s})

    output.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(results).write_parquet(output)
    print(f"Results written to {output}", file=sys.stderr)


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
    )


if __name__ == "__main__":
    main()
