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

    python python/kernmlops/replay/generate_breakpoints.py <monitor_log> \
        [--output data/breakpoints.parquet] \
        [--hot-keys 10] \
        [--random-rows 3]
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
    """Create the ``base_pages`` sentinel mapping with all keys set to zero.

    Args:
        access_counts: Per-key access counts as returned by
            :func:`parse_log`.

    Returns:
        A new dict ``{key: 0}`` for every key in *access_counts*.

    Note:
        The all-zero row is interpreted by replay as the ``base_pages``
        sentinel. Replay disables THP before restoring Redis for that row
        instead of issuing replay-time split syscalls.
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


def _hottest_keys(
    access_counts: dict[str, int],
) -> list[str]:
    """Return keys ordered by access count (hottest first).

    Args:
        access_counts: Per-key access counts from :func:`parse_log`.

    Returns:
        Keys sorted by descending access count, ties broken alphabetically.
    """
    return [
        key
        for key, _count in sorted(
            access_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def generate_combinations(
    access_counts: dict[str, int],
    hot_keys: int = 10,
    random_rows: int = 3,
) -> list[dict[str, int]]:
    """Generate replay breakpoint combinations for the current hot-key study.

    Produces:

    1. All-zeros ``base_pages`` sentinel row.
    2. All-max combination (every key at ``access_counts[key] + 1`` —
       page never broken for any key).
    3. Up to *hot_keys* split-only rows (hottest first): split only that
       key before its first access, leave all others unsplit.
    4. *random_rows* random split-only rows chosen from the remaining keys
       that were not already used for the hottest rows.

    The key ordering is currently hardcoded to hottest-first. A future
    version may accept a policy parameter to change the ordering (e.g.
    coldest-first).

    Args:
        access_counts: Per-key access counts.
        hot_keys: Number of split-only hot-key rows to generate.
        random_rows: Number of random combination rows to generate.

    Returns:
        A list of breakpoint dicts, each mapping every key to a valid
        access number.
    """
    if not access_counts:
        return [{}]

    # Hardcoded policy: hottest keys first.
    keys = _hottest_keys(access_counts)
    key_to_index = {key: i for i, key in enumerate(keys)}
    seen: set[tuple[int, ...]] = set()
    combos: list[dict[str, int]] = []

    def _add(values: tuple[int, ...]) -> bool:
        if values not in seen:
            seen.add(values)
            combos.append(dict(zip(keys, values)))
            return True
        return False

    # 1. All-zeros base_pages sentinel row.
    _add(tuple(0 for _ in keys))

    # 2. All-max combination (every key at access_count + 1 — page never broken).
    _add(tuple(access_counts[k] + 1 for k in keys))

    # 3. Split-only hot-key rows.
    never_break_values = [access_counts[k] + 1 for k in keys]
    added_hot = 0
    for key in keys:
        if added_hot >= hot_keys:
            break
        values = list(never_break_values)
        values[key_to_index[key]] = 0
        if _add(tuple(values)):
            added_hot += 1

    # 4. Random split-only rows from the remaining keys.
    shuffled_keys = random.sample(keys, len(keys))
    added_random = 0
    for key in shuffled_keys:
        if added_random >= random_rows:
            break
        values = list(never_break_values)
        values[key_to_index[key]] = 0
        if _add(tuple(values)):
            added_random += 1

    return combos


def write_parquet(combos: list[dict[str, int]], output_path: Path) -> None:
    """Write breakpoint combinations to a Parquet file.

    Each row represents one combination; each column is a replay-time
    split target whose value is the access number at which to break.
    An all-zero row is the ``base_pages`` sentinel, while
    ``access_counts[key] + 1`` means never split that key.

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
            "replay-time split targets. The first generated row is the "
            "all-zero base_pages sentinel, the second row is no_break."
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
        "--hot-keys",
        type=int,
        default=10,
        help="Number of split-only hot-key rows to generate (default: 10).",
    )
    parser.add_argument(
        "--random-rows",
        type=int,
        default=3,
        help="Number of random combination rows to generate (default: 3).",
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

    combos = generate_combinations(
        access_counts,
        hot_keys=args.hot_keys,
        random_rows=args.random_rows,
    )
    write_parquet(combos, output_path)

    print(
        f"Wrote {len(combos)} breakpoint combinations to {output_path}", file=sys.stderr
    )


if __name__ == "__main__":
    main()
