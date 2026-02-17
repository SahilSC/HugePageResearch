#!/usr/bin/env python3
"""
compaction_analysis.py — Build three joined, temporally-aligned tables from
collected parquet files for training a compaction decision model.

Output tables:
  1. page_observations  — Per-VMA-region snapshots (one row per region per timestamp)
  2. system_context     — Global system state per observation window
  3. compaction_events  — Intervention decisions with before/after measurements

Usage:
    python compaction_analysis.py                         # uses default manifest
    python compaction_analysis.py --manifest data/compaction_manifest.json
    python compaction_analysis.py --collection-dir data/curated/redis/<id> --benchmark redis

Output:
    data/page_observations.parquet  +  data/page_observations.csv
    data/system_context.parquet     +  data/system_context.csv
    data/compaction_events.parquet  +  data/compaction_events.csv
"""

import argparse
import json
import sys
from pathlib import Path

import polars as pl


# ---------------------------------------------------------------------------
# Parquet loader helpers
# ---------------------------------------------------------------------------

def try_load(base: Path, name: str) -> pl.DataFrame | None:
    """Load a parquet file if it exists, else return None."""
    path = base / f"{name}.end.parquet"
    if path.exists():
        return pl.read_parquet(path)
    # Also check for intermediate outputs (numbered files)
    candidates = sorted(base.glob(f"{name}.*.parquet"))
    if candidates:
        return pl.read_parquet(candidates[-1])
    return None


# ---------------------------------------------------------------------------
# Table 1: page_observations
# ---------------------------------------------------------------------------

def build_page_observations(
    collection_dir: Path,
    benchmark: str,
    collection_id: str,
) -> pl.DataFrame | None:
    """
    Build per-VMA-region snapshots from smaps_vma_samples.

    Each row = one VMA region at one observation timestamp.
    Uses (start_addr, end_addr) as a stable page identifier.
    """
    vma_df = try_load(collection_dir, "smaps_vma_samples")
    if vma_df is None or len(vma_df) == 0:
        print(f"  ⚠ No smaps_vma_samples data in {collection_dir}")
        return None

    # Compute first-seen timestamp per VMA region for allocation age
    first_seen = (
        vma_df.group_by(["start_addr", "end_addr"])
        .agg(pl.col("ts_ns").min().alias("first_seen_ts_ns"))
    )
    vma_df = vma_df.join(first_seen, on=["start_addr", "end_addr"], how="left")

    result = vma_df.with_columns([
        pl.lit(collection_id).alias("collection_id"),
        pl.lit(benchmark).alias("benchmark"),
        # Rename ts_ns to observation_ts_ns for clarity
        pl.col("ts_ns").alias("observation_ts_ns"),
        # Page identifier: hex of start_addr
        pl.col("start_addr")
        .map_elements(lambda x: hex(x), return_dtype=pl.String())
        .alias("page_id"),
        # VMA address info (pass through)
        pl.col("start_addr").alias("vaddr_base"),
        pl.col("start_addr").alias("vma_start"),
        pl.col("end_addr").alias("vma_end"),
        pl.col("pid").alias("pid"),
        # Bloat estimate: 1 - (referenced / rss), clipped to [0, 1]
        (
            1.0 - pl.col("referenced_kb").cast(pl.Float64)
            / pl.col("rss_kb").cast(pl.Float64).clip(lower_bound=1)
        ).clip(lower_bound=0.0, upper_bound=1.0).alias("bloat_estimate"),
        # Allocation age
        (pl.col("ts_ns") - pl.col("first_seen_ts_ns")).alias("allocation_age_ns"),
        # PMD mapped flag
        (pl.col("anon_hugepages_kb") > 0).cast(pl.Int64).alias("pmd_mapped"),
    ])

    # Select and order columns to match the spec
    cols = [
        "collection_id",
        "benchmark",
        "observation_ts_ns",
        "pid",
        "page_id",
        "vaddr_base",
        "vma_start",
        "vma_end",
        "vma_flags" if "vm_flags" not in result.columns else "vm_flags",
        "rss_kb",
        "anonymous_kb",
        "referenced_kb",
        "anon_hugepages_kb",
        "thp_eligible",
        "bloat_estimate",
        "allocation_age_ns",
        "pmd_mapped",
    ]
    # Handle the vm_flags -> vma_flags rename
    if "vm_flags" in result.columns:
        result = result.rename({"vm_flags": "vma_flags"})
        cols = [c if c != "vm_flags" else "vma_flags" for c in cols]

    # Drop ts_ns and first_seen_ts_ns since we have observation_ts_ns
    available = [c for c in cols if c in result.columns]
    return result.select(available)


