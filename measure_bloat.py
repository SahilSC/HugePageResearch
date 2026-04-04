import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import polars as pl

# Add project root to path
sys.path.insert(0, os.path.abspath("python/kernmlops"))
from analysis.bloat import clean_rss_pid

# Collection IDs
run_never = "4e982d62-1b59-4559-9926-5fe610b503c1"
run_always = "90b45e53-eafa-44f6-a976-c9c74f9c7877"
run_madvise = "5aacd8b6-e6e3-4877-8cac-90f7bac44ef2"

runs = {
    "never (baseline)": run_never,
    "always (high bloat?)": run_always,
    "madvise": run_madvise,
}

print("--- MEASURING MEMORY BLOAT ---")

results = {}

for label, cid in runs.items():
    print(f"\nProcessing {label} (ID: {cid})...")
    base_dir = f"data/curated/redis/{cid}"

    # Load RSS and Process data
    try:
        rss_df = pl.read_parquet(f"{base_dir}/mm_rss_stat.end.parquet")

        # Find the main redis-server process (tgid with most RSS events > 0)
        # We skip tgid 0 (kernel)
        tgid_counts = (
            rss_df.filter(pl.col("tgid") > 0)
            .group_by("tgid")
            .len()
            .sort("len", descending=True)
        )
        main_pid = tgid_counts[0, "tgid"]
        print(f"  Identified main PID: {main_pid} ({tgid_counts[0, 'len']} events)")

        # Clean RSS timeline
        timeline = clean_rss_pid(rss_df, main_pid)
        print(timeline)
        # Normalize time to start at 0
        min_ts = timeline["ts_ns"].min()
        timeline = timeline.with_columns(
            ((pl.col("ts_ns") - min_ts) / 1e9).alias("time_sec"),
            (pl.col("count") * 4 / 1024).alias("rss_mb"),
        )

        # Calculate stats
        avg_rss = timeline["rss_mb"].mean()
        max_rss = timeline["rss_mb"].max()
        print(f"  Avg RSS: {avg_rss:.2f} MB")
        print(f"  Max RSS: {max_rss:.2f} MB")

        results[label] = {"df": timeline, "avg_rss": avg_rss, "max_rss": max_rss}

    except Exception as e:
        print(f"  Error processing {label}: {e}")

print("\n--- BLOAT CALCULATION ---")
baseline_avg = results["never (baseline)"]["avg_rss"]
baseline_max = results["never (baseline)"]["max_rss"]

print(f"Baseline (Never) Avg RSS: {baseline_avg:.2f} MB")

for label in ["always (high bloat?)", "madvise"]:
    if label in results:
        avg = results[label]["avg_rss"]
        bloat = avg - baseline_avg
        bloat_pct = (bloat / baseline_avg) * 100
        print(f"{label}:")
        print(f"  Avg RSS: {avg:.2f} MB")
        print(f"  Bloat:   {bloat:+.2f} MB ({bloat_pct:+.1f}%)")

# Plotting
plt.figure(figsize=(10, 6))
for label, data in results.items():
    df = data["df"]
    # Downsample for plotting if too large
    if len(df) > 10000:
        df = df.sample(10000).sort("time_sec")
    plt.plot(df["time_sec"], df["rss_mb"], label=label)

plt.xlabel("Time (seconds)")
plt.ylabel("RSS (MB)")
plt.title("Redis RSS Usage by THP Policy")
plt.legend()
plt.grid(True, alpha=0.3)
output_path = Path("figures/redis_bloat_comparison.png")
output_path.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(output_path)
print(f"\nGraph saved to: {output_path}")
