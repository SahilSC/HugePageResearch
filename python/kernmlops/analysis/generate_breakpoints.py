"""Generate replay-time breakpoint configurations from a redis-cli monitor log.

Parses a redis-cli MONITOR log to count accesses for Redis keys that are
valid replay-time split targets, then produces a single Parquet file
where each row is one breakpoint combination and each column is a YCSB
user-record key.

The current replay/VAPTR flow breaks only top-level ``user*`` hash keys
via ``VAPTR FIELD field0 <key>``. Redis bookkeeping commands such as
``ZREM "_indices" "user123"`` are still replayed later, but
``_indices`` is not written to the breakpoint parquet because it is not
itself a valid split target.

Usage::

    python generate_breakpoints.py <monitor_log> [--output data/breakpoints.parquet] [--max-combos 10]
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from collections import Counter
from pathlib import Path

import polars as pl

# Matches a timestamped redis-cli monitor line:
#   <timestamp> [<db> <client>] "CMD" "arg1" ...
_LOG_LINE_RE: re.Pattern[str] = re.compile(r"^\d+\.\d+\s+\[.+?\]\s+")

# Extracts all double-quoted tokens from the command portion.
_QUOTED_TOKEN_RE: re.Pattern[str] = re.compile(r'"((?:[^"\\]|\\.)*)"')
_REPLAY_SPLIT_TARGET_PREFIX = "user"


def _is_replay_split_target(key: str) -> bool:
    """Return whether *key* is a valid replay-time split target.

    The current replay flow breaks only YCSB user-record hashes. Redis
    bookkeeping keys such as ``_indices`` still appear in the MONITOR
    log and are replayed, but they are not valid inputs to
    ``VAPTR FIELD field0 <key>``.

    Args:
        key: Primary Redis key from a parsed MONITOR command.

    Returns:
        ``True`` when *key* names a YCSB user record, else ``False``.
    """
    return key.startswith(_REPLAY_SPLIT_TARGET_PREFIX)


def parse_log(log_path: Path) -> dict[str, int]:
    """Parse a redis-cli MONITOR log and count accesses per split target.

    The parser counts only monitor lines whose primary Redis key is a
    valid replay-time split target. In this repo that means top-level
    ``user*`` YCSB record hashes, not bookkeeping keys such as
    ``_indices``.

    Example output:
        {"user123": 7, "user456": 2}

    Args:
        log_path: Path to the redis-cli monitor log file.

    Returns:
        A mapping ``{key: access_count}`` for every valid split target
        observed as the primary Redis key in the MONITOR log.

    Raises:
        FileNotFoundError: If *log_path* does not exist.
    """
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")

    counts: Counter[str] = Counter()

    with open(log_path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("OK"):
                continue
            if not _LOG_LINE_RE.match(line):
                continue

            tokens = _QUOTED_TOKEN_RE.findall(line)
            # Need at least a command token and one key argument.
            if len(tokens) < 2:
                continue

            key = tokens[1]
            if _is_replay_split_target(key):
                counts[key] += 1

    return dict(counts)


def make_breakpoints(access_counts: dict[str, int]) -> dict[str, int]:
    """Create an initial breakpoint mapping with all keys set to zero.

    Args:
        access_counts: Per-key access counts as returned by
            :func:`parse_log`.

    Returns:
        A new dict ``{key: 0}`` for every key in *access_counts*.
    """
    return {key: 0 for key in access_counts}


def set_breakpoint(
    breakpoints: dict[str, int],
    key: str,
    access_num: int,
    access_counts: dict[str, int],
) -> dict[str, int]:
    """Return a copy of *breakpoints* with one key's value updated.

    Args:
        breakpoints: Current breakpoint mapping.
        key: The Redis key to update.
        access_num: The access number at which to break. Valid range
            is ``[0, access_counts[key] + 1]`` inclusive. A value of
            ``access_counts[key] + 1`` means the page is never broken.
        access_counts: Per-key access counts used for validation.

    Returns:
        A shallow copy of *breakpoints* with the requested change applied.

    Raises:
        KeyError: If *key* is not present in *access_counts*.
        ValueError: If *access_num* is outside ``[0, access_counts[key] + 1]``.
    """
    if key not in access_counts:
        raise KeyError(f"Key {key!r} not found in access counts")

    max_access = access_counts[key] + 1
    if not (0 <= access_num <= max_access):
        raise ValueError(
            f"access_num must be in [0, {max_access}] for key {key!r}, got {access_num}"
        )

    updated = breakpoints.copy()
    updated[key] = access_num
    return updated


def generate_combinations(
    access_counts: dict[str, int],
    max_combos: int = 10,
) -> list[dict[str, int]]:
    """Generate a representative sample of breakpoint combinations.

    Strategy:

    1. All-(-1) combination (every key broken before the trace begins).
    2. All-zeros baseline (every key at breakpoint 0).
    3. All-max combination (every key at ``access_counts[key] + 1`` —
       page never broken for any key).
    4. Single-key-at-max variants (each key individually set to its
       maximum; all others at 0).
    5. Random combinations to fill remaining slots, where each key is
       independently sampled from ``[0, access_counts[key] + 1]``.

    Duplicate combinations are deduplicated.

    Args:
        access_counts: Per-key access counts.
        max_combos: Upper bound on the number of combinations to return.

    Returns:
        A list of breakpoint dicts, each mapping every key to a valid
        access number.
    """
    if not access_counts:
        return [{}]

    keys = sorted(access_counts.keys())
    seen: set[tuple[int, ...]] = set()
    combos: list[dict[str, int]] = []

    def _add(values: tuple[int, ...]) -> None:
        if values not in seen and len(combos) < max_combos:
            seen.add(values)
            combos.append(dict(zip(keys, values)))

    # 1. All-(-1) combination (every key broken before the trace begins).
    _add(tuple(-1 for _ in keys))

    # 2. All-zeros baseline.
    _add(tuple(0 for _ in keys))

    # 3. All-max combination (every key at access_count + 1 — page never broken).
    _add(tuple(access_counts[k] + 1 for k in keys))

    # 4. Single-key-at-max variants (one key at max, others at 0).
    for i, key in enumerate(keys):
        if len(combos) >= max_combos:
            break
        values = [0] * len(keys)
        values[i] = access_counts[key] + 1
        _add(tuple(values))

    # 5. Random combinations for remaining slots.
    max_attempts = max_combos * 10
    for _ in range(max_attempts):
        if len(combos) >= max_combos:
            break
        values = tuple(random.randint(0, access_counts[k] + 1) for k in keys)
        _add(values)

    return combos


def write_parquet(combos: list[dict[str, int]], output_path: Path) -> None:
    """Write breakpoint combinations to a Parquet file.

    Each row represents one combination; each column is a replay-time
    split target whose value is the access number at which to break
    (0 = no break).

    Args:
        combos: List of breakpoint dicts as returned by
            :func:`generate_combinations`.
        output_path: Destination ``.parquet`` file path.
    """
    df = pl.DataFrame(
        combos, schema={k: pl.Int64 for k in (combos[0] if combos else {})}
    )
    df.write_parquet(output_path)


def _print_summary(access_counts: dict[str, int]) -> None:
    """Print human-readable summary statistics to stderr.

    Args:
        access_counts: Per-key access counts.
    """
    if not access_counts:
        print("No replay-time split targets found in log.", file=sys.stderr)
        return

    total_keys = len(access_counts)
    total_accesses = sum(access_counts.values())
    min_accesses = min(access_counts.values())
    max_accesses = max(access_counts.values())

    print(
        f"Unique split targets: {total_keys}\n"
        f"Total accesses:   {total_accesses}\n"
        f"Min accesses/key: {min_accesses}\n"
        f"Max accesses/key: {max_accesses}",
        file=sys.stderr,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Parse a redis-cli monitor log and generate a Parquet file of "
            "replay-time split targets."
        ),
    )
    parser.add_argument(
        "monitor_log",
        type=Path,
        help="Path to the redis-cli monitor log file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/breakpoints.parquet"),
        help=(
            "Output Parquet file path for replay-time split targets "
            "(default: data/breakpoints.parquet)."
        ),
    )
    parser.add_argument(
        "--max-combos",
        type=int,
        default=10,
        help="Maximum number of breakpoint combinations to generate (default: 10).",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Entry point for the generate_breakpoints CLI.

    Args:
        argv: Command-line arguments. Defaults to ``sys.argv[1:]``.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    log_path: Path = args.monitor_log
    output_path: Path = args.output

    access_counts = parse_log(log_path)
    _print_summary(access_counts)

    combos = generate_combinations(access_counts, max_combos=args.max_combos)
    write_parquet(combos, output_path)

    print(
        f"Wrote {len(combos)} breakpoint combinations to {output_path}", file=sys.stderr
    )


if __name__ == "__main__":
    main()
