# GUPS Split Harness Config

- baseline row: `base_pages`
- baseline meaning: `THP never` with no page breaks
- `no_break` meaning: `THP always` with no page breaks
- split-only meaning: `THP always` with selected page breaks
- runtime percent formula: `100 * ((runtime_s_i / base_pages_runtime_s_i) - 1)`
- speedup percent formula: `100 * ((base_pages_runtime_s_i / runtime_s_i) - 1)`

- results parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw/gups_split_results.parquet`
- metadata json: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw/gups_split_results.metadata.json`
- breakpoints parquet: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw/breakpoints.parquet`
- artifacts dir: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw/gups_split_artifacts`
- benchmark dir: ``
- gups binary: `/users/SahilSC/kernmlops-benchmark/gups/gups`
- commands log: `/users/SahilSC/HugePageResearch-gups-harness/temp_data_analysis/gups_quick_1gib_4rows_inline_20260428T184204Z/raw/gups_split_artifacts/run_commands.log`
- table_size_gib: `1`
- table_size_mib: `None`
- repeats: `1`
- updates_multiplier: `4`
- stream_seed: `7`
- runs: `1`
- split_mode: `inline`
- collectors: `[]`

## Included Rows

- `base_pages`: split_success_total=0, split_expected_successes=0, target_page_count=0, page_group=, split_max_attempts_max=0, runtime_pct_vs_base_pages_mean=0.000, speedup_pct_vs_base_pages_mean=0.000
- `no_break`: split_success_total=0, split_expected_successes=0, target_page_count=0, page_group=, split_max_attempts_max=0, runtime_pct_vs_base_pages_mean=-63.222, speedup_pct_vs_base_pages_mean=171.903
- `(1) hot page #1`: split_success_total=1, split_expected_successes=0, target_page_count=1, page_group=hot, split_max_attempts_max=1, runtime_pct_vs_base_pages_mean=-16.732, speedup_pct_vs_base_pages_mean=20.094
- `(1) hot page #2`: split_success_total=1, split_expected_successes=0, target_page_count=1, page_group=hot, split_max_attempts_max=1, runtime_pct_vs_base_pages_mean=-18.568, speedup_pct_vs_base_pages_mean=22.802

## Omitted Split-Only Rows

- none
