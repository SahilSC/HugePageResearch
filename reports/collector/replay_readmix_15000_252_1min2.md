# Replay Read-Mix `15000` `2-5-2` `1min-2`

## Overview

This report records two back-to-back runtime-only Redis replay experiments that
use the same replay row layout and the same dataset size:

- `15000` records
- `2-5-2` row layout
- `2` timed runs per row
- no dTLB collection

The two workload mixes are:

- Test 1: `100%` reads
- Test 2: `80%` reads / `20%` deletes

Both experiments calibrate independently so the `base_pages` row lands near one
minute.

## Code And Runbook Changes

The capture script now accepts YCSB operation-mix environment overrides while
keeping the old defaults when those overrides are not provided.

The new supported capture overrides are:

- `READ_PROPORTION`
- `UPDATE_PROPORTION`
- `SCAN_PROPORTION`
- `INSERT_PROPORTION`
- `READMODIFYWRITE_PROPORTION`
- `DELETE_PROPORTION`

The script now fails fast when those six proportions do not sum to `1.0`.

The runbook in `replay.md` was updated to document those overrides and to show
both new capture examples.

## Smoke Validation

### Test 1 smoke (`100%` read)

- `RECORD_COUNT=1024`
- `OPERATION_COUNT=4096`
- `5` rows
- `1` timed run per row

Observed properties:

- first replay row restored with THP `never`
- result parquet shape: `(5, 985)`
- runtime columns: `runtime_s_1`
- no `dtlb_*` columns
- runtimes: `24.188s`, `26.680s`, `27.817s`, `28.599s`, `29.183s`

### Test 2 smoke (`80%` read / `20%` delete)

- `RECORD_COUNT=1024`
- `OPERATION_COUNT=4096`
- `5` rows
- `1` timed run per row

Observed properties:

- first replay row restored with THP `never`
- result parquet shape: `(5, 984)`
- runtime columns: `runtime_s_1`
- no `dtlb_*` columns
- runtimes: `12.319s`, `12.597s`, `10.562s`, `11.472s`, `12.069s`

## Test 1: `100%` Read

### Accepted calibration

- `RECORD_COUNT=15000`
- accepted `OPERATION_COUNT=10000`
- calibration result shape: `(5, 5896)`
- calibration runtimes:
  - `base_pages`: `64.052s`
  - `no_break`: `55.270s`
  - split row: `69.864s`
  - split row: `61.367s`
  - random split row: `58.854s`

These values put the `base_pages` row inside the target `55s` to `65s` band on
the first full-size capture, so no second calibration capture was needed.

### Main artifacts

- `data/redis_traces/replay_read100_15000_252_1min2_snapshot.rdb`
- `data/redis_traces/replay_read100_15000_252_1min2_monitor_run.log`
- `data/replay_read100_15000_252_calibration_breakpoints_5rows.parquet`
- `data/replay_read100_15000_252_calibration_results_5rows.parquet`
- `data/replay_read100_15000_252_1min2_breakpoints_9rows.parquet`
- `data/replay_read100_15000_252_1min2_results_9rows.parquet`
- `data/replay_read100_15000_252_1min2_runtime.log`
- `temp_data_analysis/replay_read100_15000_252_1min2_dashboard.html`
- `temp_data_analysis/replay_read100_15000_252_1min2_metrics.json`
- `temp_data_analysis/replay_read100_15000_252_1min2_rows.json`
- `temp_data_analysis/replay_read100_15000_252_1min2_summary.md`

### Final results

The detached runtime-only Test 1 job completed successfully and wrote
`data/replay_read100_15000_252_1min2_results_9rows.parquet`.

Observed properties:

- result parquet shape: `(9, 5897)`
- runtime columns: `runtime_s_1`, `runtime_s_2`
- no `dtlb_*` columns
- timed-run range: `58.889s` to `65.441s`
- `base_pages` mean: `62.607s`
- `no_break` mean: `62.502s`
- `base_pages` vs `no_break`: `+0.17%`
- mean hot split delta vs `no_break`: `+1.21%`
- mean random split delta vs `no_break`: `-1.51%`
- split warnings logged: `8`

