import polars as pl

# Load process trace
base_dir = "data/curated/redis/3eee00c3-9a1a-42ce-9241-2ce452f4c604"
df = pl.read_parquet(f"{base_dir}/process_trace.end.parquet")
print(df.head())
print(df.columns)
# Filter for redis-server
redis_procs = df.filter(pl.col("name").str.contains("redis-server"))
print("\n--- Redis Processes ---")
print(redis_procs)
