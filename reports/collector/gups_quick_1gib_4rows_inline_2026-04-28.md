# GUPS Quick 1 GiB / 4 Rows / 1 Run / Inline

## Summary

This reran the same four-row 1 GiB quick matrix as the earlier `pre_split`
check, but with `--split-mode inline` instead of `--split-mode pre_split`.

To keep the comparison clean, this run reused the exact calibration outputs and
the exact four-row breakpoint matrix from the earlier quick run:

- source quick run: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_20260428T183301Z`
- reused breakpoints parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw/breakpoints.parquet`

The full inline rerun finished in `101s`, so it also stayed well under the
five-minute cap.

## Exact Paths

- Raw directory: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw`
- Dashboard directory: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z`
- Runtime log: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw/gups_split_runtime.log`
- Results parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw/gups_split_results.parquet`
- Metadata JSON: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw/gups_split_results.metadata.json`
- Hot page #1 split events: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw/gups_split_artifacts/row_002/run_01/split_events.csv`
- Hot page #2 split events: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw/gups_split_artifacts/row_003/run_01/split_events.csv`
- Hot page #1 split schedule: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw/gups_split_artifacts/row_002/run_01/split_schedule.csv`
- Hot page #2 split schedule: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw/gups_split_artifacts/row_003/run_01/split_schedule.csv`

## Results

The generated matrix still contained exactly four rows:
`base_pages`, `no_break`, `hot page #1`, and `hot page #2`.

All four timed rows still stayed below the `60s` per-row cap:

- `base_pages`: `18.387922s`
- `no_break`: `6.762669s`
- `hot page #1`: `15.311311s`
- `hot page #2`: `14.973594s`

For direct comparison, the earlier `pre_split` quick run produced:

- `base_pages`: `18.866449s`
- `no_break`: `6.772392s`
- `hot page #1`: `6.812942s`
- `hot page #2`: `6.731181s`

So the timed hot-page rows roughly doubled when the same two pages were broken
inline instead of pre-timed:

- `hot page #1`: `+8.498369s` versus `pre_split`
- `hot page #2`: `+8.242413s` versus `pre_split`

## Split Timing

The actual split events were still tiny:

- `hot page #1`: one successful inline split at page index `0`, global update
  index `1`, `split_wall_us=585`
- `hot page #2`: one successful inline split at page index `1`, global update
  index `73`, `split_wall_us=584`

That means the large runtime increase in the inline run is not caused by the
split syscall itself. The measured split wall time remained well below `1ms`
per hot-page row, but the timed hot rows still rose to about `15s`.

## Interpretation

This quick comparison matches the earlier conclusion:

1. `pre_split` isolates steady-state post-split performance because the split is
   done before timing starts.
2. `inline` keeps extra work in the timed region because GUPS must use the
   split-schedule path while it is checking for the split trigger.
3. The actual split event is still tiny, so the runtime gap between `inline`
   and `pre_split` is mostly harness-path overhead rather than split latency.

## Output Files

The dashboard directory contains:

- `config.md`
- `runtime_mean_std.png`
- `runtime_pct_vs_base_pages.png`
- `speedup_pct_vs_base_pages.png`
- `gups_mean_std.png`
- `gups_split_harness_gups_quick_1gib_4rows_inline_20260428T184204Z.html`

This is still a single-sample quick check, so the rendered standard deviations
are zero for every row.
