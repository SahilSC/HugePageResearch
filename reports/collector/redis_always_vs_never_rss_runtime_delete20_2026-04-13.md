# Redis Always vs Never RSS Runtime Report With 20% Deletes

## Summary

This run repeated the Redis `always` versus `never` hugepage comparison with
the workload changed to `80%` reads and `20%` deletes.

The shared configs stayed untouched. This run added:

- `config/redis_never_delete20_rss.yaml`
- `config/redis_always_delete20_rss.yaml`

The comparison reused the existing RSS renderer:

- `temp_data_analysis/render_redis_always_vs_never_rss_runtime.py`

## Why This Run Is Different

The only workload change relative to the earlier report was:

- `read_proportion: 0.80`
- `delete_proportion: 0.20`

All other benchmark settings stayed aligned with the prior RSS comparison:

- `outer_repeat: 20`
- `repeat: 1`
- `record_count: 4096`
- `operation_count: 409600`
- `field_length: 2097152`
- `mm_rss_stat` and `process_trace` enabled

## Collections

- `THP never` collection id: `20260413T091834264729`
- `THP always` collection id: `20260413T100118363207`

## Inputs Used For The Graphs

- Stdout logs:
  - `temp_data_analysis/redis_never_delete20_20260413_collect.stdout.log`
  - `temp_data_analysis/redis_always_delete20_20260413_collect.stdout.log`
- `THP never` curated inputs:
  - `data/curated/redis/20260413T091834264729/redis_benchmark.log`
  - `data/curated/redis/20260413T091834264729/system_info.end.parquet`
  - `data/curated/redis/20260413T091834264729/mm_rss_stat.{0..27,end}.parquet`
  - `data/curated/redis/20260413T091834264729/process_trace.{0..27,end}.parquet`
- `THP always` curated inputs:
  - `data/curated/redis/20260413T100118363207/redis_benchmark.log`
  - `data/curated/redis/20260413T100118363207/system_info.end.parquet`
  - `data/curated/redis/20260413T100118363207/mm_rss_stat.{0..29,end}.parquet`
  - `data/curated/redis/20260413T100118363207/process_trace.{0..29,end}.parquet`

## Manual `tmux` Polls

Successful `never` run in session `redis-rss-delete20-20260413`:

- `2026-04-13T15:18:41Z`: first immediate check after start; hooks loaded and
  Redis answered `PONG`
- `2026-04-13T15:28:47Z`: pane still active; benchmark log had reached
  `out_i=5`
- `2026-04-13T15:39:02Z`: pane still active; benchmark log had reached
  `out_i=10`
- `2026-04-13T15:49:18Z`: pane still active; benchmark log had reached
  `out_i=15`
- `2026-04-13T15:56:30Z`: pane still active; benchmark log had reached the
  final `out_i=19` phase
- `2026-04-13T16:00:43Z`: run finished cleanly; pane returned to shell and
  printed `Collection_id: 20260413T091834264729`

Successful `always` run in session `redis-rss-delete20-20260413`:

- `2026-04-13T16:01:24Z`: first immediate check after start; hooks loaded and
  Redis answered `PONG`
- `2026-04-13T16:11:30Z`: pane still active; benchmark log had reached
  `out_i=5`
- `2026-04-13T16:21:44Z`: pane still active; benchmark log had reached
  `out_i=11`
- `2026-04-13T16:32:02Z`: pane still active; benchmark log had reached
  `out_i=16`
- `2026-04-13T16:39:14Z`: run finished cleanly; pane returned to shell and
  printed `Collection_id: 20260413T100118363207`

## Outputs

- `temp_data_analysis/ycsb_overall_load_runtime_20260413_delete20_always_vs_never.png`
- `temp_data_analysis/ycsb_overall_run_runtime_20260413_delete20_always_vs_never.png`
- `temp_data_analysis/redis_rss_outer_iteration_20260413_delete20_always_vs_never.png`
- `temp_data_analysis/redis_rss_runtime_20260413_delete20_always_vs_never.html`

## Validation

- End-to-end render:
  - `.venv/bin/python temp_data_analysis/render_redis_always_vs_never_rss_runtime.py --never-stdout temp_data_analysis/redis_never_delete20_20260413_collect.stdout.log --always-stdout temp_data_analysis/redis_always_delete20_20260413_collect.stdout.log --label 20260413_delete20`