# ---------------------------------------------------------------------------
# Table 2: system_context
# ---------------------------------------------------------------------------

def build_system_context(
    collection_dir: Path,
    benchmark: str,
    collection_id: str,
) -> pl.DataFrame | None:
    """
    Build global system state per observation window.

    Joins /proc/meminfo snapshots, /proc/vmstat snapshots, and aggregate
    perf counters (dTLB/iTLB misses).
    """
    # --- Memory usage from /proc/meminfo ---
    mem_df = try_load(collection_dir, "memory_usage")
    if mem_df is None or len(mem_df) == 0:
        print(f"  ⚠ No memory_usage data in {collection_dir}")
        return None

    # Convert ts_uptime_us to ts_ns for consistency
    if "ts_uptime_us" in mem_df.columns:
        mem_df = mem_df.with_columns(
            (pl.col("ts_uptime_us") * 1000).alias("observation_ts_ns"),
        )
    else:
        print(f"  ⚠ No timestamp column in memory_usage")
        return None

    # Build memory columns
    mem_result = mem_df.with_columns([
        pl.lit(collection_id).alias("collection_id"),
        pl.lit(benchmark).alias("benchmark"),
        (pl.col("mem_free_bytes") / (1024 * 1024)).alias("mem_free_mb"),
        (pl.col("mem_available_bytes") / (1024 * 1024)).alias("mem_available_mb"),
        (pl.col("anon_hugepages_total_bytes") / (1024 * 1024)).alias("anon_hugepages_mb"),
        (pl.col("anon_pages_total_bytes") / (1024 * 1024)).alias("anon_pages_mb"),
        (pl.col("swap_free_bytes") / (1024 * 1024)).alias("swap_free_mb"),
        (pl.col("swap_total_bytes") / (1024 * 1024)).alias("swap_total_mb"),
        (pl.col("dirty_bytes") / (1024 * 1024)).alias("dirty_mb"),
        (pl.col("writeback_bytes") / (1024 * 1024)).alias("writeback_mb"),
        # Hugepages_Total and Free are already stored as counts (multiplied by 1024 in hook)
        # Undo the * 1024 from the hook to get actual counts
        (pl.col("hugepages_total") / 1024).cast(pl.Int64).alias("hugepages_total"),
        (pl.col("hugepages_free") / 1024).cast(pl.Int64).alias("hugepages_free"),
        # Hugepage fraction
        (
            pl.col("anon_hugepages_total_bytes")
            / pl.col("anon_pages_total_bytes").clip(lower_bound=1)
        ).alias("hugepage_fraction"),
    ])

    # Select memory columns
    mem_cols = [
        "collection_id", "benchmark", "observation_ts_ns",
        "mem_free_mb", "mem_available_mb",
        "anon_hugepages_mb", "anon_pages_mb",
        "hugepages_free", "hugepages_total",
        "swap_free_mb", "swap_total_mb",
        "dirty_mb", "writeback_mb",
        "hugepage_fraction",
    ]
    result = mem_result.select([c for c in mem_cols if c in mem_result.columns])

    # --- VMStat from /proc/vmstat ---
    vmstat_df = try_load(collection_dir, "vmstat_samples")
    if vmstat_df is not None and len(vmstat_df) > 0:
        # VMStat has ts_ns; join to closest memory timestamp using asof join
        vmstat_cols_to_keep = [
            "ts_ns",
            "pgfault", "pgmajfault",
            "pgmigrate_success", "pgmigrate_fail",
            "compact_stall", "compact_fail", "compact_success",
            "compact_daemon_wake",
            "compact_migrate_scanned", "compact_free_scanned", "compact_isolated",
            "thp_fault_alloc", "thp_fault_fallback",
            "thp_collapse_alloc", "thp_collapse_alloc_failed",
            "thp_split_page", "thp_split_page_failed",
            "thp_deferred_split_page", "thp_split_pmd",
        ]
        available_vmstat_cols = [c for c in vmstat_cols_to_keep if c in vmstat_df.columns]
        vmstat_sub = vmstat_df.select(available_vmstat_cols).sort("ts_ns")

        # Asof join: for each memory observation, find the closest vmstat snapshot
        result = result.sort("observation_ts_ns").join_asof(
            vmstat_sub,
            left_on="observation_ts_ns",
            right_on="ts_ns",
            strategy="nearest",
        )
        # Drop the redundant ts_ns from vmstat
        if "ts_ns" in result.columns:
            result = result.drop("ts_ns")

    # --- Aggregate perf counters ---
    for perf_name, col_name in [
        ("dtlb_misses", "system_dtlb_miss_total"),
        ("itlb_misses", "system_itlb_miss_total"),
        ("tlb_flushes", "system_tlb_flush_total"),
        ("dtlb_walk_duration", "system_dtlb_walk_total"),
    ]:
        perf_df = try_load(collection_dir, perf_name)
        if perf_df is not None and "cumulative_count" in perf_df.columns and len(perf_df) > 0:
            # Perf data has per-CPU timestamps. Group by nearest observation window.
            # For simplicity, sum across CPUs per timestamp and asof join.
            if "ts_uptime_us" in perf_df.columns:
                perf_agg = (
                    perf_df.with_columns(
                        (pl.col("ts_uptime_us") * 1000).alias("ts_ns")
                    )
                    .group_by("ts_ns")
                    .agg(pl.col("cumulative_count").sum().alias(col_name))
                    .sort("ts_ns")
                )
                result = result.sort("observation_ts_ns").join_asof(
                    perf_agg,
                    left_on="observation_ts_ns",
                    right_on="ts_ns",
                    strategy="nearest",
                )
                if "ts_ns" in result.columns:
                    result = result.drop("ts_ns")

    return result


