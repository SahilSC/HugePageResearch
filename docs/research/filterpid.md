# Redis Auto-Clean by PID/TGID (Plan)

## Goal

For Redis collections, automatically:
- scrape `process_trace`
- find the `redis-server` process
- filter PID/TGID-carrying parquet rows to that process (process-level = `tgid`)
- write a new curated run named `cleaned<ID>`

Also add a `collect data` CLI argument to control cleaning:
- clean enabled by default
- opt-out available (`--no-clean`)

## Key Finding (What to target)

Use `tgid`, not `pid`, for the default Redis cleaner.

Reason:
- `perf` records both `pid` and `tgid`
- `pid` is the thread id
- `tgid` is the userspace process id (thread group / process)

This comes directly from the perf hook:
- `python/kernmlops/data_collection/bpf_instrumentation/perf/perf_hook.py:27`
- `python/kernmlops/data_collection/bpf_instrumentation/perf/perf_hook.py:28`

## Key Finding (Where to discover Redis process id)

`process_trace` is the simplest reliable source for Redis process discovery in collected data.

Why:
- It records `pid`, `tgid`, `name`, and lifecycle events (`start`, `exec`, `end`)
- Your Redis config already enables `process_trace`

Relevant code:
- `python/kernmlops/data_collection/bpf_instrumentation/fork_and_exit.py:20`
- `python/kernmlops/data_collection/bpf_instrumentation/fork_and_exit.py:53`
- `config/redis_always.yaml:24`

## Key Finding (Why cleaning must be post-collection)

Hooks are loaded before `benchmark.run()` starts, so Redis PIDs/TGIDs are not known yet when BPF hooks are attached.

Relevant code:
- hooks loaded first: `python/kernmlops/cli/collect.py:169`
- benchmark starts later: `python/kernmlops/cli/collect.py:231`

So the low-complexity implementation is:
- collect raw data as today
- then run an automatic cleaning pass on the completed Redis collection

## Desired Behavior (Updated)

When `collect data` runs for Redis:
1. Raw collection is written as normal (existing behavior).
2. If cleaning is enabled, load `process_trace` from that collection.
3. Find `redis-server` TGID(s) automatically.
4. Create a sibling curated collection named `cleaned<ID>` containing only rows for those TGID(s) (for tables with `pid`/`tgid` columns).
5. Leave the original collection untouched.

Example:
- Raw: `data/curated/redis/<ID>/`
- Cleaned: `data/curated/redis/cleaned<ID>/`

## Auto-Detection Rules (Redis-only)

### Target discovery from `process_trace` [Do not filter by cap_type]

Primary rule:
- Use `process_trace` rows where:
  - `cap_type == "exec"` [DO NOT DO THIS]
  - `name` starts with `redis-server` (covers `redis-server` and `redis-server-tcmalloc`)

Target set:
- collect unique `tgid` values from those rows

Fallback (if no matching `exec` rows):
- use `cap_type == "start"` and `name` starts with `redis-server`

If still none:
- cleaning step should warn and skip creating `cleaned<ID>`

### Why `tgid` default is correct

`redis-server` may have multiple threads:
- filtering by `pid` would keep only one thread unless we infer all thread ids
- filtering by `tgid` keeps all threads in the Redis server process

## What gets filtered

