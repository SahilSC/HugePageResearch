# Replay 3-Minute 22-Row Experiment

## Overview

This report captures the longer replay experiment that uses:

- `base_pages` as the all-zero first row
- `no_break` as the intact-THP baseline
- `17` hottest split-only rows
- `3` random split-only rows
- `3` timed runs per row

The runtime-only round and the dTLB round reuse the same accepted trace and
breakpoint matrix. The only experiment difference is the dTLB collector config
added to the second round.

## What Changed

- `python/kernmlops/data_collection/replay/replay_trace.py` now treats the
  all-zero row as the `base_pages` sentinel and sets THP to `never` before
  restoring Redis for that row.
- `python/kernmlops/analysis/generate_breakpoints.py` now interprets the
  "random" rows as random single-key split rows rather than broad random
  multi-key rows.
- `scripts/capture_redis_trace.sh` now moves the finished `dump.rdb` into the
  preserved snapshot path to avoid duplicate full-size RDB copies on disk.
- Replay restore now stages large snapshots through a hard link when possible so
  the replay loop can reuse a 13G preserved snapshot without requiring another
  13G copy for every restore.

## Accepted Workload

- `RECORD_COUNT=12288`
- accepted `OPERATION_COUNT=458752`
- calibration target: `base_pages` near `180s`
- accepted 5-row calibration runtimes:
  - `base_pages`: `177.104s`
  - `no_break`: `170.844s`
  - hot split row: `166.671s`
  - hot split row: `172.588s`
  - random split row: `170.325s`

## Results

The runtime-only round completed first and wrote
`data/replay_3min_results_22rows.parquet`. It contains all `22` rows with
`runtime_s_1..3` and no `dtlb_*` columns.

The matched dTLB round then completed and wrote
`data/replay_3min_dtlb_results_22rows.parquet`. It contains all `22` rows with
`runtime_s_1..3`, `dtlb_loads_1..3`, and `dtlb_misses_1..3`.

Headline results from the generated dashboard:

- `base_pages` averaged `173.290s` in the runtime-only round.
- `no_break` averaged `171.918s` in the runtime-only round.
- `base_pages` was `+0.80%` slower than `no_break`.
- The `17` hot split-only rows averaged `-0.21%` versus `no_break`.
- The `3` random split-only rows averaged `+0.04%` versus `no_break`.
- `base_pages` dTLB loads were `+14.88%` versus `no_break`.
- `base_pages` dTLB misses were `+6.66%` versus `no_break`.
- Runtime-only timed runs ranged from `162.371s` to `181.247s`.

The final paired analysis artifacts are:

- `temp_data_analysis/replay_3min_22rows_dashboard.html`
- `temp_data_analysis/replay_3min_22rows_runtime_dtlb.png`
- `temp_data_analysis/replay_3min_22rows_metrics.json`
- `temp_data_analysis/replay_3min_22rows_rows.json`
- `temp_data_analysis/replay_3min_22rows_summary.md`

## Notes And Caveats

The replay behavior now matches the intended research contract:

- the all-zero row is `base_pages`
- `base_pages` sets THP to `never` before Redis restore
- `base_pages` replays without replay-time split syscalls
- all non-zero rows restore Redis with THP `always`
- the second long run differs from the first only by the added dTLB collector

The largest caveat is that split-only rows still logged repeated
`split_thp(...)=ENOENT` failures during the long runs. The runtime-only log
recorded `48` warnings and the matched dTLB log recorded `45`. Those warnings
show that some requested splits did not fire successfully on every run, so the
results should be interpreted as the measured behavior of this exact replay
path rather than a clean "all requested splits succeeded" dataset.

The live execution log for this work is:

- `3minlongerexperiment.md`
