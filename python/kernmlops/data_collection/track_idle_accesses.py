#!/usr/bin/env python3
"""
Track per-page access frequency for redis-server using idle page tracking.

Mechanism:
  1. Walk /proc/<pid>/pagemap to find present physical pages (PFNs).
  2. Mark those PFNs as idle via /sys/kernel/mm/page_idle/bitmap.
     This sets PG_idle on each page and clears the hardware PTE Accessed bit,
     so subsequent accesses are detectable.
  3. Sleep for --interval seconds (default: 0).
  4. Read back the idle bitmap: a bit of 0 means the page was accessed
     (the hardware Accessed bit fired, which clears PG_idle).
  5. Accumulate access counts per PFN across polls.

Requires root privileges and CONFIG_IDLE_PAGE_TRACKING=y in the kernel.
  Check: test -f /sys/kernel/mm/page_idle/bitmap

Usage:
    sudo .venv/bin/python   scripts/track_idle_accesses.py ...   # CPython + pyarrow → .parquet
    sudo .pypy-venv/bin/python scripts/track_idle_accesses.py ...  # PyPy → .csv.gz fallback
"""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import signal
import struct
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    _HAS_PYARROW = True
except ImportError:
    _HAS_PYARROW = False

PAGE_SIZE = 4096
PAGEMAP_ENTRY_SIZE = 8  # uint64

# /proc/<pid>/pagemap entry bits
PM_PRESENT = 1 << 63
PM_PFN_MASK = (1 << 55) - 1  # bits 0-54

# /proc/kpageflags bits — used only for THP/HUGE metadata, not access tracking
KPF_HUGE = 1 << 17
KPF_THP = 1 << 22

# Idle page bitmap
IDLE_BITMAP_PATH = "/sys/kernel/mm/page_idle/bitmap"
WORD_BITS = 64
WORD_BYTES = 8  # each entry is a uint64


def find_redis_pid() -> int:
    """Return the PID of a running redis-server, or raise RuntimeError."""
    try:
        out = subprocess.check_output(["pidof", "redis-server"], text=True).strip()
        pids = out.split()
        if len(pids) > 1:
            print(f"[warn] multiple redis-server PIDs found: {pids}; using {pids[0]}")
        return int(pids[0])
    except subprocess.CalledProcessError:
        raise RuntimeError("redis-server not found; is it running?")


def get_vma_ranges(pid: int) -> list[tuple[int, int, str]]:
    """Return (start, end, name) for each VMA, skipping vsyscall."""
    regions = []
    maps_path = Path(f"/proc/{pid}/maps")
    try:
        for line in maps_path.read_text().splitlines():
            parts = line.split()
            if not parts:
                continue
            addr_range = parts[0]
            name = parts[-1] if len(parts) >= 6 else ""
            if "[vsyscall]" in name:
                continue
            start_hex, end_hex = addr_range.split("-")
            regions.append((int(start_hex, 16), int(end_hex, 16), name))
    except PermissionError:
        raise RuntimeError(f"Cannot read /proc/{pid}/maps — run as root?")
    return regions


def _parse_pagemap_region(
    raw: bytes, start: int, name: str, pfn_map: dict[int, tuple[int, str]]
) -> None:
    """Parse a pagemap region buffer and populate pfn_map with present pages."""
    n = len(raw) // PAGEMAP_ENTRY_SIZE
    if n == 0:
        return
    # Bulk-unpack all uint64 entries in one C call; PyPy JIT optimises the loop.
    entries = struct.unpack_from(f"<{n}Q", raw)
    for i, entry in enumerate(entries):
        if entry >> 63:  # PM_PRESENT
            pfn_map[entry & PM_PFN_MASK] = (start + i * PAGE_SIZE, name)


