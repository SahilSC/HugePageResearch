# Replay 1:1 15360 Runtime-Only Reset

## Overview

This report captures the fresh runtime-only replay reset that forces
`RECORD_COUNT == OPERATION_COUNT` exactly.

The run shape was:

- `RECORD_COUNT=15360`
- `OPERATION_COUNT=15360`
- `14` replay rows total
- `3` timed runs per row
- runtime-only only, with no dTLB collection

The replay rows were:

- `base_pages`
- `no_break`
- the `10` hottest split-only keys
- `2` random split-only non-hot keys

## Why This Run Exists

The earlier longer replay data mixed dataset size and run length by using an
operation count much larger than the record count. This reset run removes that
confounder and measures the current replay path with a true one-to-one capture.

The earlier one-minute target was dropped for this reset because the current
machine cannot support a one-to-one run anywhere near one minute under the
existing workload. The heaviest practical one-to-one count on this machine was
`15360/15360`, which still produces only about seven seconds of timed replay per
row-run.

## What Changed Before The Run

No code changes were needed.

To make room for the new capture, the old large preserved snapshots were
removed:

- `data/redis_traces/replay_3min_22rows_snapshot.rdb`
- `data/redis_traces/replay_cap1m_32rows_snapshot.rdb`
- `dump.rdb`

That cleanup was required because the filesystem only had about `133M` free
before the new capture, and `dump.rdb` was still a hard link to the old `13G`
snapshot.

## Main Artifacts

- `data/redis_traces/replay_1to1_15360_14rows_snapshot.rdb`
- `data/redis_traces/replay_1to1_15360_14rows_monitor_run.log`
- `data/replay_1to1_15360_smoke_breakpoints_5rows.parquet`
- `data/replay_1to1_15360_smoke_results_5rows.parquet`
- `data/replay_1to1_15360_breakpoints_14rows.parquet`
- `data/replay_1to1_15360_results_14rows.parquet`
- `data/replay_1to1_15360_runtime.log`
- `temp_data_analysis/replay_1to1_15360_14rows_dashboard.html`
- `temp_data_analysis/replay_1to1_15360_14rows_metrics.json`
- `temp_data_analysis/replay_1to1_15360_14rows_rows.json`
- `temp_data_analysis/replay_1to1_15360_14rows_summary.md`

## Smoke Validation

The smoke run used:

- `5` rows
- `1` timed run per row
- runtime-only only

Smoke validation passed:

- first row logged the THP-disabled `base_pages` restore
- result parquet shape was `(5, 8106)`
- result parquet contained `runtime_s_1`
- result parquet contained no `dtlb_*` columns

## Full Run Results

The full breakpoint parquet shape was `(14, 8105)`.

The final runtime-only result parquet shape was `(14, 8108)`.

It contains:

- `runtime_s_1`
- `runtime_s_2`
- `runtime_s_3`

It contains no `dtlb_*` columns.

Overall timed-run summary:

- minimum timed run: `6.276s`
- maximum timed run: `8.040s`
- mean across all `42` timed runs: `7.190s`

Baseline row means:

- `base_pages`: `7.170s`
- `no_break`: `7.282s`

So in this exact one-to-one reset run, `base_pages` was slightly faster than
`no_break` by about `0.112s`, or about `1.54%`.

The full wall-clock for the detached runtime-only job was about `36.1` minutes,
measured from tmux session creation to the result parquet write. That much
longer wall-clock is dominated by the repeated restore cycle for the preserved
`16G` snapshot, not by the timed replay itself.

## Notes And Caveats

The new one-to-one capture produced:

- `15360` total accesses
- `8105` unique split targets
- a preserved snapshot size of about `16G`

The split-only rows still showed replay-time split failures. The runtime log
recorded `27` `break_page failed` warnings. That means this dataset is still
useful as a runtime-only one-to-one reset, but it should not be interpreted as a
case where every requested split succeeded cleanly.

## Conclusion

The current workload can support a heavy one-to-one reset run on this machine,
but not a one-to-one minute-long run. The practical result here is:

- the heaviest feasible one-to-one count was `15360/15360`
- timed replay stayed around `7s`, not `60s`
- wall-clock stayed high because of repeated restores of a large snapshot

If a future experiment still wants one-to-one plus minute-long timed replays,
the next step needs to change either the workload shape or the machine/storage
budget rather than increasing this current count further.
