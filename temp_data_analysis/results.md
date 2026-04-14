# Redis THP Runtime Comparison

Generated at: 2026-04-11T02:10:28Z

- Container: `heuristic_allen`
- Session: `redis-thp-compare` on the host, because `tmux` is not installed in the container

## Commands

```bash
python python/kernmlops collect -v -c config/redis_always_outer10.yaml --benchmark redis
python python/kernmlops collect -v -c config/redis_madvise_outer10.yaml --benchmark redis
python python/kernmlops collect -v -c config/redis_never_outer10.yaml --benchmark redis
python python/kernmlops collect -v -c config/redis_always_outer10_update50_delete50.yaml --benchmark redis
python python/kernmlops collect -v -c config/redis_madvise_outer10_update50_delete50.yaml --benchmark redis
python python/kernmlops collect -v -c config/redis_never_outer10_update50_delete50.yaml --benchmark redis
```

## Per-Run Metrics

| suite | mode | collection_id | total_time_s | load_sum_s | run_sum_s | load_count | run_count |
| --- | --- | --- | --- | --- | --- | --- | --- |
| outer10 | always | 20260410T231616593579 | 1438.026 | 327.475 | 45.403 | 10 | 100 |
| outer10 | madvise | 20260410T234019589764 | 1505.956 | 363.286 | 45.542 | 10 | 100 |
| outer10 | never | 20260411T000530221402 | 1501.505 | 363.014 | 45.563 | 10 | 100 |
| outer10_update50_delete50 | always | 20260411T003036776538 | 1698.802 | 327.529 | 289.545 | 10 | 100 |
| outer10_update50_delete50 | madvise | 20260411T005900941923 | 1791.512 | 367.595 | 284.088 | 10 | 100 |
| outer10_update50_delete50 | never | 20260411T012857575133 | 1783.827 | 370.637 | 284.478 | 10 | 100 |

## Pairwise Suite Deltas

| suite | comparison | metric | lhs_value_s | rhs_value_s | delta_s | delta_pct |
| --- | --- | --- | --- | --- | --- | --- |
| outer10_update50_delete50 | always - madvise | total_time_s | 1698.802 | 1791.512 | -92.710 | -5.175 |
| outer10_update50_delete50 | always - madvise | load_sum_s | 327.529 | 367.595 | -40.066 | -10.899 |
| outer10_update50_delete50 | always - madvise | run_sum_s | 289.545 | 284.088 | 5.457 | 1.921 |
| outer10_update50_delete50 | always - never | total_time_s | 1698.802 | 1783.827 | -85.026 | -4.766 |
| outer10_update50_delete50 | always - never | load_sum_s | 327.529 | 370.637 | -43.108 | -11.631 |
| outer10_update50_delete50 | always - never | run_sum_s | 289.545 | 284.478 | 5.067 | 1.781 |
| outer10_update50_delete50 | madvise - never | total_time_s | 1791.512 | 1783.827 | 7.685 | 0.431 |
| outer10_update50_delete50 | madvise - never | load_sum_s | 367.595 | 370.637 | -3.042 | -0.821 |
| outer10_update50_delete50 | madvise - never | run_sum_s | 284.088 | 284.478 | -0.390 | -0.137 |
| outer10 | always - madvise | total_time_s | 1438.026 | 1505.956 | -67.930 | -4.511 |
| outer10 | always - madvise | load_sum_s | 327.475 | 363.286 | -35.811 | -9.858 |
| outer10 | always - madvise | run_sum_s | 45.403 | 45.542 | -0.139 | -0.305 |
| outer10 | always - never | total_time_s | 1438.026 | 1501.505 | -63.479 | -4.228 |
| outer10 | always - never | load_sum_s | 327.475 | 363.014 | -35.539 | -9.790 |
| outer10 | always - never | run_sum_s | 45.403 | 45.563 | -0.160 | -0.351 |
| outer10 | madvise - never | total_time_s | 1505.956 | 1501.505 | 4.451 | 0.296 |
| outer10 | madvise - never | load_sum_s | 363.286 | 363.014 | 0.272 | 0.075 |
| outer10 | madvise - never | run_sum_s | 45.542 | 45.563 | -0.021 | -0.046 |

