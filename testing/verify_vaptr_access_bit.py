#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify vaptr access-bit output from a collected Redis run."
    )
    parser.add_argument(
        "--run-root",
        default="data/curated/redis",
        type=Path,
        help="Root directory containing Redis collection directories",
    )
    parser.add_argument(
        "--prefix",
        default="vaptr-access-bit",
        help="Collection directory prefix to select the latest run",
    )
    return parser.parse_args()


def maybe_hex(value: int | None) -> str | None:
    return None if value is None else f"0x{value:x}"


def latest_run_dir(run_root: Path, prefix: str) -> Path:
    candidates = sorted(path for path in run_root.glob(f"{prefix}*") if path.is_dir())
    if not candidates:
        raise FileNotFoundError(f"no collection directories found for prefix {prefix!r}")
    return candidates[-1]


def main() -> int:
    args = parse_args()
    run_dir = latest_run_dir(args.run_root, args.prefix)
    vaptr_path = run_dir / "vaptr.end.parquet"
    process_trace_path = run_dir / "process_trace.end.parquet"

    if not vaptr_path.is_file():
        raise FileNotFoundError(f"missing {vaptr_path}")
    if not process_trace_path.is_file():
        raise FileNotFoundError(f"missing {process_trace_path}")

    vaptr_df = pl.read_parquet(vaptr_path)
    process_trace_df = pl.read_parquet(process_trace_path)

    required_columns = {
        "key",
        "address",
        "page_addr",
        "available",
        "mapped_pfn",
        "tracking_pfn",
        "physical_page_addr",
        "tracking_physical_page_addr",
        "page_idle",
        "access_bit",
        "access_bit_valid",
    }
    missing = sorted(required_columns.difference(vaptr_df.columns))
    if missing:
        raise AssertionError(f"vaptr missing required columns: {missing}")

    if vaptr_df.is_empty():
        raise AssertionError("vaptr.end.parquet is empty")

    valid_rows = vaptr_df.filter(pl.col("access_bit_valid"))
    accessed_rows = valid_rows.filter(pl.col("access_bit"))
    physical_rows = vaptr_df.filter(pl.col("physical_page_addr").is_not_null())
    redis_rows = process_trace_df.filter(pl.col("name").str.starts_with("redis-server"))
    redis_tgids = sorted(set(redis_rows["tgid"].to_list())) if not redis_rows.is_empty() else []

    if physical_rows.is_empty():
        raise AssertionError("vaptr has no rows with physical-page metadata")
    if valid_rows.is_empty():
        raise AssertionError("vaptr has no rows with access_bit_valid=true")
    if accessed_rows.is_empty():
        raise AssertionError("vaptr has no rows with access_bit=true")
    if not redis_tgids:
        raise AssertionError("process_trace has no redis-server TGID entries")

    print(f"run_dir={run_dir}")
    print(f"vaptr_rows={len(vaptr_df)}")
    print(f"vaptr_valid_rows={len(valid_rows)}")
    print(f"vaptr_accessed_rows={len(accessed_rows)}")
    print(f"redis_tgids={redis_tgids}")
    print("sample_rows:")

    preview = accessed_rows.head(5).iter_rows(named=True)
    for row in preview:
        print(
            {
                "key": row["key"],
                "page_addr": row["page_addr"],
                "physical_page_addr": maybe_hex(row["physical_page_addr"]),
                "tracking_physical_page_addr": maybe_hex(
                    row["tracking_physical_page_addr"]
                ),
                "mapped_pfn": row["mapped_pfn"],
                "tracking_pfn": row["tracking_pfn"],
                "page_idle": row["page_idle"],
                "access_bit": row["access_bit"],
                "access_bit_valid": row["access_bit_valid"],
            }
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
