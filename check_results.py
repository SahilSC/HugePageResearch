import os
import sys

import polars as pl

# Add project root to path
sys.path.insert(0, os.path.abspath("python/kernmlops"))

# Collection ID from the recent run
COLLECTION_ID = "4e982d62-1b59-4559-9926-5fe610b503c1"
BASE_DIR = f"data/curated/redis/{COLLECTION_ID}"

print(f"--- Analyzing Collection: {COLLECTION_ID} ---")
print(f"Data directory: {BASE_DIR}")
print()

try:
    # Load RSS data
    rss_df = pl.read_parquet(f"{BASE_DIR}/mm_rss_stat.end.parquet")
    print(f"RSS Events captured: {len(rss_df)}")

    # Load Process data
    proc_df = pl.read_parquet(f"{BASE_DIR}/process_trace.end.parquet")
    print(f"Process Events captured: {len(proc_df)}")

    # Load System info
    sys_df = pl.read_parquet(f"{BASE_DIR}/system_info.end.parquet")
    print(f"System Info: {sys_df['manufacturer'][0]} {sys_df['os'][0]}")

    # Find redis-like processes in RSS data (since process_trace might miss early starters)
    print("\nTop 5 processes by RSS event count:")
    top_tgids = rss_df.group_by("tgid").len().sort("len", descending=True).head(5)
    for row in top_tgids.iter_rows():
        tgid, count = row
        print(f"  TGID {tgid}: {count} events")

except Exception as e:
    print(f"Error reading data: {e}")
