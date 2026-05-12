# GUPS Split Harness Config

- baseline row: `base_pages`
- baseline meaning: `THP never` with no page breaks
- `no_break` meaning: `THP always` with no page breaks
- split-row meaning: `THP always` with selected page breaks
- runtime percent formula: `100 * ((runtime_s_i / base_pages_runtime_s_i) - 1)`
- speedup percent formula: `100 * ((base_pages_runtime_s_i / runtime_s_i) - 1)`

- results parquet: `temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_results.parquet`
- metadata json: `temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_results.metadata.json`
- breakpoints parquet: `temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/breakpoints.parquet`
- artifacts dir: `temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_artifacts`
- benchmark dir: `/users/SahilSC/kernmlops-benchmark`
- gups binary: `/users/SahilSC/kernmlops-benchmark/gups/gups`
- commands log: `temp_data_analysis/gups_cumulative_hot_counts_20260501T021031Z/raw/gups_split_artifacts/run_commands.log`
- table_size_gib: `2`
- table_size_mib: `None`
- repeats: `1`
- updates_multiplier: `5`
- stream_seed: `7`
- runs: `3`
- split_mode: `pre_split`
- collectors: `[]`

## Included Rows

- `base_pages`: split_success_total=0, split_expected_successes=0, target_page_count=0, page_group=, split_max_attempts_max=0, runtime_pct_vs_base_pages_mean=0.000, speedup_pct_vs_base_pages_mean=0.000
- `no_break`: split_success_total=0, split_expected_successes=0, target_page_count=0, page_group=, split_max_attempts_max=0, runtime_pct_vs_base_pages_mean=-70.135, speedup_pct_vs_base_pages_mean=234.870
- `(30/30) hot 10 pages`: split_success_total=30, split_expected_successes=30, target_page_count=10, page_group=hot, split_max_attempts_max=1, runtime_pct_vs_base_pages_mean=-69.641, speedup_pct_vs_base_pages_mean=229.412
- `(300/300) hot 100 pages`: split_success_total=300, split_expected_successes=300, target_page_count=100, page_group=hot, split_max_attempts_max=1, runtime_pct_vs_base_pages_mean=-64.932, speedup_pct_vs_base_pages_mean=185.178
- `(3000/3000) hot 1000 pages`: split_success_total=3000, split_expected_successes=3000, target_page_count=1000, page_group=hot, split_max_attempts_max=1, runtime_pct_vs_base_pages_mean=-3.217, speedup_pct_vs_base_pages_mean=3.330

## Omitted Split-Only Rows

- none