# ---------------------------------------------------------------------------
# Table 3: compaction_events
# ---------------------------------------------------------------------------

def build_compaction_events(
    collection_dir: Path,
    benchmark: str,
    collection_id: str,
) -> pl.DataFrame | None:
    """
    Build intervention decisions with before/after measurements.

    Reads thp_candidates and thp_interventions, then joins with smaps
    data to compute before/after deltas.
    """
    candidates_df = try_load(collection_dir, "thp_candidates")
    interventions_df = try_load(collection_dir, "thp_interventions")

    if (candidates_df is None or len(candidates_df) == 0) and (
        interventions_df is None or len(interventions_df) == 0
    ):
        print(f"  ⚠ No thp_candidates or thp_interventions data in {collection_dir}")
        return None

    vma_df = try_load(collection_dir, "smaps_vma_samples")

    rows: list[dict] = []

    # Process interventions (break decisions)
    if interventions_df is not None and len(interventions_df) > 0:
        for row in interventions_df.iter_rows(named=True):
            entry: dict = {
                "collection_id": collection_id,
                "benchmark": benchmark,
                "page_id": hex(row["start_addr"]),
                "decision_ts_ns": row["ts_ns"],
                "decision": "break",
                "decision_reason": "intervention",
                "success": row.get("success", False),
                "error": row.get("error", ""),
                "age_bucket": row.get("age_bucket", "unknown"),
            }

            # Look up before/after smaps data
            if vma_df is not None and len(vma_df) > 0:
                region_data = vma_df.filter(
                    (pl.col("start_addr") == row["start_addr"])
                    & (pl.col("end_addr") == row["end_addr"])
                ).sort("ts_ns")

                if len(region_data) > 0:
                    # Before: latest snapshot before the decision
                    before = region_data.filter(pl.col("ts_ns") <= row["ts_ns"])
                    if len(before) > 0:
                        last_before = before.row(-1, named=True)
                        entry["referenced_kb_before"] = last_before["referenced_kb"]
                        entry["anon_hugepages_kb_before"] = last_before["anon_hugepages_kb"]
                        rss = max(last_before["rss_kb"], 1)
                        entry["bloat_estimate"] = round(
                            1.0 - last_before["referenced_kb"] / rss, 4
                        )

                    # After: earliest snapshot after the decision
                    after = region_data.filter(pl.col("ts_ns") > row["ts_ns"])
                    if len(after) > 0:
                        first_after = after.row(0, named=True)
                        entry["referenced_kb_after"] = first_after["referenced_kb"]
                        entry["anon_hugepages_kb_after"] = first_after["anon_hugepages_kb"]

                    # Memory freed
                    if "anon_hugepages_kb_before" in entry and "anon_hugepages_kb_after" in entry:
                        entry["memory_freed_kb"] = (
                            entry["anon_hugepages_kb_before"]
                            - entry["anon_hugepages_kb_after"]
                        )
            rows.append(entry)

    # Process candidates that were NOT intervened (keep decisions)
    if candidates_df is not None and len(candidates_df) > 0:
        intervened_decision_ids: set[str] = set()
        if interventions_df is not None and len(interventions_df) > 0:
            if "decision_id" in interventions_df.columns:
                intervened_decision_ids = set(
                    interventions_df["decision_id"].to_list()
                )

        for row in candidates_df.iter_rows(named=True):
            if row.get("decision_id") in intervened_decision_ids:
                continue  # Already processed as an intervention
            entry = {
                "collection_id": collection_id,
                "benchmark": benchmark,
                "page_id": hex(row["start_addr"]),
                "decision_ts_ns": row["ts_ns"],
                "decision": "keep",
                "decision_reason": row.get("candidate_type", "unknown"),
                "success": True,
                "error": "",
                "age_bucket": "unknown",
            }

            # Look up bloat from smaps
            if vma_df is not None and len(vma_df) > 0:
                region_data = vma_df.filter(
                    (pl.col("start_addr") == row["start_addr"])
                    & (pl.col("end_addr") == row["end_addr"])
                    & (pl.col("ts_ns") <= row["ts_ns"])
                ).sort("ts_ns")
                if len(region_data) > 0:
                    last = region_data.row(-1, named=True)
                    entry["referenced_kb_before"] = last["referenced_kb"]
                    entry["anon_hugepages_kb_before"] = last["anon_hugepages_kb"]
                    rss = max(last["rss_kb"], 1)
                    entry["bloat_estimate"] = round(
                        1.0 - last["referenced_kb"] / rss, 4
                    )
            rows.append(entry)

    if not rows:
        return None

    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def write_table(df: pl.DataFrame, name: str, output_dir: Path):
    """Write a DataFrame as both parquet and CSV."""
    parquet_path = output_dir / f"{name}.parquet"
    csv_path = output_dir / f"{name}.csv"
    df.write_parquet(parquet_path)
    df.write_csv(csv_path)
    print(f"  ✓ {name}: {len(df)} rows, {len(df.columns)} cols")
    print(f"    → {parquet_path}")
    print(f"    → {csv_path}")


