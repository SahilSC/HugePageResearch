# Redis Runtime Search Setup

- branch: `redis_runtimes`
- container target: `heuristic_allen`
- status: implementation complete, overnight run ready to launch

## What changed

- Added [`runtime_search.py`](/users/SahilSC/HugePageResearch/python/kernmlops/experiments/redis_thp_replication/runtime_search.py) as a dedicated overnight Redis THP runtime search runner.
- Added [`helper_pressure.py`](/users/SahilSC/HugePageResearch/python/kernmlops/experiments/redis_thp_replication/helper_pressure.py) for the bounded stage-2 helper-pressure pivot.
- Added [`run_redis_runtime_search_in_container.sh`](/users/SahilSC/HugePageResearch/temp_data_analysis/run_redis_runtime_search_in_container.sh) to launch the search from a host-side `tmux` session into the newest running container.
- Added [`monitor_redis_runtime_search.sh`](/users/SahilSC/HugePageResearch/temp_data_analysis/monitor_redis_runtime_search.sh) to poll the `tmux` session every 20 minutes and record stage, candidate, and runner state.
- Added [`test_redis_runtime_search.py`](/users/SahilSC/HugePageResearch/testing/test_redis_runtime_search.py) coverage for candidate generation, success rules, ranking, timeout handling, and resume-manifest round trips.

## Why this works

- The search uses YCSB run `[OVERALL], RunTime(ms)` from per-phase run logs as the primary success metric.
- Stage 1 screens broad candidate families, calibrates toward an 8-12 minute run window, and compares `always` against `never` only.
- Stage 2 pivots to warmup and bounded helper-pressure variants if no clear stage-1 winner appears by the 4.5-hour checkpoint.
- The runner now fails fast if THP/procfs tuning files are not writable, so it does not silently die in a non-privileged container.
- Checkpoints and latest artifacts are persisted during the run, including the stage-2 pivot.

## Validation

- `.venv/bin/python -m py_compile python/kernmlops/experiments/redis_thp_replication/runtime_search.py python/kernmlops/experiments/redis_thp_replication/helper_pressure.py`
- `.venv/bin/python -m unittest testing.test_redis_runtime_search testing.test_redis_vaptr_benchmark`
- `python python/kernmlops/experiments/redis_thp_replication/runtime_search.py --no-execute --container-name heuristic_allen --branch-name redis_runtimes`
- Container preflight confirmed writable:
  - `/sys/kernel/mm/transparent_hugepage/enabled`
  - `/sys/kernel/mm/transparent_hugepage/khugepaged/scan_sleep_millisecs`
  - `/proc/sys/vm/overcommit_memory`
  - `/proc/sys/vm/drop_caches`

## Launch

- Host runner: `temp_data_analysis/run_redis_runtime_search_in_container.sh`
- Host monitor: `temp_data_analysis/monitor_redis_runtime_search.sh`
- Expected latest outputs:
  - `temp_data_analysis/redis_runtime_search_latest_status.json`
  - `temp_data_analysis/redis_runtime_search_latest_manifest.json`
  - `temp_data_analysis/redis_runtime_search_latest.html`
  - `temp_data_analysis/redis_runtime_search_latest.md`
  - `temp_data_analysis/redis_runtime_search_monitor.log`
