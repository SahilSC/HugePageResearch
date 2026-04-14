# Redis Always vs Never RSS Runtime Report With 50% Deletes And Zipfian Access

## Summary

This run compared Redis with transparent huge pages set to `never` versus
`always` for a workload with `50%` reads, `50%` deletes, and an explicit
Zipfian request distribution.

The shared configs stayed untouched. This run added:

- `config/redis_never_delete50_zipfian_rss.yaml`
- `config/redis_always_delete50_zipfian_rss.yaml`

The comparison reused the existing RSS renderer:

- `temp_data_analysis/render_redis_always_vs_never_rss_runtime.py`

## Why This Run Is Different

The workload for this comparison used:

- `read_proportion: 0.50`
- `delete_proportion: 0.50`
- `request_distribution: "zipfian"`

All other benchmark settings stayed aligned with the earlier Redis RSS runs:

- `outer_repeat: 20`
- `repeat: 1`
- `record_count: 4096`
- `operation_count: 409600`
- `field_length: 2097152`
- `mm_rss_stat` and `process_trace` enabled

Both benchmark logs used for the graphs explicitly include
`requestdistribution=zipfian`.

## Collections

- `THP never` collection id: `20260413T125803605596`
- `THP always` collection id: `20260413T140312014590`

## Inputs Used For The Graphs

- Stdout logs:
  - `temp_data_analysis/redis_never_delete50_zipfian_20260413_collect.stdout.log`
  - `temp_data_analysis/redis_always_delete50_zipfian_rerun_20260413_collect.stdout.log`
- `THP never` curated inputs:
  - `data/curated/redis/20260413T125803605596/redis_benchmark.log`
  - `data/curated/redis/20260413T125803605596/system_info.end.parquet`
  - `data/curated/redis/20260413T125803605596/mm_rss_stat.{0..28,end}.parquet`
  - `data/curated/redis/20260413T125803605596/process_trace.{0..28,end}.parquet`
- `THP always` curated inputs:
  - `data/curated/redis/20260413T140312014590/redis_benchmark.log`
  - `data/curated/redis/20260413T140312014590/system_info.end.parquet`
  - `data/curated/redis/20260413T140312014590/mm_rss_stat.{0..30,end}.parquet`
  - `data/curated/redis/20260413T140312014590/process_trace.{0..30,end}.parquet`

## Run Notes

- The first `THP always` attempt produced partial data in
  `data/curated/redis/20260413T133658986412`, but it stopped early and never
  wrote `system_info.end.parquet` or a `Collection_id:` line.
- That partial run was excluded from the graphs.
- The final `always` graphs use the clean rerun from
  `temp_data_analysis/redis_always_delete50_zipfian_rerun_20260413_collect.stdout.log`
  and collection `20260413T140312014590`.

## Outputs

- `temp_data_analysis/ycsb_overall_load_runtime_20260413_delete50_zipfian_always_vs_never.png`
- `temp_data_analysis/ycsb_overall_run_runtime_20260413_delete50_zipfian_always_vs_never.png`
- `temp_data_analysis/redis_rss_outer_iteration_20260413_delete50_zipfian_always_vs_never.png`
- `temp_data_analysis/redis_rss_runtime_20260413_delete50_zipfian_always_vs_never.html`

## Validation

- End-to-end render:
  - `.venv/bin/python temp_data_analysis/render_redis_always_vs_never_rss_runtime.py --never-stdout temp_data_analysis/redis_never_delete50_zipfian_20260413_collect.stdout.log --always-stdout temp_data_analysis/redis_always_delete50_zipfian_rerun_20260413_collect.stdout.log --label 20260413_delete50_zipfian`
