# GUPS Split Harness 8 GiB / 10 Hot / 3 Random / 3 Runs

- Date: `2026-04-22`
- Session: `gups-split-8gib-20260422`
- Calibration results: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422/raw/calibration_results.jsonl`
- Calibration page summary: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422/raw/calibration_page_summary.csv`
- Calibration stdout: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422/raw/calibration_stdout.log`
- Breakpoints parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422/raw/breakpoints.parquet`
- Results parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422/raw/gups_split_results.parquet`
- Metadata JSON: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422/raw/gups_split_results.metadata.json`
- Runtime log: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422/raw/gups_split_runtime.log`
- Poll log: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422/raw/tmux_poll.log`
- Artifacts dir: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422/raw/gups_split_artifacts`
- Rendered dashboard dir: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422`
- Mirrored dashboard dir: `/users/SahilSC/HugePageResearch/temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422`

## Calibration

- `table_size_gib=8`, `repeats=2`, `updates_multiplier=4`, `threads=1`, `stream_seed=7`
- Calibration produced `8192` page-summary rows plus header across `2` repeats.

## Matrix Shape

- `15` rows total: `base_pages`, `no_break`, `10` hot split-only rows, `3` random split-only rows
- `3` runs per row with `5` repeats per invocation
- Collector config: `config/replay_collectors_dtlb.yaml`

## Headline Results

- `base_pages` mean runtime per repeat: `305.690s`
- `base_pages` mean total runtime per invocation: `1528.451s`
- `no_break` mean runtime per repeat: `67.743s`
- `no_break` mean total runtime per invocation: `338.717s`
- Split-only mean runtime per repeat across all split rows/runs: `184.745s`
- Split-only mean total runtime per invocation across all split rows/runs: `923.724s`

## Split Success Counts

- `hot page #1` total split successes across all runs: `3`
- `hot page #2` total split successes across all runs: `3`
- `hot page #3` total split successes across all runs: `3`
- `hot page #4` total split successes across all runs: `3`
- `hot page #5` total split successes across all runs: `3`
- `hot page #6` total split successes across all runs: `3`
- `hot page #7` total split successes across all runs: `3`
- `hot page #8` total split successes across all runs: `3`
- `hot page #9` total split successes across all runs: `3`
- `hot page #10` total split successes across all runs: `3`
- `random page #1` total split successes across all runs: `3`
- `random page #2` total split successes across all runs: `3`
- `random page #3` total split successes across all runs: `3`

## Notes

- The dashboard keeps the current graph semantics: mean runtime per repeat, not total invocation time.
- The rendered folder was mirrored into the main checkout so the graphs are easy to open there too.