def collect_present_pfns_fd(
    pagemap_fd: int,
    regions: list[tuple[int, int, str]],
) -> dict[int, tuple[int, str]]:
    """Walk pagemap using an already-open fd; return {pfn: (vaddr, vma_name)}."""
    pfn_map: dict[int, tuple[int, str]] = {}
    for start, end, name in regions:
        if start >= end:
            continue
        n_pages = (end - start) // PAGE_SIZE
        offset = (start // PAGE_SIZE) * PAGEMAP_ENTRY_SIZE
        os.lseek(pagemap_fd, offset, os.SEEK_SET)
        try:
            raw = os.read(pagemap_fd, n_pages * PAGEMAP_ENTRY_SIZE)
        except OSError:
            continue
        _parse_pagemap_region(raw, start, name, pfn_map)
    return pfn_map


def collect_present_pfns(
    pid: int,
    regions: list[tuple[int, int, str]],
) -> dict[int, tuple[int, str]]:
    """Walk pagemap; return {pfn: (vaddr, vma_name)} for all present pages."""
    pagemap_path = f"/proc/{pid}/pagemap"
    try:
        pagemap_fd = os.open(pagemap_path, os.O_RDONLY)
    except PermissionError:
        raise RuntimeError(f"Cannot open {pagemap_path} — run as root?")
    except FileNotFoundError:
        raise RuntimeError(f"Process {pid} died")
    try:
        return collect_present_pfns_fd(pagemap_fd, regions)
    finally:
        os.close(pagemap_fd)


def _group_pfns_by_word(pfns: list[int]) -> tuple[list[int], dict[int, list[int]]]:
    """
    Return (sorted_word_indices, word_idx -> [pfns]) for a list of PFNs.
    Used to build batched bitmap reads/writes.
    """
    word_to_pfns: dict[int, list[int]] = {}
    for pfn in pfns:
        word_to_pfns.setdefault(pfn >> 6, []).append(pfn)
    return sorted(word_to_pfns), word_to_pfns


def mark_pfns_idle(idle_fd: int, pfns: list[int]) -> None:
    """
    Set the idle bit for each PFN in /sys/kernel/mm/page_idle/bitmap.
    The kernel clears PG_idle (and the bit) when the page is next accessed,
    making subsequent check_pfns_accessed calls detect the access.
    Consecutive bitmap words are batched into a single pwrite.
    """
    if not pfns:
        return

    word_to_mask: dict[int, int] = {}
    for pfn in pfns:
        wi = pfn >> 6  # pfn // WORD_BITS
        word_to_mask[wi] = word_to_mask.get(wi, 0) | (1 << (pfn & 63))

    sorted_words = sorted(word_to_mask)
    i = 0
    while i < len(sorted_words):
        run_start = sorted_words[i]
        j = i
        while j + 1 < len(sorted_words) and sorted_words[j + 1] == sorted_words[j] + 1:
            j += 1
        run_len = j - i + 1
        buf = struct.pack(
            f"<{run_len}Q",
            *[word_to_mask[sorted_words[i + k]] for k in range(run_len)],
        )
        try:
            os.pwrite(idle_fd, buf, run_start * WORD_BYTES)
        except OSError:
            pass
        i = j + 1


def check_pfns_accessed(idle_fd: int, pfns: list[int]) -> set[int]:
    """
    Read the idle bitmap for pfns; return those whose bit is 0.
    A cleared bit means the page was accessed since it was last marked idle.
    Consecutive bitmap words are batched into a single pread.
    """
    if not pfns:
        return set()

    sorted_words, word_to_pfns = _group_pfns_by_word(pfns)
    accessed: set[int] = set()
    i = 0
    while i < len(sorted_words):
        run_start = sorted_words[i]
        j = i
        while j + 1 < len(sorted_words) and sorted_words[j + 1] == sorted_words[j] + 1:
            j += 1
        run_len = j - i + 1
        try:
            raw = os.pread(idle_fd, run_len * WORD_BYTES, run_start * WORD_BYTES)
        except OSError:
            i = j + 1
            continue
        actual = len(raw) // WORD_BYTES
        if actual == 0:
            i = j + 1
            continue
        words = struct.unpack_from(f"<{actual}Q", raw)
        for k, word in enumerate(words):
            for pfn in word_to_pfns[sorted_words[i + k]]:
                if not (word & (1 << (pfn & 63))):
                    accessed.add(pfn)
        i = j + 1
    return accessed


def poll_once(
    idle_fd: int,
    access_counts: dict[int, int],
    pfn_meta: dict[int, tuple[int, str]],
    interval: float,
) -> tuple[int, int]:
    """
    Mark all known PFNs idle, wait interval seconds, then record which were
    accessed.  Returns (n_accessed, n_total).
    """
    pfns = list(pfn_meta.keys())
    mark_pfns_idle(idle_fd, pfns)
    if interval > 0:
        time.sleep(interval)
    accessed = check_pfns_accessed(idle_fd, pfns)
    for pfn in accessed:
        access_counts[pfn] += 1
    return len(accessed), len(pfns)


def read_kpageflags_batch(kpf_fd: int, pfns: list[int]) -> dict[int, int]:
    """Read /proc/kpageflags for a list of PFNs; returns {pfn: flags}."""
    if not pfns:
        return {}
    result: dict[int, int] = {}
    pfns = sorted(pfns)
    i = 0
    while i < len(pfns):
        run_start = pfns[i]
        j = i
        while j + 1 < len(pfns) and pfns[j + 1] == pfns[j] + 1:
            j += 1
        run_len = j - i + 1
        try:
            raw = os.pread(
                kpf_fd, run_len * PAGEMAP_ENTRY_SIZE, run_start * PAGEMAP_ENTRY_SIZE
            )
        except OSError:
            i = j + 1
            continue
        actual = len(raw) // PAGEMAP_ENTRY_SIZE
        if actual:
            flags_vals = struct.unpack_from(f"<{actual}Q", raw)
            for k, flags in enumerate(flags_vals):
                result[run_start + k] = flags
        i = j + 1
    return result


def print_top(
    access_counts: dict[int, int],
    pfn_meta: dict[int, tuple[int, str]],
    kpf_fd: int,
    top_n: int,
    elapsed: float,
) -> None:
    top = sorted(access_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
    flags_map = read_kpageflags_batch(kpf_fd, [pfn for pfn, _ in top])

    print(f"\n{'─' * 80}")
    print(
        f"{'PFN':>12}  {'PhysAddr':>16}  {'Accesses':>9}  {'Acc/s':>7}  {'Flags':12}  VMA"
    )
    print(f"{'─' * 80}")
    for pfn, count in top:
        vaddr, name = pfn_meta.get(pfn, (0, "?"))
        flags = flags_map.get(pfn, 0)
        flag_str = ("HUGE " if flags & KPF_HUGE else "") + (
            "THP " if flags & KPF_THP else ""
        )
        rate = count / elapsed if elapsed > 0 else 0.0
        print(
            f"  {pfn:>12x}  {pfn * PAGE_SIZE:>16x}  {count:>9d}  {rate:>7.2f}"
            f"  {flag_str:<12}  {name}"
        )


def _output_path(requested: str) -> str:
    """Return the actual output path, switching to .csv.gz if pyarrow is unavailable."""
    if not _HAS_PYARROW and requested.endswith(".parquet"):
        path = requested[:-8] + ".csv.gz"
        print(f"[info] pyarrow not available; output will be {path}")
        return path
    return requested


def dump_parquet(
    path: str,
    access_counts: dict[int, int],
    pfn_meta: dict[int, tuple[int, str]],
    kpf_fd: int,
    n_polls: int,
    elapsed: float,
) -> None:
    flags_map = read_kpageflags_batch(kpf_fd, list(pfn_meta.keys()))
    pfns, phys_addrs, vaddrs, vma_names, accesses, is_huge, is_thp = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for pfn, (vaddr, name) in sorted(pfn_meta.items()):
        flags = flags_map.get(pfn, 0)
        pfns.append(pfn)
        phys_addrs.append(pfn * PAGE_SIZE)
        vaddrs.append(vaddr)
        vma_names.append(name)
        accesses.append(access_counts.get(pfn, 0))
        is_huge.append(bool(flags & KPF_HUGE))
        is_thp.append(bool(flags & KPF_THP))
    table = pa.table(
        {
            "pfn": pa.array(pfns, type=pa.uint64()),
            "phys_addr": pa.array(phys_addrs, type=pa.uint64()),
            "vaddr": pa.array(vaddrs, type=pa.uint64()),
            "vma_name": pa.array(vma_names, type=pa.string()),
            "accesses": pa.array(accesses, type=pa.uint32()),
            "n_polls": pa.array([n_polls] * len(pfns), type=pa.uint32()),
            "elapsed_s": pa.array([elapsed] * len(pfns), type=pa.float32()),
            "is_huge": pa.array(is_huge, type=pa.bool_()),
            "is_thp": pa.array(is_thp, type=pa.bool_()),
        }
    )
    pq.write_table(table, path, compression="zstd")
    print(f"[info] Parquet written to {path}  ({len(pfn_meta)} pages)")


def dump_csv_gz(
    path: str,
    access_counts: dict[int, int],
    pfn_meta: dict[int, tuple[int, str]],
    kpf_fd: int,
    n_polls: int,
    elapsed: float,
) -> None:
    flags_map = read_kpageflags_batch(kpf_fd, list(pfn_meta.keys()))
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "wt", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "pfn",
                "phys_addr",
                "vaddr",
                "vma_name",
                "accesses",
                "n_polls",
                "elapsed_s",
                "is_huge",
                "is_thp",
            ]
        )
        for pfn, (vaddr, name) in sorted(pfn_meta.items()):
            flags = flags_map.get(pfn, 0)
            writer.writerow(
                [
                    pfn,
                    pfn * PAGE_SIZE,
                    vaddr,
                    name,
                    access_counts.get(pfn, 0),
                    n_polls,
                    f"{elapsed:.3f}",
                    int(bool(flags & KPF_HUGE)),
                    int(bool(flags & KPF_THP)),
                ]
            )
    print(f"[info] CSV written to {path}  ({len(pfn_meta)} pages)")


