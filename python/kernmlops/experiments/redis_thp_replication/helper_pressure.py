"""Bounded helper-memory pressure for Redis runtime experiments."""

from __future__ import annotations

import argparse
import mmap
import os
import random
import time
from pathlib import Path


PAGE_SIZE = 4096


def _touch_mapping(mapping: mmap.mmap, length: int) -> None:
    """Fault each page into memory so the helper creates real pressure."""

    for offset in range(0, length, PAGE_SIZE):
        mapping[offset : offset + 1] = b"\0"


def _set_affinity(cpu: int | None) -> None:
    """Pin the helper to one CPU when the platform supports affinity."""

    if cpu is None or not hasattr(os, "sched_setaffinity"):
        return
    os.sched_setaffinity(0, {cpu})


def run_pressure(
    *,
    duration_seconds: float,
    target_bytes: int,
    chunk_min_bytes: int,
    chunk_max_bytes: int,
    seed: int,
    cpu: int | None,
    log_path: Path | None,
) -> None:
    """Allocate and free anonymous mappings around the 2 MiB boundary.

    The helper keeps a bounded pool of mmaps whose sizes vary around the THP
    size. This is intentionally simple: the goal is to create moderate external
    allocator churn without relying on Redis fork paths or persistence spikes.
    """

    _set_affinity(cpu)
    rng = random.Random(seed)
    deadline = time.monotonic() + duration_seconds
    mappings: list[tuple[mmap.mmap, int]] = []
    resident_bytes = 0

    log_file = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("w", encoding="utf-8")
        log_file.write(
            "helper pressure starting "
            f"duration_seconds={duration_seconds} "
            f"target_bytes={target_bytes} "
            f"chunk_min_bytes={chunk_min_bytes} "
            f"chunk_max_bytes={chunk_max_bytes} "
            f"cpu={cpu}\n"
        )
        log_file.flush()

    try:
        while time.monotonic() < deadline:
            allocate = resident_bytes < target_bytes or not mappings
            if allocate:
                size = rng.randint(chunk_min_bytes, chunk_max_bytes)
                mapping = mmap.mmap(-1, size)
                _touch_mapping(mapping, size)
                mappings.append((mapping, size))
                resident_bytes += size
                if log_file is not None:
                    log_file.write(f"alloc size={size} resident_bytes={resident_bytes}\n")
            else:
                drop_count = max(1, len(mappings) // 8)
                for _ in range(drop_count):
                    index = rng.randrange(len(mappings))
                    mapping, size = mappings.pop(index)
                    mapping.close()
                    resident_bytes -= size
                if log_file is not None:
                    log_file.write(f"free resident_bytes={resident_bytes}\n")
            if log_file is not None:
                log_file.flush()
            time.sleep(0.05)
    finally:
        for mapping, _ in mappings:
            mapping.close()
        if log_file is not None:
            log_file.write("helper pressure finished\n")
            log_file.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--target-bytes", type=int, required=True)
    parser.add_argument("--chunk-min-bytes", type=int, required=True)
    parser.add_argument("--chunk-max-bytes", type=int, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cpu", type=int, default=None)
    parser.add_argument("--log-path", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the helper from the command line."""

    args = _build_parser().parse_args(argv)
    run_pressure(
        duration_seconds=args.duration_seconds,
        target_bytes=args.target_bytes,
        chunk_min_bytes=args.chunk_min_bytes,
        chunk_max_bytes=args.chunk_max_bytes,
        seed=args.seed,
        cpu=args.cpu,
        log_path=args.log_path,
    )


if __name__ == "__main__":
    main()
