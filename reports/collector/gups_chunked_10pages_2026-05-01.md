# GUPS Chunked 10-Page Pre-Split Experiment

## Summary

This run measures the steady-state GUPS runtime after breaking 10-page chunks
before the timed update loop. The matrix uses `pre_split`, so the chunk rows do
not use the inline split-schedule path.

The final run used `table_size_gib=2`, `updates_multiplier=5`, `repeats=1`,
`runs=3`, `stream_seed=7`, `threads=1`, and no replay collectors.

## Inputs And Outputs

- Raw directory: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw`
- Dashboard directory: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z`
- Runtime log: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/experiment_runtime.log`
- Experiment summary JSON: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/experiment_summary.json`
- Breakpoints parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/breakpoints.parquet`
- Results parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_results.parquet`
- Commands log: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/run_commands.log`
- HTML dashboard: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/gups_split_harness_gups_chunked_10pages_20260501T012721Z.html`
- Runtime graph: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/runtime_mean_std.png`
- GUPS graph: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/gups_mean_std.png`

## Pilot

The original 3 GiB candidate was invalid because this GUPS binary requires the
table word count to be a power of two. I switched to valid 2 GiB pilots and
tuned `updates_multiplier` to land near a 60 second `base_pages` bar.

- `2 GiB`, `updates_multiplier=4`: `44.244459s`
- `2 GiB`, `updates_multiplier=5`: `57.473676s`
- `2 GiB`, `updates_multiplier=6`: `65.439046s`

Chosen setting: `2 GiB`, `updates_multiplier=5`.

## Final Results

| Row | Runtime Values (s) | Mean Runtime (s) | Split Successes |
| --- | --- | ---: | ---: |
| `base_pages` | `55.327970`, `56.793995`, `56.359417` | `56.160461` | `0` |
| `no_break` | `16.938522`, `16.807885`, `16.972309` | `16.906239` | `0` |
| `hot pages 1 to 10` | `17.168892`, `17.283562`, `17.146711` | `17.199722` | `30/30` |
| `hot pages 11 to 20` | `17.169803`, `17.203062`, `17.203775` | `17.192213` | `30/30` |
| `hot pages 21 to 30` | `17.239937`, `17.147881`, `17.210144` | `17.199321` | `30/30` |
| `least hot pages 1 to 10` | `17.242431`, `17.176706`, `17.320919` | `17.246685` | `30/30` |

## Split Timing

Each chunk row wrote `pre_split_events.csv` for each run. There were no
`split_schedule.csv` files in the final artifact tree.

- `hot pages 1 to 10`: 30 split events, mean `751.633 us`, min `654 us`, max `860 us`
- `hot pages 11 to 20`: 30 split events, mean `762.800 us`, min `649 us`, max `886 us`
- `hot pages 21 to 30`: 30 split events, mean `766.167 us`, min `655 us`, max `886 us`
- `least hot pages 1 to 10`: 30 split events, mean `770.067 us`, min `657 us`, max `884 us`

Pre-split event files:

- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/row_002/run_01/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/row_002/run_02/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/row_002/run_03/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/row_003/run_01/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/row_003/run_02/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/row_003/run_03/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/row_004/run_01/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/row_004/run_02/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/row_004/run_03/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/row_005/run_01/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/row_005/run_02/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/row_005/run_03/pre_split_events.csv`

## Interpretation

The final `base_pages` mean was `56.160461s`, which is close to the requested
one-minute target. The `no_break` row was much faster at `16.906239s`, and all
four chunk rows were close to `no_break` at about `17.2s`.

That is consistent with the earlier quick checks: when the split happens before
the timer, the measured runtime reflects the steady-state page layout rather
than inline split-schedule overhead.