For each parquet file in the Redis run:
- If table has `tgid`, filter to `tgid IN redis_server_tgids` [IF THIS DOESNT HAVE TGID, print to console.]
- Else if table has `pid` only, filter to `pid IN redis_server_pids` (rare fallback) [DON'T DO THIS CASE - if sometihng has tgid it hshould have pid too]
- Else copy unchanged

Examples in your Redis runs:
- perf tables (`instructions_retired`, `page_faults`, `dtlb_*`, `itlb_*`, etc.) have `pid` + `tgid`
- `mm_rss_stat` has `pid` + `tgid`
- `process_trace` has `pid` + `tgid`
- `system_info` does not (copy unchanged)

## Important Compatibility Detail (Collection ID rewrite)

The cleaned copy must not only use directory name `cleaned<ID>`; it should also rewrite the `collection_id` column inside copied parquet tables (when present) to `cleaned<ID>`.

Why:
- many tables already contain `collection_id`
- downstream loaders/graphs read ids from table contents (especially `system_info`)
- leaving the old `collection_id` would make the cleaned directory and logical collection id disagree

Plan:
- during copy/filter, if a table has `collection_id`, overwrite it with the new cleaned id

## CLI Plan (`collect data`)

### New options on `collect data`

Add a Click boolean flag pair:
- `--clean/--no-clean`

Default:
- `--clean` (enabled by default)

Scope:
- only applies to Redis benchmark for now
- other benchmarks ignore it (or no-op with a short message)

## Integration Plan (Where to run cleaner)

Integrate the cleaner at the end of `run_collect()` after the final `end` parquet chunk is written.

Relevant point in flow:
- final output write happens in `python/kernmlops/cli/collect.py:263`

Sequence:
1. write raw `<ID>` collection completely
2. if benchmark is Redis and clean enabled, run `clean_redis_collection(...)`
3. print both ids:
   - `Collection_id: <ID>`
   - `Cleaned_collection_id: cleaned<ID>` (if created)

This keeps the current raw data behavior intact and adds a clean derivative.

## Implementation Plan (Not too complex)

### Phase 1: Add a small Redis cleaner utility (post-collection)

Add a helper function/module, e.g.:
- `python/kernmlops/analysis/redis_clean.py` [use analysis for intermediate dir] (or `python/kernmlops/cli/redis_clean.py`)

Function shape:
- `clean_redis_collection(data_curated_dir: Path, collection_id: str) -> str | None`

Responsibilities:
- resolve `data/curated/redis/<ID>`
- read `process_trace.*.parquet`
- detect Redis server TGIDs
- copy/filter all parquet files into `data/curated/redis/cleaned<ID>/`
- rewrite `collection_id` column to `cleaned<ID>` where present
- return cleaned id or `None` if skipped

### Phase 2: Redis server TGID extractor

Implement a small helper:
- `discover_redis_server_tgids(process_trace_df) -> set[int]`

Rules:
- match `name.str.starts_with("redis-server")`
- prefer `cap_type == "exec"`
- fallback to `cap_type == "start"`
- unique TGIDs only

Also collect matching PIDs for optional fallback/reporting.

### Phase 3: Table copy/filter loop

For each parquet file in raw Redis collection directory:
- read parquet
- apply filter only if `pid`/`tgid` columns exist
- overwrite `collection_id` column if present
- write to cleaned directory with same filename

Filtering rule (MVP):
- keep rows where `tgid` is one of discovered Redis server TGIDs

No include/exclude UI in this version (automatic cleaner only).

## Optional (but still simple) follow-up [Ignore optional]

After MVP works, add a non-default extension:
- `--clean-target redis-server|ycsb|redis+ycsb`

But for now keep it fixed to `redis-server` only to stay simple.

## Failure Handling (Redis-only)

If cleaning is enabled and benchmark is Redis:
- Missing `process_trace` parquet:
  - warn and skip cleaning [SHOULD FAIL FAST]
- `process_trace` present but no `redis-server` TGID found:
  - warn and skip cleaning [FAIL FAST]
- Cleaner error:
  - print error and preserve raw collection (do not delete anything)

## Validation Plan

For each cleaned run:
- print discovered Redis TGIDs
- print row counts before/after for each filtered table
- verify `system_info.benchmark_name == "redis"`
- verify cleaned directory exists and includes `system_info.end.parquet`
- verify cleaned tables have `collection_id == cleaned<ID>` (spot-check)

## Why this plan is the right tradeoff

- Uses existing collected `process_trace` data (no BPF changes)
- Correctly targets process-level Redis data via `tgid`
- Preserves raw data for debugging
- Minimal changes to collection flow
- Easy to test on existing Redis collections and future runs

## filteridv2

### Hybrid approach (probably better than pure `process_trace` scraping)

This is a strong alternative and may be a better default design:

- attach eBPF hooks before the benchmark starts (same as today)
- start Redis benchmark normally
- capture the Redis server process id (`redis-server`) while it is running
- still collect all data from all processes (no kernel-side filtering yet)
- filter only after collection finishes

This keeps the system simple while improving how we identify the target process.

### Why this may be better

Compared to "discover Redis only from `process_trace` after the fact", this approach:

- reduces dependence on `process_trace` heuristics
- avoids name-matching edge cases (missing `exec`, truncated names, etc.)
- gives a direct source of truth for the target Redis process
- still preserves raw data and post-hoc flexibility

### Key observation for Redis specifically

`RedisBenchmark.run()` launches Redis with `subprocess.Popen(...)` and stores it on `self.server`.

That means the collector can potentially use:
- `self.server.pid`

For Redis, this PID is the process leader and is the correct default `tgid` target for post-filtering.

Relevant code:
- `python/kernmlops/kernmlops_benchmark/redis.py:112`

### Practical design for V2

Keep raw collection unchanged, but record target ids during collection:

1. Start hooks first (unchanged).
2. Start Redis benchmark (unchanged).
3. After Redis starts, obtain target Redis pid from the benchmark object (`server.pid`).
4. Treat that pid as the target `tgid` for cleaning.
5. Save this target id in metadata for the collection (new small metadata file or extra field in `system_info`).
6. After collection completes, run the same post-filter copy step to build `cleaned<ID>`.

### Where the pid/tgid should come from (priority order)

Recommended priority:

1. Direct benchmark pid (`RedisBenchmark.server.pid`) as target `tgid` (best) [ IF TGID NOT AVALIABLE, FAIL FAST]
2. `process_trace` confirmation / fallback (`redis-server` exec/start rows)
3. If neither is available, skip cleaning with warning

### Why still keep `process_trace`

Even with direct pid capture, `process_trace` is still useful for:

- validation (did the captured pid actually show up as `redis-server`?)
- debugging weird runs
- future expansion (clean Redis + YCSB modes)

So V2 should not remove `process_trace`; it should reduce reliance on it for target discovery.

### Impact on current plan

This V2 does not conflict with the current `cleaned<ID>` post-filter plan.

It mainly changes target discovery:
- current plan: derive Redis TGID from `process_trace` only
- V2 plan: prefer direct Redis pid/tgid from the running benchmark, use `process_trace` as validation/fallback

### Why not kernel-side filtering yet (still)

Even in V2, it still makes sense to filter after collection first:

- fewer moving parts
- no dynamic BPF allowlist updates
- easier to validate correctness
- raw data preserved for debugging/re-analysis

Kernel-time filtering can still be a later optimization if data volume becomes a problem.
