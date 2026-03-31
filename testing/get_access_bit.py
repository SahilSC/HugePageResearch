#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from data_collection.page_access import PageAccessTracker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sample the access bit for the physical page backing a process VA."
    )
    parser.add_argument("--pid", required=True, type=int, help="Target process pid")
    parser.add_argument(
        "--va",
        required=True,
        type=lambda value: int(value, 0),
        help="Virtual address in decimal or 0x-prefixed hex",
    )
    parser.add_argument(
        "--samples",
        default=2,
        type=int,
        help="Number of interval samples to take (default: 2)",
    )
    parser.add_argument(
        "--interval",
        default=0.25,
        type=float,
        help="Seconds to sleep between samples (default: 0.25)",
    )
    return parser.parse_args()


def maybe_hex(value: int | None) -> str | None:
    return None if value is None else f"0x{value:x}"


def main() -> int:
    args = parse_args()
    tracker = PageAccessTracker()
    try:
        for sample_index in range(args.samples):
            result = tracker.sample(args.pid, args.va)
            print(
                json.dumps(
                    {
                        "sample": sample_index,
                        "pid": result.pid,
                        "virtual_address": maybe_hex(result.virtual_address),
                        "mapped_pfn": result.mapped_pfn,
                        "tracking_pfn": result.tracking_pfn,
                        "physical_page_addr": maybe_hex(result.physical_page_addr),
                        "tracking_physical_page_addr": maybe_hex(
                            result.tracking_physical_page_addr
                        ),
                        "page_idle": result.page_idle,
                        "access_bit": result.access_bit,
                        "access_bit_valid": result.access_bit_valid,
                    }
                )
            )
            if sample_index + 1 < args.samples:
                time.sleep(args.interval)
    finally:
        tracker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
