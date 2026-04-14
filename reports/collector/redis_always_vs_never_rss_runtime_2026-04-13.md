# Redis Always vs Never RSS Runtime Report

## Summary

This run compared Redis with transparent huge pages set to `never` versus
`always`, while collecting Redis RSS from `mm_rss_stat` and plotting the
results by `outer_iteration`.

The shared configs stayed untouched. This work added:

- `config/redis_never_rss.yaml`
- `config/redis_always_rss.yaml`
- `temp_data_analysis/render_redis_always_vs_never_rss_runtime.py`

The Redis benchmark launcher also now uses the repo-managed Redis `7.4.2`
binary and CLI from `/tmp/redis-7.4.2/src/` instead of the distro Redis on
`PATH`. That change was needed because `/usr/bin/redis-server` `7.0.15` could
not load `redis-module/vaptr.so`.

## Why It Works

- The new `_rss` configs keep the Redis benchmark settings the same as the
  original `always` and `never` configs, but add the `mm_rss_stat` hook next to
  `process_trace`.
- `process_trace` identifies the single Redis TGID used for each run.
- `mm_rss_stat` reconstructs Redis RSS as:
  `MM_FILEPAGES + MM_ANONPAGES + MM_SHMEMPAGES`
- `MM_SWAPENTS` is excluded.
- The renderer aligns RSS to each `outer_iteration` run completion using the
  timestamped YCSB progress line immediately before each
  `[OVERALL], RunTime(ms)` entry. The benchmark logs only emit an explicit
  `Run phase ... finished` line for the final outer iteration, so the renderer
  uses the per-phase YCSB timestamp for all outer iterations and keeps the
  explicit finish line when it is present.

## Collections

- `THP never` collection id: `20260413T040512689084`
- `THP always` collection id: `20260413T044440279869`

## Inputs Used For The Graphs

- Stdout logs:
  - `temp_data_analysis/redis_never_20260413_collect.stdout.log`
  - `temp_data_analysis/redis_always_20260413_collect.stdout.log`
- `THP never` curated inputs:
  - `data/curated/redis/20260413T040512689084/redis_benchmark.log`
  - `data/curated/redis/20260413T040512689084/system_info.end.parquet`
  - `data/curated/redis/20260413T040512689084/mm_rss_stat.{0..29,end}.parquet`
  - `data/curated/redis/20260413T040512689084/process_trace.{0..29,end}.parquet`
- `THP always` curated inputs:
  - `data/curated/redis/20260413T044440279869/redis_benchmark.log`
  - `data/curated/redis/20260413T044440279869/system_info.end.parquet`
  - `data/curated/redis/20260413T044440279869/mm_rss_stat.{0..30,end}.parquet`
  - `data/curated/redis/20260413T044440279869/process_trace.{0..30,end}.parquet`

## Manual `tmux` Polls

Successful `never` run in session `redis-rss-20260413`:

- `2026-04-13T10:05:19Z`: first immediate check after start; hooks loaded and
  Redis answered `PONG`
- `2026-04-13T10:10:57Z`: pane still active; collector output advancing
- `2026-04-13T10:21:18Z`: pane still active; benchmark log showed progress into
  later outer iterations
- `2026-04-13T10:31:31Z`: pane still active; benchmark log had reached
  `out_i=13`
- `2026-04-13T10:38:52Z`: pane still active; benchmark log had reached
  `out_i=17`
- `2026-04-13T10:44:06Z`: run finished cleanly; pane returned to shell and
  printed `Collection_id: 20260413T040512689084`

Successful `always` run in session `redis-rss-20260413`:

- `2026-04-13T10:44:47Z`: first immediate check after start; hooks loaded and
  Redis answered `PONG`
- `2026-04-13T10:54:54Z`: pane still active; benchmark log had reached
  `out_i=5`
- `2026-04-13T11:05:10Z`: pane still active; benchmark log had reached
  `out_i=11`
- `2026-04-13T11:13:24Z`: pane still active; benchmark log had reached
  `out_i=15`
- `2026-04-13T11:18:38Z`: pane still active; benchmark log had reached
  `out_i=18`
- `2026-04-13T11:22:52Z`: run finished cleanly; pane returned to shell and
  printed `Collection_id: 20260413T044440279869`

## Outputs

- `temp_data_analysis/ycsb_overall_load_runtime_20260413_always_vs_never.png`
- `temp_data_analysis/ycsb_overall_run_runtime_20260413_always_vs_never.png`
- `temp_data_analysis/redis_rss_outer_iteration_20260413_always_vs_never.png`
- `temp_data_analysis/redis_rss_runtime_20260413_always_vs_never.html`

## Validation

- Static validation:
  - `PYTHONPYCACHEPREFIX=/tmp/pycache .venv/bin/python -m py_compile temp_data_analysis/render_redis_always_vs_never_rss_runtime.py`
- Targeted tests:
  - `PYTHONPATH=python/kernmlops .venv/bin/python -m unittest testing.test_redis_runtime testing.test_redis_vaptr_benchmark`
- End-to-end render:
  - `.venv/bin/python temp_data_analysis/render_redis_always_vs_never_rss_runtime.py --never-stdout temp_data_analysis/redis_never_20260413_collect.stdout.log --always-stdout temp_data_analysis/redis_always_20260413_collect.stdout.log --label 20260413`