def dump_output(
    path: str,
    access_counts: dict[int, int],
    pfn_meta: dict[int, tuple[int, str]],
    kpf_fd: int,
    n_polls: int,
    elapsed: float,
) -> None:
    if path.endswith(".parquet"):
        dump_parquet(path, access_counts, pfn_meta, kpf_fd, n_polls, elapsed)
    else:
        dump_csv_gz(path, access_counts, pfn_meta, kpf_fd, n_polls, elapsed)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--pid",
        type=int,
        default=None,
        help="PID of redis-server (auto-detected if omitted)",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=0,
        help="Seconds between marking idle and checking access (default: 0)",
    )
    p.add_argument(
        "--samples",
        type=int,
        default=0,
        help="Number of polls to collect (0 = run until Ctrl-C)",
    )
    p.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of top pages to display at exit (default: 20)",
    )
    p.add_argument(
        "--out",
        metavar="FILE",
        default="accesses.parquet",
        help="Output path: .parquet (requires pyarrow) or .csv.gz "
        "(default: accesses.parquet, auto-falls-back to .csv.gz)",
    )
    p.add_argument(
        "--static-maps",
        action="store_true",
        help="Read /proc/<pid>/maps once at startup instead of every poll",
    )
    p.add_argument(
        "--refresh-interval",
        type=int,
        default=10,
        metavar="N",
        help="Re-scan pagemap every N polls to update present PFNs "
        "(0 = every poll; default: 10)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if os.geteuid() != 0:
        sys.exit("error: must be run as root (needed for pagemap PFNs and page_idle)")

    if not Path(IDLE_BITMAP_PATH).exists():
        sys.exit(
            "error: idle page tracking not available\n"
            "       kernel must be built with CONFIG_IDLE_PAGE_TRACKING=y\n"
            f"       expected: {IDLE_BITMAP_PATH}"
        )

    pid = args.pid
    if pid is None:
        pid = find_redis_pid()
        print(f"[info] found redis-server at PID {pid}")
    else:
        if not Path(f"/proc/{pid}").exists():
            sys.exit(f"error: PID {pid} does not exist")

    out_path = _output_path(args.out)
    idle_fd = os.open(IDLE_BITMAP_PATH, os.O_RDWR)

    access_counts: dict[int, int] = defaultdict(int)
    pfn_meta: dict[int, tuple[int, str]] = {}
    poll_n = 0
    start_time = time.monotonic()

    done = False

    def _sigint(_sig, _frame):
        nonlocal done
        done = True

    signal.signal(signal.SIGINT, _sigint)

    refresh_interval = args.refresh_interval
    print(
        f"[info] polling PID {pid}  interval={args.interval}s  "
        f"refresh-interval={refresh_interval}  (Ctrl-C to stop)"
    )

    static_heap_regions: list[tuple[int, int, str]] | None = None
    if args.static_maps:
        regions = get_vma_ranges(pid)
        static_heap_regions = [r for r in regions if r[2] in ("[heap]", "")]
        print(f"[info] static maps: {len(static_heap_regions)} heap/anon VMAs")

    pagemap_fd: int | None = None
    try:
        while not done:
            if args.samples > 0 and poll_n >= args.samples:
                break

            should_refresh = (refresh_interval == 0) or (poll_n % refresh_interval == 0)

            if should_refresh:
                if pagemap_fd is not None:
                    os.close(pagemap_fd)
                    pagemap_fd = None

                if static_heap_regions is not None:
                    heap_regions = static_heap_regions
                else:
                    try:
                        regions = get_vma_ranges(pid)
                    except RuntimeError as e:
                        print(f"[warn] {e}")
                        break
                    heap_regions = [r for r in regions if r[2] in ("[heap]", "")]

                pagemap_path = f"/proc/{pid}/pagemap"
                try:
                    pagemap_fd = os.open(pagemap_path, os.O_RDONLY)
                except (PermissionError, FileNotFoundError) as e:
                    print(f"[warn] cannot open {pagemap_path}: {e}")
                    break

                try:
                    pfn_meta = collect_present_pfns_fd(pagemap_fd, heap_regions)
                except OSError as e:
                    print(f"[warn] pagemap read failed: {e}")
                    break

            n_accessed, n_total = poll_once(
                idle_fd, access_counts, pfn_meta, args.interval
            )

            poll_n += 1
            elapsed = time.monotonic() - start_time
            refresh_marker = " [refresh]" if should_refresh else ""
            print(
                f"[poll {poll_n:5d}]  "
                f"elapsed={elapsed:7.1f}s  "
                f"present={n_total:6d} pages ({n_total * PAGE_SIZE // 1024 // 1024} MiB)  "
                f"accessed={n_accessed:6d}  ({100 * n_accessed / max(n_total, 1):.1f}%)"
                f"{refresh_marker}"
            )
    finally:
        elapsed = time.monotonic() - start_time
        os.close(idle_fd)
        if pagemap_fd is not None:
            os.close(pagemap_fd)

    if poll_n == 0:
        print("[info] no polls completed")
        return

    print(
        f"\n[info] {poll_n} polls over {elapsed:.1f}s  "
        f"({poll_n / elapsed:.1f} polls/s, ~{elapsed / poll_n * 1000:.0f} ms/poll)"
    )

    kpf_fd = os.open("/proc/kpageflags", os.O_RDONLY)
    try:
        print_top(access_counts, pfn_meta, kpf_fd, args.top, elapsed)
        dump_output(out_path, access_counts, pfn_meta, kpf_fd, poll_n, elapsed)
    finally:
        os.close(kpf_fd)


if __name__ == "__main__":
    main()
