#!/usr/bin/env python3
"""
compaction_collect.py — Orchestrator for huge page compaction data collection.

Runs benchmarks with THP=always and all compaction-relevant BPF hooks enabled,
then records the collection IDs in a manifest for downstream analysis.

Usage (must run as root):
    sudo -E HOME=$HOME UNAME=$USER GID=$(id -g) PATH="$PATH" \
      .venv/bin/python compaction_collect.py \
        --benchmarks redis gap \
        --runs-per-benchmark 3

Or to run all benchmarks once:
    sudo -E ... .venv/bin/python compaction_collect.py --benchmarks all
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Benchmark → config mapping
# ---------------------------------------------------------------------------
BENCHMARK_CONFIGS = {
    "redis": "config/compaction_redis.yaml",
    "gap": "config/compaction_gap.yaml",
    "mongodb": "config/compaction_mongodb.yaml",
    "memcached": "config/compaction_memcached.yaml",
    "linux_build": "config/compaction_linux_build.yaml",
}

ALL_BENCHMARKS = list(BENCHMARK_CONFIGS.keys())

# Benchmark -> (required path under benchmark root, setup command hint)
BENCHMARK_REQUIREMENTS = {
    "redis": ("ycsb", "bash scripts/setup-benchmarks/setup-redis.sh"),
    "gap": ("gap", "bash scripts/setup-benchmarks/setup-gap.sh"),
    "mongodb": ("ycsb", "bash scripts/setup-benchmarks/setup-mongodb.sh"),
    "memcached": ("ycsb", "bash scripts/setup-benchmarks/install-ycsb.sh"),
    "linux_build": ("linux_build", "bash scripts/setup-benchmarks/clone-linux.sh"),
}


def benchmark_root_dir() -> Path:
    """
    Resolve benchmark root using the same env conventions as kernmlops.
    """
    benchmark_dir = os.environ.get("BENCHMARK_DIR")
    if benchmark_dir:
        return Path(benchmark_dir)
    if "UNAME" in os.environ:
        return Path(f"/home/{os.environ['UNAME']}/kernmlops-benchmark")
    return Path.home() / "kernmlops-benchmark"


def check_benchmark_setup(benchmarks: list[str]) -> bool:
    """
    Validate required benchmark directories exist before running collection.
    """
    root = benchmark_root_dir()
    missing: list[tuple[str, Path, str]] = []

    for bm in benchmarks:
        req = BENCHMARK_REQUIREMENTS.get(bm)
        if req is None:
            continue
        rel_path, setup_hint = req
        required_path = root / rel_path
        if not required_path.exists():
            missing.append((bm, required_path, setup_hint))

    if not missing:
        return True

    print("\nERROR: One or more benchmarks are not configured:")
    for bm, required_path, setup_hint in missing:
        print(f"  - {bm}: missing {required_path}")
        print(f"    setup: {setup_hint}")
    print(
        "\nInstall missing benchmarks, then rerun this command."
        " (Tip: keep `UNAME=$USER` under sudo so benchmark paths resolve correctly.)"
    )
    return False


def run_single_collection(
    benchmark: str, config_path: str, verbose: bool
) -> str | None:
    """
    Run a single data collection pass for the given benchmark.

    Output is streamed directly to the terminal so the user can see progress.
    Returns the collection ID string, or None if the run failed.
    """
    cmd = [
        sys.executable,
        "python/kernmlops",
        "collect",
        "data",
        "-c",
        config_path,
    ]
    if verbose:
        cmd.append("-v")

    print(f"\n{'=' * 60}")
    print(f"  Running: {benchmark}  (config: {config_path})")
    print(f"  Command: {' '.join(cmd)}")
    print(f"{'=' * 60}\n", flush=True)

    # Stream output directly to terminal (no capture)
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print(f"\n  ✗ FAILED (exit {result.returncode})")
        return None

    # Find the latest collection directory for this benchmark
    curated_dir = Path("data/curated") / benchmark
    if curated_dir.exists():
        # Get most recently modified collection dir
        collections = sorted(
            curated_dir.iterdir(),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if collections:
            collection_id = collections[0].name
            print(f"\n  ✓ Collection ID: {collection_id}")
            return collection_id

    print(f"\n  ✗ No collection output found in {curated_dir}")
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Collect compaction-relevant data across benchmarks.",
    )
    parser.add_argument(
        "--benchmarks",
        nargs="+",
        default=["all"],
        choices=ALL_BENCHMARKS + ["all"],
        help="Benchmarks to run (default: all).",
    )
    parser.add_argument(
        "--runs-per-benchmark",
        type=int,
        default=1,
        help="How many collection runs per benchmark (default: 1).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data",
        help="Base output directory (default: data).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Pass -v to the collect command.",
    )
    args = parser.parse_args()

    # Resolve benchmark list
    benchmarks = ALL_BENCHMARKS if "all" in args.benchmarks else args.benchmarks

    # Validate configs exist
    for bm in benchmarks:
        cfg = BENCHMARK_CONFIGS[bm]
        if not Path(cfg).exists():
            print(f"ERROR: config {cfg} not found. Run from the repo root.")
            sys.exit(1)
    if not check_benchmark_setup(benchmarks):
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Run collections
    # -----------------------------------------------------------------------
    manifest: dict[str, list[str]] = {bm: [] for bm in benchmarks}
    total = len(benchmarks) * args.runs_per_benchmark
    done = 0

    print("\nCompaction Data Collection")
    print(f"  Benchmarks : {', '.join(benchmarks)}")
    print(f"  Runs each  : {args.runs_per_benchmark}")
    print(f"  Total runs : {total}")
    print(f"  Output dir : {args.output_dir}")

    for bm in benchmarks:
        for run_idx in range(args.runs_per_benchmark):
            done += 1
            print(
                f"\n[{done}/{total}] {bm} run {run_idx + 1}/{args.runs_per_benchmark}"
            )
            cid = run_single_collection(bm, BENCHMARK_CONFIGS[bm], args.verbose)
            if cid:
                manifest[bm].append(cid)

    # -----------------------------------------------------------------------
    # Write manifest
    # -----------------------------------------------------------------------
    manifest_path = Path(args.output_dir) / "compaction_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_data = {
        "created": datetime.now().isoformat(),
        "benchmarks": manifest,
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2) + "\n")
    print(f"\n✓ Manifest written to {manifest_path}")

    # Summary
    succeeded = sum(len(ids) for ids in manifest.values())
    print(f"\nDone: {succeeded}/{total} collections succeeded.")
    if succeeded < total:
        print("Some runs failed — check the output above for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
