# Base-Pages Replay Change Log

This note explains the new replay behavior, the exact files I changed, and the
new experiment outputs. The goal was to remove the artificial overhead from the
old "split every tracked key before access 0" baseline and replace it with a
cleaner "Redis starts with no THP" baseline.

## What Changed In The Code

### 1. Replay now treats the all-zero row differently

File:
- `python/kernmlops/data_collection/replay_trace.py`

Main change:
- The all-zero breakpoint row no longer means "issue split-thp for every key at
  access 0."
- It now means "restart Redis with `--disable-thp yes` and replay with no
  per-key splits."

Why this matters:
- The old row mixed two costs together:
  1. the runtime cost of operating without huge pages
  2. a large replay-time storm of split-thp syscalls
- The new row isolates the first cost and removes the second one.

Implementation details:
- `restore_snapshot(...)` now accepts `disable_thp_for_redis: bool = False`.
- When that flag is true, replay starts Redis with `--disable-thp yes`.
- `_uses_base_pages_baseline(...)` detects the all-zero breakpoint row.
- `run_benchmark(...)` uses that detection to:
  - restore Redis in no-THP mode for the first row
  - replay with an empty effective breakpoint map for that row
  - keep all non-zero rows on the old per-key split path

### 2. Breakpoint docs were updated to match the new meaning

File:
- `python/kernmlops/analysis/generate_breakpoints.py`

Main change:
- The docs and CLI help now explain that the all-zero row is a special
  base-pages baseline during replay.
- The parquet format itself did not change.

Why this matters:
- The values in the parquet still look the same as before.
- The semantic change lives in replay, so the documentation needs to say that
  clearly.

### 3. Replay tests now prove the new baseline path

File:
- `testing/test_replay_trace.py`

Added coverage for:
- `restore_snapshot(..., disable_thp_for_redis=True)` appending
  `--disable-thp yes`
- `run_benchmark(...)` treating the all-zero row as the base-pages baseline
- non-baseline rows still using the original breakpoint map unchanged

Validation:
- `PYTHONPATH=python/kernmlops .venv/bin/python -m unittest testing.test_generate_breakpoints testing.test_replay_trace`
- Result: `Ran 25 tests ... OK`

## New Experiment

### Inputs reused

I kept the workload fixed by reusing:
- `data/redis_traces/snapshot.rdb`
- `data/redis_traces/monitor_run.log`

The generated breakpoint matrix for the rerun is:
- `data/test3_basepages_breakpoints_10keys_12rows.parquet`

This is the same 12-row shape as before:
- `base_pages` baseline
- `no_break`
- top 10 split-only hot-key rows

### Runtime-only rerun

Output parquet:
- `data/test3_basepages_results_10keys_12rows.parquet`

Execution environment:
- Docker container: `clever_hofstadter`

Replay wall-clock:
- `1478.985s` (`24.6` minutes)

Important runtime result:
- `base_pages`: `18.273s`
- `no_break`: `17.796s`
- `base_pages` vs `no_break`: `+2.68%`

This is the key answer to your question.

The older runtime-only first-row baseline from
`data/test1_hotkey_results_32rows.parquet` was:
- first row: `20.659s`
- `no_break`: `17.765s`
- gap: `+16.29%`

So after removing the split storm:
- old first-row gap: `+16.29%`
- new base-pages gap: `+2.68%`

That strongly suggests the old "all-break" time was heavily inflated by the
cost of replay-time split operations, not just by the cost of running Redis on
base pages.

## New Graph And Dashboard

Generated analysis files:
- `temp_data_analysis/basepages_10keys_analysis.py`
- `temp_data_analysis/basepages_10keys_metrics.json`
- `temp_data_analysis/basepages_10keys_rows.json`
- `temp_data_analysis/basepages_10keys_summary.md`
- `temp_data_analysis/basepages_10keys_runtime_impact.png`
- `temp_data_analysis/basepages_10keys_dashboard.html`

The new dashboard only uses the new runtime-only rerun.

What it shows:
- percent delta vs `no_break`
- absolute mean runtime by row
- hotness vs runtime impact
- a row table with exact keys and all 3 run samples

Important dashboard takeaway:
- the new `base_pages` row sits much closer to `no_break`
- the hottest key, `hot#1`, is also the slowest split-only row in this rerun
- the access-count vs runtime-delta correlation in this rerun is strongly
  positive: `+0.867`

## Small Runtime Notes

The first replay attempt failed because Redis was not actually reachable inside
the container on `127.0.0.1:6379`. I started Redis inside `clever_hofstadter`
with:
- `redis-server ./config/redis.conf`

After Redis finished loading the `4.0G` snapshot and replied with `PONG`, the
rerun completed successfully.

## Final File List

Code changes:
- `python/kernmlops/data_collection/replay_trace.py`
- `python/kernmlops/analysis/generate_breakpoints.py`
- `testing/test_replay_trace.py`
- `temp_data_analysis/basepages_10keys_analysis.py`
- `temp_data_analysis/basepages_10keys_dashboard.html`

New experiment data:
- `data/test3_basepages_breakpoints_10keys_12rows.parquet`
- `data/test3_basepages_results_10keys_12rows.parquet`

New analysis outputs:
- `temp_data_analysis/basepages_10keys_metrics.json`
- `temp_data_analysis/basepages_10keys_rows.json`
- `temp_data_analysis/basepages_10keys_summary.md`
- `temp_data_analysis/basepages_10keys_runtime_impact.png`
- `temp_data_analysis/basepages_10keys_dashboard.html`

## Follow-On Documentation

There is now a separate end-to-end runbook for this workflow:

- `replay.md`

That file explains how to:

- start from a prepared environment
- capture `snapshot.rdb` and `monitor_run.log`
- generate breakpoint matrices
- run runtime-only replay
- run host-root dTLB replay
- regenerate the analysis artifacts and dashboard
