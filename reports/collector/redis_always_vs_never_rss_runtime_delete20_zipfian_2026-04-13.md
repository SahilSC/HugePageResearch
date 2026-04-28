# Redis Always vs Never RSS Runtime Report With 20% Deletes And Zipfian Access

## Summary

This run repeated the Redis `always` versus `never` hugepage comparison with
the workload set to `80%` reads, `20%` deletes, and an explicit Zipfian
request distribution.

The shared configs stayed untouched. This run added:

- `config/redis_never_delete20_zipfian_rss.yaml`
- `config/redis_always_delete20_zipfian_rss.yaml`

The comparison reused the existing RSS renderer:

- `temp_data_analysis/render_redis_always_vs_never_rss_runtime.py`

## Why This Run Is Different

The workload change relative to the earlier `20%` delete report was:

- `read_proportion: 0.80`
- `delete_proportion: 0.20`
- `request_distribution: "zipfian"`

All other benchmark settings stayed aligned with the earlier RSS comparison:

- `outer_repeat: 20`
- `repeat: 1`
- `record_count: 4096`
- `operation_count: 409600`
- `field_length: 2097152`
- `mm_rss_stat` and `process_trace` enabled

Both benchmark logs explicitly include `requestdistribution=zipfian`.

## Collections

- `THP never` collection id: `20260413T112703058069`
- `THP always` collection id: `20260413T120742759288`

## Inputs Used For The Graphs

- Stdout logs:
  - `temp_data_analysis/redis_never_delete20_zipfian_20260413_collect.stdout.log`
  - `temp_data_analysis/redis_always_delete20_zipfian_20260413_collect.stdout.log`
- `THP never` curated inputs:
  - `data/curated/redis/20260413T112703058069/redis_benchmark.log`
  - `data/curated/redis/20260413T112703058069/system_info.end.parquet`
  - `data/curated/redis/20260413T112703058069/mm_rss_stat.{0..27,end}.parquet`
  - `data/curated/redis/20260413T112703058069/process_trace.{0..27,end}.parquet`
- `THP always` curated inputs:
  - `data/curated/redis/20260413T120742759288/redis_benchmark.log`
  - `data/curated/redis/20260413T120742759288/system_info.end.parquet`
  - `data/curated/redis/20260413T120742759288/mm_rss_stat.{0..29,end}.parquet`
  - `data/curated/redis/20260413T120742759288/process_trace.{0..29,end}.parquet`

## Manual `tmux` Polls

Successful runs in session `redis-rss-delete20-zipfian-20260413`:

Retained checkpoints for the completed `never` run:

- `2026-04-13T17:27:11Z`: first immediate check after start; hooks loaded and
  Redis answered `PONG`
- `2026-04-13T17:37:17Z`: pane still active; benchmark work was advancing
- `2026-04-13T18:07:00Z`: run finished cleanly; pane returned to shell and
  printed `Collection_id: 20260413T112703058069`

Directly observed `always` run checkpoints:

- `2026-04-13T18:07:50Z`: first immediate check after start; hooks loaded and
  Redis answered `PONG`
- `2026-04-13T18:18:11Z`: pane still active; benchmark log had reached
  `out_i=5`
- `2026-04-13T18:28:35Z`: pane still active; benchmark log had reached
  `out_i=11`
- `2026-04-13T18:38:47Z`: pane still active; benchmark log had reached
  `out_i=16`
- `2026-04-13T18:47:07Z`: run finished cleanly; pane returned to shell and
  printed `Collection_id: 20260413T120742759288`

## Outputs

- `temp_data_analysis/ycsb_overall_load_runtime_20260413_delete20_zipfian_always_vs_never.png`
- `temp_data_analysis/ycsb_overall_run_runtime_20260413_delete20_zipfian_always_vs_never.png`
- `temp_data_analysis/redis_rss_outer_iteration_20260413_delete20_zipfian_always_vs_never.png`
- `temp_data_analysis/redis_rss_runtime_20260413_delete20_zipfian_always_vs_never.html`

## Validation

- Static validation:
  - `PYTHONPYCACHEPREFIX=/tmp/pycache .venv/bin/python -m py_compile temp_data_analysis/render_redis_always_vs_never_rss_runtime.py`
- End-to-end render:
  - `.venv/bin/python temp_data_analysis/render_redis_always_vs_never_rss_runtime.py --never-stdout temp_data_analysis/redis_never_delete20_zipfian_20260413_collect.stdout.log --always-stdout temp_data_analysis/redis_always_delete20_zipfian_20260413_collect.stdout.log --label 20260413_delete20_zipfian`