Generated dashboard artifacts:

- `temp_data_analysis/replay_read100_15000_252_1min2_dashboard.html`
- `temp_data_analysis/replay_read100_15000_252_1min2_metrics.json`
- `temp_data_analysis/replay_read100_15000_252_1min2_rows.json`
- `temp_data_analysis/replay_read100_15000_252_1min2_summary.md`

## Test 2: `80%` Read / `20%` Delete

### Accepted calibration

Pending until Test 1 finishes, its dashboard is generated, and disk is cleaned
up enough to keep only one preserved large snapshot at a time.

## Intermediate dTLB Pass: `100%` Read `2-2-1`

Before starting Test 2, an intermediate dTLB-instrumented replay reused the
accepted Test 1 read-heavy snapshot and monitor log with a smaller matrix:

- `5` total rows
- row 1: `base_pages`
- row 2: `no_break`
- rows 3-4: `2` hottest split-only keys
- row 5: `1` random split-only key
- `2` timed runs per row
- dTLB loads and misses enabled through `config/replay_collectors_dtlb.yaml`

### Intermediate artifacts

- `data/replay_read100_15000_221_1min2_dtlb_breakpoints_5rows.parquet`
- `data/replay_read100_15000_221_1min2_dtlb_results_5rows.parquet`
- `temp_data_analysis/replay_read100_15000_221_1min2_dtlb_dashboard.html`
- `temp_data_analysis/replay_read100_15000_221_1min2_dtlb_metrics.json`
- `temp_data_analysis/replay_read100_15000_221_1min2_dtlb_rows.json`
- `temp_data_analysis/replay_read100_15000_221_1min2_dtlb_summary.md`

### Intermediate results

Observed properties:

- result parquet shape: `(5, 5901)`
- runtime columns: `runtime_s_1`, `runtime_s_2`
- dTLB columns: `dtlb_loads_1`, `dtlb_misses_1`, `dtlb_loads_2`, `dtlb_misses_2`
- all dTLB values were non-negative
- timed-run range: `60.051s` to `68.461s`
- `base_pages` mean runtime: `62.626s`
- `no_break` mean runtime: `63.880s`
- `base_pages` runtime vs `no_break`: `-1.96%`
- `base_pages` dTLB misses: `12.544M`
- `base_pages` dTLB misses vs `no_break`: `+10.42%`
- mean hot split dTLB miss delta vs `no_break`: `-4.36%`
- random split dTLB miss delta vs `no_break`: `+0.76%`

During the live pane check, one hot split row logged a repeated
`break_page ... errno=2` warning on both runs. The result parquet still
completed successfully with all `5` rows present.

### Main artifacts

- `data/redis_traces/replay_read80_delete20_15000_252_1min2_snapshot.rdb`
- `data/redis_traces/replay_read80_delete20_15000_252_1min2_monitor_run.log`
- `data/replay_read80_delete20_15000_252_calibration_breakpoints_5rows.parquet`
- `data/replay_read80_delete20_15000_252_calibration_results_5rows.parquet`
- `data/replay_read80_delete20_15000_252_1min2_breakpoints_9rows.parquet`
- `data/replay_read80_delete20_15000_252_1min2_results_9rows.parquet`
- `data/replay_read80_delete20_15000_252_1min2_runtime.log`
- `temp_data_analysis/replay_read80_delete20_15000_252_1min2_dashboard.html`
- `temp_data_analysis/replay_read80_delete20_15000_252_1min2_metrics.json`
- `temp_data_analysis/replay_read80_delete20_15000_252_1min2_rows.json`
- `temp_data_analysis/replay_read80_delete20_15000_252_1min2_summary.md`

### Final results

Pending until the second runtime-only job completes.

## Notes And Caveats

This experiment family intentionally keeps replay runtime-only only. No dTLB
collector is enabled anywhere in the smoke runs, calibration runs, or long
replays.

The largest operational constraint is disk. The `15000`-record capture pushes
the preserved snapshot into the same large range as the earlier `15360`
experiment, so only one preserved large snapshot should be kept on disk at a
time.
