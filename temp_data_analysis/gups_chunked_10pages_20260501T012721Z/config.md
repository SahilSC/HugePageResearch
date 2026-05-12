# GUPS Split Harness Config

- baseline row: `base_pages`
- baseline meaning: `THP never` with no page breaks
- `no_break` meaning: `THP always` with no page breaks
- split-row meaning: `THP always` with selected page breaks
- runtime percent formula: `100 * ((runtime_s_i / base_pages_runtime_s_i) - 1)`
- speedup percent formula: `100 * ((base_pages_runtime_s_i / runtime_s_i) - 1)`

- results parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_results.parquet`
- metadata json: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_results.metadata.json`
- breakpoints parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/breakpoints.parquet`
- artifacts dir: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts`
- benchmark dir: `/users/SahilSC/kernmlops-benchmark`
- gups binary: `/users/SahilSC/kernmlops-benchmark/gups/gups`
- commands log: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_chunked_10pages_20260501T012721Z/raw/gups_split_artifacts/run_commands.log`
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
- `no_break`: split_success_total=0, split_expected_successes=0, target_page_count=0, page_group=, split_max_attempts_max=0, runtime_pct_vs_base_pages_mean=-69.892, speedup_pct_vs_base_pages_mean=232.203
- `(30/30) hot pages 1 to 10`: split_success_total=30, split_expected_successes=30, target_page_count=10, page_group=hot, split_max_attempts_max=1, runtime_pct_vs_base_pages_mean=-69.371, speedup_pct_vs_base_pages_mean=226.516
- `(30/30) hot pages 11 to 20`: split_success_total=30, split_expected_successes=30, target_page_count=10, page_group=hot, split_max_attempts_max=1, runtime_pct_vs_base_pages_mean=-69.384, speedup_pct_vs_base_pages_mean=226.659
- `(30/30) hot pages 21 to 30`: split_success_total=30, split_expected_successes=30, target_page_count=10, page_group=hot, split_max_attempts_max=1, runtime_pct_vs_base_pages_mean=-69.370, speedup_pct_vs_base_pages_mean=226.536
- `(30/30) least hot pages 1 to 10`: split_success_total=30, split_expected_successes=30, target_page_count=10, page_group=least_hot, split_max_attempts_max=1, runtime_pct_vs_base_pages_mean=-69.286, speedup_pct_vs_base_pages_mean=225.637

## Omitted Split-Only Rows

- none
