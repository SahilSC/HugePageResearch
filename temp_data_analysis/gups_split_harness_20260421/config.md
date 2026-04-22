# GUPS Split Harness Config

- baseline row: `base_pages`
- baseline meaning: `THP never` with no page breaks
- `no_break` meaning: `THP always` with no page breaks
- split-only meaning: `THP always` with selected page breaks
- runtime percent formula: `100 * ((runtime_s_i / base_pages_runtime_s_i) - 1)`
- speedup percent formula: `100 * ((base_pages_runtime_s_i / runtime_s_i) - 1)`

- results parquet: `temp_data_analysis/gups_split_harness_20260421/raw/gups_split_results.parquet`
- metadata json: `temp_data_analysis/gups_split_harness_20260421/raw/gups_split_results.metadata.json`
- breakpoints parquet: `temp_data_analysis/gups_split_harness_20260421/raw/direct_breakpoints.parquet`
- artifacts dir: `temp_data_analysis/gups_split_harness_20260421/raw/gups_split_artifacts`
- benchmark dir: ``
- gups binary: `/users/SahilSC/kernmlops-benchmark/gups/gups`
- commands log: `temp_data_analysis/gups_split_harness_20260421/raw/gups_split_artifacts/run_commands.log`
- table_size_gib: `None`
- table_size_mib: `64`
- repeats: `4`
- updates_multiplier: `4`
- stream_seed: `7`
- runs: `3`
- collectors: `['dtlb_loads', 'dtlb_misses']`

## Included Rows

- `base_pages`: split_success_total=0, split_max_attempts_max=0, runtime_pct_vs_base_pages_mean=0.000, speedup_pct_vs_base_pages_mean=0.000
- `no_break`: split_success_total=0, split_max_attempts_max=0, runtime_pct_vs_base_pages_mean=-49.202, speedup_pct_vs_base_pages_mean=96.937
- `(3) hot page #1`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=52.639, speedup_pct_vs_base_pages_mean=-34.427
- `(3) hot page #2`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=52.347, speedup_pct_vs_base_pages_mean=-34.330
- `(3) random page #1`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=51.935, speedup_pct_vs_base_pages_mean=-34.128

## Omitted Split-Only Rows

- none
