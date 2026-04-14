# Research Context

This local file is intentionally gitignored. Keep it short, current, and
useful for the next thread.

## Current Goal

- Measure whether splitting hot Redis THPs hurts replay performance, and pair
  the runtime data with dTLB load/miss metadata.

## Current Workflow

- Capture:
  - `scripts/capture_redis_trace.sh`
  - default dataset size: `4096` records
  - default run length: `45056` operations
- Breakpoint generation:
  - `python python/kernmlops/analysis/generate_breakpoints.py ...`
  - main experiment mode: `--combo-mode hotkey_harm`
  - row shape:
    - row 1: `all_break`
    - row 2: `no_break`
    - later rows: `split_only_this_hot_key`
- Replay:
  - `python python/kernmlops/data_collection/replay_trace.py ...`
  - new replay flags:
    - `--redis-bin PATH`
    - `--collect-dtlb`

## Important Semantics

- Replay splits a page when `access_counts[key] == breakpoint` before executing
  that command.
- That means:
  - `0` = split before the first access
  - `access_count + 1` = never split
- The hot-key harm matrix therefore means:
  - `all_break`: split every target before first access
  - `no_break`: leave every target unsplit
  - `split_only:<key>`: split just that key before first access and leave every
    other key unsplit

## Current Environment Split

- Stable timing path:
  - use the existing Docker container `hungry_proskuriakova`
- dTLB instrumentation path:
  - use host replay under `sudo`
  - explicit Redis binary:
    - `/tmp/redis-7.4.2/src/redis-server`
  - explicit module:
    - `/users/SahilSC/HugePageResearch/redis-module/vaptr.so`

Why:

- Host-root `PerfBPFHook` loading works.
- The same perf-event attach fails inside the active Docker container.
- Host userspace `perf` is mismatched to the custom kernel, so the repo's BPF
  path is the correct way to collect dTLB counters here.

## Current Artifacts

- Calibration:
  - `data/calibration_results_3rows.parquet`
  - `data/calibration_plus10_results_3rows.parquet`
- Main hot-key timing run:
  - `data/test1_hotkey_breakpoints_32rows.parquet`
  - `data/test1_hotkey_results_32rows.parquet`
- dTLB smoke validation:
  - `data/test2_dtlb_smoke_breakpoints_4rows.parquet`
  - `data/test2_dtlb_smoke_results_4rows.parquet`
  - `data/test2_dtlb_smoke_results_4rows_100hz.parquet`
  - `data/test2_dtlb_smoke_results_4rows_100hz_reuse.parquet`
- Full dTLB run:
  - output target: `data/test2_dtlb_results_32rows.parquet`
  - status at last update: complete
 - End-only rerun:
  - `data/test2_endpoll_results_10keys_12rows.parquet`

## Current Results

- Calibration with `45056` operations:
  - runtimes: `21.845s`, `18.698s`, `19.229s`
  - unique split targets: `4095`
  - total accesses: `45056`
- Test 1 full hot-key run:
  - full wall-clock time: `4044s` (`67.4` minutes)
  - `all_break`: `20.659s` mean
  - `no_break`: `17.765s` mean
  - slowest single-key split row:
    - `split_only:user2770635419936443086`
    - `19.019s`
  - fastest single-key split row:
    - `split_only:user4902807571609768596`
    - `17.244s`
- dTLB smoke validation:
  - original `1000 Hz` smoke proved the schema but was too expensive
  - replay now uses a lighter `100 Hz` perf sample rate
  - replay now reuses one collector across the whole benchmark job
  - updated smoke wall-clock:
    - `223s` total for `4` rows
  - updated sample rows:
    - `all_break`: `36.501s`, `2771581722` loads, `12099323` misses
    - `no_break`: `31.110s`, `13789187278` loads, `44758412` misses
- final host-root dTLB run:
  - artifact:
    - `data/test2_dtlb_results_32rows.parquet`
  - schema:
    - `32` rows
    - `runtime_s_1..3`
    - `dtlb_loads_1..3`
    - `dtlb_misses_1..3`
  - approximate wall-clock:
    - about `88` minutes
  - baselines:
    - `all_break`: `35.551s`
    - `no_break`: `31.253s`
  - split-only summary:
    - `14/30` slower than `no_break`
    - `16/30` faster than `no_break`
    - mean delta versus `no_break`: `+0.190s`
  - repeated slow rows across Test 1 and Test 2:
    - `user1009539227024920839`
    - `user2770635419936443086`
- end-only top-10-key rerun:
  - wall-clock:
    - `1670s` (`27.8` minutes)
  - baselines:
    - `all_break`: `25.206s`
    - `no_break`: `22.405s`
  - split-only summary:
    - `8/10` slower than `no_break`
    - `2/10` faster than `no_break`
    - mean delta versus `no_break`: `+0.441s`
  - hottest key:
    - `user3474737920793767716`
    - `1772` accesses
    - `-0.207s` versus `no_break`

## Current Interpretation

- The runtime-only hot-key data already shows that splitting everything is bad.
- Splitting one hot key can still slow Redis relative to `no_break`, but the
  effect is smaller and depends on which key is chosen.
- The remaining question is whether the worst hot-key rows line up with higher
  dTLB load or miss counts in a stronger way than the current simple
  correlations show. Right now the answer is "not clearly."

## Current Validation

- `PYTHONPATH=python/kernmlops .venv/bin/python -m unittest testing.test_generate_breakpoints testing.test_replay_trace`
- `python -m py_compile python/kernmlops/data_collection/replay_trace.py`
- `bash -n scripts/capture_redis_trace.sh`
- Result:
  - `Ran 21 tests ... OK`

## Next Thread Starting Point

- First check whether `data/test2_dtlb_results_32rows.parquet` exists yet.
- Main next step:
  - inspect the repeated slow rows
    - `user1009539227024920839`
    - `user2770635419936443086`
  - compare their placement and page-level behavior more directly
- Do not delete any `*results*.parquet`