## Cross-Suite Deltas

| mode | comparison | metric | base_value_s | compare_value_s | delta_s | delta_pct |
| --- | --- | --- | --- | --- | --- | --- |
| always | outer10_update50_delete50 - outer10 | total_time_s | 1438.026 | 1698.802 | 260.776 | 18.134 |
| always | outer10_update50_delete50 - outer10 | load_sum_s | 327.475 | 327.529 | 0.054 | 0.016 |
| always | outer10_update50_delete50 - outer10 | run_sum_s | 45.403 | 289.545 | 244.142 | 537.722 |
| madvise | outer10_update50_delete50 - outer10 | total_time_s | 1505.956 | 1791.512 | 285.556 | 18.962 |
| madvise | outer10_update50_delete50 - outer10 | load_sum_s | 363.286 | 367.595 | 4.309 | 1.186 |
| madvise | outer10_update50_delete50 - outer10 | run_sum_s | 45.542 | 284.088 | 238.546 | 523.793 |
| never | outer10_update50_delete50 - outer10 | total_time_s | 1501.505 | 1783.827 | 282.322 | 18.803 |
| never | outer10_update50_delete50 - outer10 | load_sum_s | 363.014 | 370.637 | 7.623 | 2.100 |
| never | outer10_update50_delete50 - outer10 | run_sum_s | 45.563 | 284.478 | 238.915 | 524.362 |

## Preserved Logs

| suite | mode | stdout_log | benchmark_log |
| --- | --- | --- | --- |
| outer10 | always | temp_data_analysis/outer10_always_collect.stdout.log | temp_data_analysis/outer10_always_redis_benchmark.log |
| outer10 | madvise | temp_data_analysis/outer10_madvise_collect.stdout.log | temp_data_analysis/outer10_madvise_redis_benchmark.log |
| outer10 | never | temp_data_analysis/outer10_never_collect.stdout.log | temp_data_analysis/outer10_never_redis_benchmark.log |
| outer10_update50_delete50 | always | temp_data_analysis/outer10_update50_delete50_always_collect.stdout.log | temp_data_analysis/outer10_update50_delete50_always_redis_benchmark.log |
| outer10_update50_delete50 | madvise | temp_data_analysis/outer10_update50_delete50_madvise_collect.stdout.log | temp_data_analysis/outer10_update50_delete50_madvise_redis_benchmark.log |
| outer10_update50_delete50 | never | temp_data_analysis/outer10_update50_delete50_never_collect.stdout.log | temp_data_analysis/outer10_update50_delete50_never_redis_benchmark.log |

## Monitoring Checks

- `[2026-04-10T23:16:48Z] session=redis-thp-compare status=alive`
- `[2026-04-10T23:36:48Z] session=redis-thp-compare status=alive`
- `[2026-04-10T23:56:48Z] session=redis-thp-compare status=alive`
- `[2026-04-11T00:16:48Z] session=redis-thp-compare status=alive`
- `[2026-04-11T00:36:48Z] session=redis-thp-compare status=alive`
- `[2026-04-11T00:56:48Z] session=redis-thp-compare status=alive`
- `[2026-04-11T01:16:48Z] session=redis-thp-compare status=alive`
- `[2026-04-11T01:36:48Z] session=redis-thp-compare status=alive`
- `[2026-04-11T01:56:48Z] session=redis-thp-compare status=alive`

## Artifacts

- [outer10 chart](redis_thp_outer10_runtime_compare.png)
- [outer10 update/delete 50/50 chart](redis_thp_outer10_update50_delete50_runtime_compare.png)
- [HTML dashboard](redis_thp_runtime_compare_dashboard.html)