def process_collection(
    collection_dir: Path,
    benchmark: str,
    collection_id: str,
) -> tuple[pl.DataFrame | None, pl.DataFrame | None, pl.DataFrame | None]:
    """Process a single collection and return the three tables."""
    print(f"\nProcessing {benchmark}/{collection_id}...")

    page_obs = build_page_observations(collection_dir, benchmark, collection_id)
    sys_ctx = build_system_context(collection_dir, benchmark, collection_id)
    comp_events = build_compaction_events(collection_dir, benchmark, collection_id)

    return page_obs, sys_ctx, comp_events


def main():
    parser = argparse.ArgumentParser(
        description="Build three-table compaction data from collected parquet files.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--manifest",
        type=str,
        default="data/compaction_manifest.json",
        help="Path to the manifest JSON from compaction_collect.py.",
    )
    group.add_argument(
        "--collection-dir",
        type=str,
        help="Process a single collection directory (skip manifest).",
    )
    parser.add_argument(
        "--benchmark",
        type=str,
        default="unknown",
        help="Benchmark name (used with --collection-dir).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data",
        help="Output directory for the three tables (default: data).",
    )
    args = parser.parse_args()

    all_page_obs: list[pl.DataFrame] = []
    all_sys_ctx: list[pl.DataFrame] = []
    all_comp_events: list[pl.DataFrame] = []

    if args.collection_dir:
        cdir = Path(args.collection_dir)
        cid = cdir.name
        po, sc, ce = process_collection(cdir, args.benchmark, cid)
        if po is not None:
            all_page_obs.append(po)
        if sc is not None:
            all_sys_ctx.append(sc)
        if ce is not None:
            all_comp_events.append(ce)
    else:
        manifest_path = Path(args.manifest)
        if not manifest_path.exists():
            print(f"ERROR: Manifest not found at {manifest_path}")
            print("Run compaction_collect.py first, or use --collection-dir.")
            sys.exit(1)

        manifest = json.loads(manifest_path.read_text())
        benchmarks = manifest.get("benchmarks", {})

        for bm_name, collection_ids in benchmarks.items():
            for cid in collection_ids:
                cdir = Path("data") / "curated" / bm_name / cid
                po, sc, ce = process_collection(cdir, bm_name, cid)
                if po is not None:
                    all_page_obs.append(po)
                if sc is not None:
                    all_sys_ctx.append(sc)
                if ce is not None:
                    all_comp_events.append(ce)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print("  Output Summary")
    print(f"{'='*60}")

    tables_written = 0

    if all_page_obs:
        combined = pl.concat(all_page_obs, how="diagonal")
        write_table(combined, "page_observations", output_dir)
        tables_written += 1
        print(f"    Columns: {combined.columns}")
    else:
        print("  ⚠ page_observations: No data (need smaps_harness hook enabled)")

    if all_sys_ctx:
        combined = pl.concat(all_sys_ctx, how="diagonal")
        write_table(combined, "system_context", output_dir)
        tables_written += 1
        print(f"    Columns: {combined.columns}")
    else:
        print("  ⚠ system_context: No data (need memory_usage hook enabled)")

    if all_comp_events:
        combined = pl.concat(all_comp_events, how="diagonal")
        write_table(combined, "compaction_events", output_dir)
        tables_written += 1
        print(f"    Columns: {combined.columns}")
    else:
        print("  ⚠ compaction_events: No data (need smaps_harness hook with interventions)")

    if tables_written == 0:
        print("\nNo tables produced. Ensure hooks are enabled in your config:")
        print("  hooks:")
        print("    - smaps_harness      # for page_observations + compaction_events")
        print("    - vmstat_harness     # for system_context vmstat columns")
        print("    - memory_usage       # for system_context meminfo columns")
        print("    - perf              # for system_context TLB counters")
        sys.exit(1)

    print(f"\n✓ {tables_written}/3 tables written to {output_dir}/")
    print("\nTo join tables for training:")
    print("  import polars as pl")
    print("  page_obs = pl.read_parquet('data/page_observations.parquet')")
    print("  sys_ctx  = pl.read_parquet('data/system_context.parquet')")
    print("  df = page_obs.sort('observation_ts_ns').join_asof(")
    print("      sys_ctx.sort('observation_ts_ns'),")
    print("      on='observation_ts_ns', by='collection_id', strategy='nearest')")


if __name__ == "__main__":
    main()
