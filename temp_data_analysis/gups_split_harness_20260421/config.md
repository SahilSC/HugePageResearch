# GUPS Split Harness Config

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

- `base_pages`: split_success_total=0, split_max_attempts_max=0
- `no_break`: split_success_total=0, split_max_attempts_max=0
- `(3) hot page #1`: split_success_total=3, split_max_attempts_max=2
- `(3) hot page #2`: split_success_total=3, split_max_attempts_max=2
- `(3) random page #1`: split_success_total=3, split_max_attempts_max=2

## Omitted Split-Only Rows

- none
