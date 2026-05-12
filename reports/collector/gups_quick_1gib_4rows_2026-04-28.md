# GUPS Quick 1 GiB / 4 Rows / 1 Run

## Summary

This quick check ran the four requested rows with a 1 GiB table:
`base_pages`, `no_break`, `hot page #1`, and `hot page #2`.

The run used `--split-mode pre_split`, `--repeats 1`, and `--runs 1`, so the
hot-page rows used the same fast timed loop as `no_break`. Their page-split
work happened before the timer started and was recorded separately in
`pre_split_events.csv`.

The full flow finished in `106s`, so it stayed well under the five-minute cap.

## Exact Paths

- Raw directory: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_20260428T183301Z/raw`
- Dashboard directory: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_20260428T183301Z`
- Runtime log: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_20260428T183301Z/raw/gups_split_runtime.log`
- Breakpoints parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_20260428T183301Z/raw/breakpoints.parquet`
- Results parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_20260428T183301Z/raw/gups_split_results.parquet`
- Metadata JSON: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_20260428T183301Z/raw/gups_split_results.metadata.json`
- Hot page #1 pre-split events: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_20260428T183301Z/raw/gups_split_artifacts/row_002/run_01/pre_split_events.csv`
- Hot page #2 pre-split events: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_20260428T183301Z/raw/gups_split_artifacts/row_003/run_01/pre_split_events.csv`

## Results

The generated breakpoint matrix contained exactly four rows:
`base_pages`, `no_break`, `hot page #1`, and `hot page #2`.

All four timed rows stayed below the `60s` per-row cap:

- `base_pages`: `18.866449s`
- `no_break`: `6.772392s`
- `hot page #1`: `6.812942s`
- `hot page #2`: `6.731181s`

The two hot-page rows landed very close to `no_break`, which is the expected
shape for a `pre_split` quick check. This is the point of `pre_split`: compare
post-split steady-state runtime without mixing in inline split-schedule work.

## Split Timing

The split work was recorded separately and excluded from `runtime_s`.

- `hot page #1`: one successful pre-timed split, `split_wall_us=542`
- `hot page #2`: one successful pre-timed split, `split_wall_us=580`

There were no inline `split_schedule.csv` files in this run, which confirms the
timed region did not use the inline split machinery.

## Output Files

The dashboard directory contains:

- `config.md`
- `runtime_mean_std.png`
- `runtime_pct_vs_base_pages.png`
- `speedup_pct_vs_base_pages.png`
- `gups_mean_std.png`
- `gups_split_harness_quick_1gib_4rows_20260428T183301Z.html`

Because this was a single-sample quick check, the rendered standard deviations
are zero for every row.

## Why This Works

`pre_split` moves the page split before the timed update loop starts. That
means:

1. the hot-page rows still run on THP `always`
2. the selected page is already split when timing begins
3. the timed loop matches the fast `no_break` path instead of the inline
   schedule-checking path

This makes the quick check suitable for answering the narrow question: “what
does runtime look like once this page is already split?”

## Tradeoffs

The quick run is fast and clean, but it is still only a single sample. It is
good for a fast shape check and for validating that `pre_split` removes the
inline overhead confounder. It is not enough to claim tight variance or a final
effect size.
