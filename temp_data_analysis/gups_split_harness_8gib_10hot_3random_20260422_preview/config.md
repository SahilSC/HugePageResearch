# GUPS Split Harness Config

- baseline row: `base_pages`
- baseline meaning: `THP never` with no page breaks
- `no_break` meaning: `THP always` with no page breaks
- split-only meaning: `THP always` with selected page breaks
- runtime percent formula: `100 * ((runtime_s_i / base_pages_runtime_s_i) - 1)`
- speedup percent formula: `100 * ((base_pages_runtime_s_i / runtime_s_i) - 1)`

- results parquet: `temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422/raw/gups_split_results.parquet`
- metadata json: `temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422_preview/gups_split_results.partial.metadata.json`
- breakpoints parquet: `temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422/raw/breakpoints.parquet`
- artifacts dir: `temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422/raw/gups_split_artifacts`
- benchmark dir: ``
- gups binary: `/users/SahilSC/kernmlops-benchmark/gups/gups`
- commands log: `temp_data_analysis/gups_split_harness_8gib_10hot_3random_20260422/raw/gups_split_artifacts/run_commands.log`
- table_size_gib: `8`
- table_size_mib: `None`
- repeats: `5`
- updates_multiplier: `4`
- stream_seed: `7`
- runs: `3`
- collectors: `['dtlb_loads', 'dtlb_misses']`

## Included Rows

- `base_pages`: split_success_total=0, split_max_attempts_max=0, runtime_pct_vs_base_pages_mean=0.000, speedup_pct_vs_base_pages_mean=0.000
- `no_break`: split_success_total=0, split_max_attempts_max=0, runtime_pct_vs_base_pages_mean=-77.839, speedup_pct_vs_base_pages_mean=351.249
- `(3) hot page #1`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=-39.618, speedup_pct_vs_base_pages_mean=65.617
- `(3) hot page #2`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=-39.505, speedup_pct_vs_base_pages_mean=65.310
- `(3) hot page #3`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=-39.509, speedup_pct_vs_base_pages_mean=65.318
- `(3) hot page #4`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=-39.601, speedup_pct_vs_base_pages_mean=65.567
- `(3) hot page #5`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=-39.789, speedup_pct_vs_base_pages_mean=66.083
- `(3) hot page #6`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=-39.596, speedup_pct_vs_base_pages_mean=65.556
- `(3) hot page #7`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=-39.675, speedup_pct_vs_base_pages_mean=65.771
- `(3) hot page #8`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=-39.364, speedup_pct_vs_base_pages_mean=64.921
- `(3) hot page #9`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=-39.563, speedup_pct_vs_base_pages_mean=65.463
- `(3) hot page #10`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=-39.212, speedup_pct_vs_base_pages_mean=64.507
- `(3) random page #1`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=-39.610, speedup_pct_vs_base_pages_mean=65.598
- `(3) random page #2`: split_success_total=3, split_max_attempts_max=2, runtime_pct_vs_base_pages_mean=-39.795, speedup_pct_vs_base_pages_mean=66.102

## Omitted Split-Only Rows

- none
