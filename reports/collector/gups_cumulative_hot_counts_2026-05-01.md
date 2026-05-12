# GUPS Cumulative Hot-Page Count Experiment

## Summary

This run measures the steady-state performance change after pre-splitting
cumulative hot-page prefixes: `1-10`, `1-100`, and `1-1000`.

The run reused the deterministic calibration from the prior 2 GiB chunked
experiment and generated a new sparse cumulative breakpoint matrix. The final
matrix used `table_size_gib=2`, `updates_multiplier=5`, `repeats=1`, `runs=3`,
`stream_seed=7`, `threads=1`, `split_mode=pre_split`, and no replay collectors.

## Inputs And Outputs

- Raw directory: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw`
- Dashboard directory: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z`
- Runtime log: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/experiment_runtime.log`
- Calibration summary copied from: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/calibration_page_summary.csv`
- Breakpoints parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/breakpoints.parquet`
- Results parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_results.parquet`
- Commands log: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_artifacts/run_commands.log`
- HTML dashboard: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/gups_split_harness_gups_cumulative_hot_counts_20260501T021031Z.html`
- Runtime graph: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/runtime_mean_std.png`
- Runtime count curve: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/runtime_by_broken_page_count.png`
- Runtime percent count curve: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/runtime_pct_vs_base_pages_by_broken_page_count.png`
- Speedup percent count curve: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/speedup_pct_vs_base_pages_by_broken_page_count.png`
- GUPS count curve: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/gups_by_broken_page_count.png`

## Breakpoint Shape

The generator was extended with `--multi-page-count-list`, then run with:

```bash
PYTHONPATH=python/kernmlops .venv/bin/python \
  python/kernmlops/replay/generate_gups_breakpoints.py \
  temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/calibration_page_summary.csv \
  --output temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/breakpoints.parquet \
  --multi-page-count-list 10,100,1000 \
  --multi-page-groups hot \
  --hot-pages 0 \
  --random-rows 0
```

Rows emitted:

- `base_pages`
- `no_break`
- `hot 10 pages`
- `hot 100 pages`
- `hot 1000 pages`

## Final Results

| Row | Mean Runtime (s) | Std Runtime (s) | Split Successes |
| --- | ---: | ---: | ---: |
| `base_pages` | `56.605811` | `0.458547` | `0` |
| `no_break` | `16.904142` | `0.048343` | `0` |
| `hot 10 pages` | `17.183944` | `0.038307` | `30/30` |
| `hot 100 pages` | `19.849346` | `0.014339` | `300/300` |
| `hot 1000 pages` | `54.787442` | `0.959688` | `3000/3000` |

## Split Timing

Each split row wrote `pre_split_events.csv` for each run. There were no
`split_schedule.csv` files in the artifact tree.

- `hot 10 pages`: 30 split events, mean `785.633 us`, min `648 us`, max `1268 us`
- `hot 100 pages`: 300 split events, mean `1962.323 us`, min `651 us`, max `3369 us`
- `hot 1000 pages`: 3000 split events, mean `14298.600 us`, min `645 us`, max `28402 us`

Pre-split event files:

- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_artifacts/row_002/run_01/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_artifacts/row_002/run_02/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_artifacts/row_002/run_03/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_artifacts/row_003/run_01/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_artifacts/row_003/run_02/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_artifacts/row_003/run_03/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_artifacts/row_004/run_01/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_artifacts/row_004/run_02/pre_split_events.csv`
- `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_artifacts/row_004/run_03/pre_split_events.csv`

## Interpretation

Breaking the hottest 10 pages did not materially change the steady-state
runtime versus `no_break`. Breaking the hottest 100 pages increased runtime to
about `19.85s`, still much closer to `no_break` than to `base_pages`. Breaking
the hottest 1000 pages moved runtime to `54.79s`, close to the `base_pages`
runtime of `56.61s`.

This is the curve expected from progressively removing most of the intact THP
coverage in a 2 GiB table with 1024 total 2 MiB regions.
