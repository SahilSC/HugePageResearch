# Changes

This file is the novice-facing running log for the Redis THP replay and DTLB
work. Each milestone explains what changed, why it changed, how it was checked,
and which design decision it locked in.

## 2026-04-07 - Milestone 1: Hot-Key Matrix And Longer Capture Default

### Goal

Make the breakpoint generator produce a clear hot-key harm matrix and raise the
default capture length so replay rows can be calibrated into the 20-25 second
range without growing the dataset itself.

### Files Changed

- `python/kernmlops/analysis/generate_breakpoints.py`
- `scripts/capture_redis_trace.sh`
- `testing/test_generate_breakpoints.py`

### Behavior Changed

- `generate_breakpoints.py` now supports a `hotkey_harm` combo mode that emits:
  - row 1: all-break
  - row 2: no-break
  - remaining rows: split only one hot key while leaving every other key unsplit
- The older mixed combination matrix is still available as `compat`.
- The capture script now defaults to `40960` operations and supports temporary
  environment overrides such as `OPERATION_COUNT=36864 ./scripts/capture_redis_trace.sh`.

### Coding Decisions

- Kept the old breakpoint matrix available for compatibility instead of deleting
  it. This keeps older workflows working while making the new experiment shape
  explicit.
- Reused the existing hottest-first ordering logic so the first single-key rows
  target the most frequently accessed keys.
- Added the operation-count override in the shell script so calibration can be
  retried without editing tracked files repeatedly.

### Architectural Decisions

- The hot-key experiment is defined as "split only one chosen hot key; leave all
  others unsplit" because that directly tests whether splitting a hot page
  hurts Redis.
- The dataset size remains stable at `4096` records. Only the run-phase length
  changes, because increasing `recordcount` would also enlarge the snapshot and
  slow restore time.

### Validation Run

- Validation is pending the replay-side changes and the repaired replay tests.

### Follow-Up Risk

- The replay-side tests are still drifted and need to be repaired before the
  full calibration and DTLB runs start.

## 2026-04-07 - Milestone 2: Replay DTLB Metrics And Test Repair

### Goal

Extend replay so it can append `dtlb_loads_*` and `dtlb_misses_*` columns to
the result parquet, while also repairing the drifted replay tests to match the
current replay contract.

### Files Changed

- `python/kernmlops/data_collection/replay_trace.py`
- `testing/test_replay_trace.py`

### Behavior Changed

- `replay_trace.py` now accepts:
  - `--redis-bin PATH`
  - `--collect-dtlb`
- When `--collect-dtlb` is enabled, replay appends per-run dTLB load and miss
  counts beside the runtime columns.
- Replay now fails fast with a clear permission error if dTLB collection is
  requested without running under `sudo` or as root.
- Snapshot restore can now restart Redis with an explicit binary path instead
  of always relying on `redis-server` from `PATH`.

### Coding Decisions

- Imported the repo's BPF perf support lazily from inside `replay_trace.py`
  instead of as a top-level import. This keeps the script runnable in the
  common `python python/kernmlops/data_collection/replay_trace.py ...` mode.
- Wrapped the dTLB logic in a dedicated `ReplayDTLBCollector` helper so the
  benchmark loop still reads like: restore, purge, collect, replay, write.
- Repaired the replay tests to match the current restore path rather than
  restoring the older `RedisServerState` API that no longer exists in the file.

### Architectural Decisions

- Chose the repo's existing `PerfBPFHook` path for dTLB loads and misses.
- The collector is documented as a host-root path because:
  - host-root BPF loading works on this machine
  - the current Docker container fails to attach the same perf events
  - the host `perf` userspace binary is mismatched to the custom kernel
- Added one collector per replay run. This keeps the per-run totals easy to
  understand and avoids a more fragile cumulative-delta protocol for a novice
  reader.

### Validation Run

- `PYTHONPATH=python/kernmlops .venv/bin/python -m unittest testing.test_generate_breakpoints testing.test_replay_trace`
- `python -m py_compile python/kernmlops/data_collection/replay_trace.py`
- Result: `Ran 21 tests ... OK`

### Follow-Up Risk

- The collector path is tested with mocks, not with live dTLB data yet. The
  next step is the real calibration run followed by a host-side smoke run with
  `--collect-dtlb`.

## 2026-04-07 - Milestone 3: Replay-Length Calibration

### Goal

Find the operation count that puts the slowest 3-row calibration replay near
`22s` while keeping every row below `25s`.

### Files Changed

- `scripts/capture_redis_trace.sh`

### Behavior Changed

- Locked the default `OPERATION_COUNT` to `45056`.

### Coding Decisions

- Kept the environment override support even after locking the new default.
  That gives us a stable default for future runs while still allowing fast
  what-if checks such as `OPERATION_COUNT=32768 ...`.

### Architectural Decisions

- Used a small `3`-row calibration matrix in `hotkey_harm` mode instead of the
  full `32` rows. This isolates the timing question from the larger experiment
  and avoids spending an hour on a workload size that might still be wrong.
- Preserved both calibration result parquet files so the tuning decision has a
  paper trail.

### Validation Run

- First calibration (`40960` operations):
  - `data/calibration_results_3rows.parquet`
  - runtimes: `19.237s`, `16.526s`, `16.377s`
- Final calibration (`45056` operations):
  - `data/calibration_plus10_results_3rows.parquet`
  - runtimes: `21.845s`, `18.698s`, `19.229s`
  - unique split targets in the captured dataset: `4095`
  - total accesses in the captured run phase: `45056`

### Follow-Up Risk

- The slowest row is now in range, but the full `32`-row Test 1 wall-clock
  still needs to be measured end to end because snapshot restore dominates the
  total experiment time.

## 2026-04-07 - Milestone 4: Test 1 Hot-Key Harm Replay

### Goal

Run the full `32`-row hot-key harm matrix with `3` runs per row and measure
the complete wall-clock time from fresh capture through final parquet write.

### Files Changed

- No source files changed in this milestone.
- New durable artifacts:
  - `data/test1_hotkey_breakpoints_32rows.parquet`
  - `data/test1_hotkey_results_32rows.parquet`

### Behavior Changed

- The repo now has one full-duration non-instrumented hot-key replay dataset
  that matches the intended experiment shape:
  - `all_break`
  - `no_break`
  - `30` split-only-one-hot-key rows ordered hottest first

### Coding Decisions

- Reused the captured `45056`-operation trace and the new `hotkey_harm` mode
  instead of introducing a second experiment shape. This keeps the main timing
  dataset aligned with the hypothesis we care about.
- Kept the result file separate from `data/results.parquet` so older reference
  outputs remain available.

### Architectural Decisions

- Measured Test 1 inside the existing Docker container because the container
  replay path is the stable timing path for this machine. The later dTLB run
  uses the host for instrumentation, but the wall-clock timing experiment stays
  on the known-good container path.

### Validation Run

- Capture + breakpoint generation + replay:
  - `./scripts/capture_redis_trace.sh`
  - `python python/kernmlops/analysis/generate_breakpoints.py data/redis_traces/monitor_run.log --output data/test1_hotkey_breakpoints_32rows.parquet --max-combos 32 --combo-mode hotkey_harm`
  - `python python/kernmlops/data_collection/replay_trace.py data/redis_traces/snapshot.rdb data/redis_traces/monitor_run.log --breakpoints data/test1_hotkey_breakpoints_32rows.parquet --output data/test1_hotkey_results_32rows.parquet --runs 3 -v`
- Measured full wall-clock time:
  - `4044s` total (`67.4` minutes)
- Captured dataset facts:
  - unique split targets: `4095`
  - total accesses in the run phase: `45056`
  - hottest key access count: `1772`
- Main timing outcomes:
  - `all_break`: `20.659s` mean
  - `no_break`: `17.765s` mean
  - slowest single-key split row: `split_only:user2770635419936443086` at
    `19.019s`
  - fastest single-key split row: `split_only:user4902807571609768596` at
    `17.244s`

### Follow-Up Risk

- The non-instrumented timing result supports the expected direction
  (`all_break` is worst), but it does not yet include dTLB metadata. The next
  milestone adds those columns and checks whether the worst hot-key rows also
  show elevated dTLB activity.

## 2026-04-07 - Milestone 5: Live DTLB Smoke Validation

### Goal

Prove that the host-root replay path can append `dtlb_loads_*` and
`dtlb_misses_*` columns to a result parquet before spending hours on the full
`32`-row instrumented run.

### Files Changed

- No source files changed in this milestone.
- New durable artifacts:
  - `data/test2_dtlb_smoke_breakpoints_4rows.parquet`
  - `data/test2_dtlb_smoke_results_4rows.parquet`

### Behavior Changed

- The replay path has now been validated end to end with live host-root dTLB
  collection. The smoke parquet contains:
  - `runtime_s_1`
  - `dtlb_loads_1`
  - `dtlb_misses_1`

### Coding Decisions

- Used a `4`-row smoke matrix for the first live dTLB check:
  - `all_break`
  - `no_break`
  - hottest split-only row
  - second-hottest split-only row
- Ran the smoke with `--runs 1` because the goal was schema validation and
  collector correctness, not stable averages yet.

### Architectural Decisions

- Ran the smoke on the host under `sudo` with
  `--redis-bin /tmp/redis-7.4.2/src/redis-server` because that is the only
  environment on this machine where the repo's BPF perf hook attaches
  successfully.
- Treated the smoke runtime numbers as instrumentation validation only. This
  run streamed verbose output through a PTY, which is much noisier than the
  quiet file-backed mode used for the final long run.

### Validation Run

- Host Redis startup:
  - `/tmp/redis-7.4.2/src/redis-server ./config/redis.conf --loadmodule /users/SahilSC/HugePageResearch/redis-module/vaptr.so`
- Smoke replay:
  - `/users/SahilSC/HugePageResearch/.venv/bin/python python/kernmlops/data_collection/replay_trace.py data/redis_traces/snapshot.rdb data/redis_traces/monitor_run.log --breakpoints data/test2_dtlb_smoke_breakpoints_4rows.parquet --output data/test2_dtlb_smoke_results_4rows.parquet --runs 1 --redis-bin /tmp/redis-7.4.2/src/redis-server --collect-dtlb -v`
- Observed smoke rows:
  - `all_break`: `91.196s`, `dtlb_loads_1=30094383032`,
    `dtlb_misses_1=114866518`
  - `no_break`: `80.845s`, `dtlb_loads_1=21525308817`,
    `dtlb_misses_1=83379300`
  - `split_only:user3474737920793767716`: `80.134s`,
    `dtlb_loads_1=11725791111`, `dtlb_misses_1=53609166`
  - `split_only:user764837236214422120`: `89.461s`,
    `dtlb_loads_1=55242099947`, `dtlb_misses_1=209543188`

### Follow-Up Risk

- The smoke proves the dTLB columns land correctly, but it is not the final
  dataset. The full `32`-row, `3`-run host-root replay is still required before
  we can compare hot-key rows systematically.

## 2026-04-07 - Milestone 6: Replay-Specific dTLB Overhead Reduction

### Goal

Reduce the host-root dTLB replay overhead without changing the replay result
schema or the generic perf hook defaults used elsewhere in the repo.

### Files Changed

- `python/kernmlops/data_collection/bpf_instrumentation/perf/perf_hook.py`
- `python/kernmlops/data_collection/replay_trace.py`
- `testing/test_replay_trace.py`

### Behavior Changed

- `PerfBPFHook` now accepts a configurable `sample_freq_hz` argument and still
  defaults to `1000`.
- `ReplayDTLBCollector` now uses a replay-specific sampling rate of `100 Hz`
  instead of relying on the generic `1000 Hz` default.

### Coding Decisions

- Kept the lower sample frequency local to the replay collector instead of
  changing the default for every existing `PerfBPFHook` caller. This reduces
  risk to the rest of the repo.
- Added a focused replay test that checks the collector asks the perf hook for
  the replay-specific sampling rate.

### Architectural Decisions

- Replay only needs the final cumulative dTLB totals per CPU for each run.
  Because of that, a dense `1000 Hz` time series is unnecessary overhead in
  this workflow.
- Lowering the replay collector to `100 Hz` preserves the same result columns
  while moving more CPU time back to Redis instead of the collector.

### Validation Run

- Focused tests:
  - `PYTHONPATH=python/kernmlops .venv/bin/python -m unittest testing.test_generate_breakpoints testing.test_replay_trace`
  - result: `Ran 22 tests ... OK`
- Compile check:
  - `PYTHONPYCACHEPREFIX=/tmp/pycache python -m py_compile python/kernmlops/data_collection/replay_trace.py python/kernmlops/data_collection/bpf_instrumentation/perf/perf_hook.py`
- Updated host smoke artifacts:
  - `data/test2_dtlb_smoke_results_4rows_100hz.parquet`
- Updated smoke wall-clock:
  - `238s` total for `4` rows
- Updated smoke row outputs:
  - runtimes: `35.224s`, `32.210s`, `33.857s`, `31.620s`
  - dTLB loads: `4051830389`, `5031339604`, `2252961265`, `3100618656`
  - dTLB misses: `14599281`, `16909926`, `10519021`, `12510377`

### Follow-Up Risk

- The replay-specific collector is now much lighter, but the full `32`-row,
  `3`-run host dataset still needs to finish before we can say how stable the
  dTLB rankings are.

## 2026-04-07 - Milestone 7: dTLB Collector Reuse And File-Descriptor Fix

### Goal

Fix the host-root replay failure where long dTLB runs eventually crashed with
`OSError: [Errno 24] Too many open files`.

### Files Changed

- `python/kernmlops/data_collection/replay_trace.py`
- `python/kernmlops/data_collection/bpf_instrumentation/perf/perf_hook.py`
- `testing/test_replay_trace.py`

### Behavior Changed

- Replay now creates one `ReplayDTLBCollector` for the entire benchmark job and
  reuses it across all rows and runs.
- `PerfBPFHook.close()` now explicitly closes the perf-event group file
  descriptors it opened.

### Coding Decisions

- Reused one collector across the whole benchmark instead of creating one
  collector per replay run. This fixes the open-file leak and also avoids
  repeated BPF attach work.
- Added a replay test that checks the DTLB collector is only created once for
  the benchmark.

### Architectural Decisions

- The dTLB collector is CPU-wide and the replay path filters by Redis TGID when
  it summarizes counters. Because of that, one long-lived collector is a better
  fit than many short-lived collectors tied to individual replay runs.
- Explicitly closing the perf-event group FDs makes the hook cleanup behavior
  easier to reason about for future replay-style callers.

### Validation Run

- Failure captured before the fix:
  - long run aborted after `499s`
  - error: `OSError: [Errno 24] Too many open files`
- Focused tests after the fix:
  - `PYTHONPATH=python/kernmlops .venv/bin/python -m unittest testing.test_generate_breakpoints testing.test_replay_trace`
  - result: `Ran 22 tests ... OK`
- Compile check:
  - `PYTHONPYCACHEPREFIX=/tmp/pycache python -m py_compile python/kernmlops/data_collection/replay_trace.py python/kernmlops/data_collection/bpf_instrumentation/perf/perf_hook.py`
- Updated host smoke artifacts:
  - `data/test2_dtlb_smoke_results_4rows_100hz_reuse.parquet`
- Updated smoke wall-clock:
  - `223s` total for `4` rows
- Updated smoke row outputs:
  - runtimes: `36.501s`, `31.110s`, `31.782s`, `31.998s`
  - dTLB loads: `2771581722`, `13789187278`, `11538766998`, `19961271510`
  - dTLB misses: `12099323`, `44758412`, `39820795`, `79964146`

### Follow-Up Risk

- The file-descriptor leak is fixed for the smoke workload, but the full
  `32`-row, `3`-run host run is still the final validation that the collector
  remains stable over the whole experiment.

## 2026-04-07 - Milestone 8: Final 32-Row dTLB Run And Analysis

### Goal

Finish the full host-root hot-key experiment with `32` rows, `3` runs per row,
and appended dTLB load/miss columns.

### Files Changed

- No source files changed in this milestone.
- New durable artifact:
  - `data/test2_dtlb_results_32rows.parquet`

### Behavior Changed

- The repo now has one full `32`-row result parquet that includes:
  - `runtime_s_1..3`
  - `dtlb_loads_1..3`
  - `dtlb_misses_1..3`

### Coding Decisions

- Kept the same captured trace and the same `32`-row breakpoint matrix used by
  Test 1. That means the final comparison changes only the environment and the
  added dTLB collection, not the workload definition itself.

### Architectural Decisions

- Continued to treat the host-root dTLB run as a within-environment comparison.
  The absolute runtimes are not meant to match the Docker Test 1 runtimes
  exactly, but the row-to-row differences inside the host run are still useful.

### Validation Run

- Final artifact schema:
  - `32` rows
  - `4104` columns total
  - metric columns:
    - `runtime_s_1..3`
    - `dtlb_loads_1..3`
    - `dtlb_misses_1..3`
- Approximate wall-clock from launch to parquet write:
  - about `88` minutes
- Final baseline means:
  - `all_break`: `35.551s`
  - `no_break`: `31.253s`
  - `all_break` is `4.299s` slower than `no_break`
  - relative slowdown: about `13.8%`
- Final split-only summary:
  - `14/30` split-only rows slower than `no_break`
  - `16/30` split-only rows faster than `no_break`
  - mean split-only delta versus `no_break`: `+0.190s`
  - median split-only delta versus `no_break`: `-0.114s`
- Final hottest-key finding:
  - the hottest tested key (`user3474737920793767716`, `1772` accesses) is
    only `0.760s` slower than `no_break`
  - several of the next hottest keys are actually faster than `no_break`
  - simple correlation between access count and runtime delta is weak
    (`~0.07`)
- Final repeated slow rows across Test 1 and Test 2:
  - `user1009539227024920839`
  - `user2770635419936443086`

### Follow-Up Risk

- Raw dTLB totals and miss-rate-style normalizations do not show a strong
  simple correlation with runtime delta yet, so the current data supports
  "splitting everything hurts" more strongly than it supports
  "the hottest key always hurts the most."

## 2026-04-07 - Milestone 9: End-Only Polling And 10-Key Test 2 Rerun

### Goal

Rerun the dTLB experiment with a simpler collection policy that polls the perf
buffers only after each replay run ends, and shrink the matrix to the top `10`
 hot-key rows plus the two baseline rows.

### Files Changed

- `python/kernmlops/data_collection/replay_trace.py`
- New durable artifacts:
  - `data/test2_endpoll_smoke_breakpoints_4rows.parquet`
  - `data/test2_endpoll_smoke_results_4rows.parquet`
  - `data/test2_endpoll_breakpoints_10keys_12rows.parquet`
  - `data/test2_endpoll_results_10keys_12rows.parquet`

### Behavior Changed

- Replay now drains perf samples only after each timed replay window ends.
- The rerun matrix contains:
  - `all_break`
  - `no_break`
  - the top `10` split-only hot-key rows

### Coding Decisions

- Treated "record 10 keys" as "test the top `10` hot keys and keep the two
  baseline rows" so the smaller rerun still has a meaningful baseline.
- Kept the same captured trace and snapshot as the earlier tests so the rerun
  changes only the collector policy and matrix size.

### Architectural Decisions

- End-only polling matches what replay actually needs: the final cumulative
  dTLB totals, not an in-run time series.
- Multiple `perf_buffer_poll()` calls still happen, but they all happen after
  the counters are disabled and therefore still satisfy the "poll at the end"
  goal.

### Validation Run

- Focused tests:
  - `PYTHONPATH=python/kernmlops .venv/bin/python -m unittest testing.test_generate_breakpoints testing.test_replay_trace`
  - result: `Ran 22 tests ... OK`
- Compile check:
  - `PYTHONPYCACHEPREFIX=/tmp/pycache python -m py_compile python/kernmlops/data_collection/replay_trace.py`
- End-only smoke:
  - `data/test2_endpoll_smoke_results_4rows.parquet`
  - wall-clock: `187s`
  - runtimes: `23.542s`, `21.443s`, `21.941s`, `20.845s`
- Final 10-key rerun:
  - `data/test2_endpoll_results_10keys_12rows.parquet`
  - wall-clock: `1670s` (`27.8` minutes)
  - baselines:
    - `all_break`: `25.206s`
    - `no_break`: `22.405s`
  - split-only summary:
    - `8/10` rows slower than `no_break`
    - `2/10` rows faster than `no_break`
    - mean split-only delta versus `no_break`: `+0.441s`
  - hottest key:
    - `user3474737920793767716` (`1772` accesses)
    - `-0.207s` versus `no_break`

### Follow-Up Risk

- Even with end-only polling, the top hot key is not the worst row. The main
  conclusion still looks like "splitting everything is bad" and "some hot keys
  matter more than others."

## 2026-04-07 - Milestone 10: Temp Analysis For The New 10-Key Rerun

### Goal

Create one isolated graph and summary set under `temp_data_analysis` that only
explores the new end-only `10`-key rerun.

### Files Changed

- `temp_data_analysis/endpoll_10keys_analysis.py`
- Generated artifacts:
  - `temp_data_analysis/endpoll_10keys_runtime_impact.png`
  - `temp_data_analysis/endpoll_10keys_metrics.json`
  - `temp_data_analysis/endpoll_10keys_summary.md`

### Behavior Changed

- The repo now has a small self-contained analysis script that reads only
  `data/test2_endpoll_results_10keys_12rows.parquet` plus the matching
  `monitor_run.log` for access-count recovery.
- The generated PNG shows:
  - runtime deltas versus `no_break`
  - hotness versus runtime delta for the `10` tested keys

### Coding Decisions

- Used one two-panel PNG instead of multiple separate plots so the user can see
  both the runtime ranking and the hotness-vs-impact relationship in one file.
- Used `hot#N` labels inside the plot and put the full key names in the summary
  markdown to keep the figure readable.

### Architectural Decisions

- Kept this analysis in `temp_data_analysis` rather than the main reports
  folder because it is a focused exploratory slice of one rerun, not the main
  canonical write-up.

### Validation Run

- Ran:
  - `.venv/bin/python temp_data_analysis/endpoll_10keys_analysis.py`
- Generated metrics:
  - `all_break`: `25.206s`
  - `no_break`: `22.405s`
  - `all_break` slower than `no_break`: `12.5%`
  - `8/10` split-only rows slower than `no_break`
  - correlation between access count and runtime delta: `-0.511`

### Follow-Up Risk

- The negative hotness/runtime correlation in this smaller rerun is based on
  only `10` tested keys, so it should be treated as a useful signal, not a
  final universal rule.

## 2026-04-07 - Milestone 11: HTML Dashboard For The New 10-Key Rerun

### Goal

Create a browser-friendly page in `temp_data_analysis` that feels like the
existing mean dashboard, but only loads the new end-only `10`-key rerun.

### Files Changed

- `temp_data_analysis/endpoll_10keys_dashboard.html`
- Regenerated:
  - `temp_data_analysis/endpoll_10keys_rows.json`

### Behavior Changed

- The repo now has a dedicated HTML dashboard for
  `data/test2_endpoll_results_10keys_12rows.parquet`.
- The page renders:
  - top-level summary cards
  - runtime delta bars versus `no_break`
  - hotness versus runtime-impact scatter
  - dTLB load and miss bars
  - a detailed row table with full key names
- The page only fetches:
  - `temp_data_analysis/endpoll_10keys_metrics.json`
  - `temp_data_analysis/endpoll_10keys_rows.json`

### Coding Decisions

- Reused the visual tone of the existing dashboard so the new page feels like
  part of the same analysis set instead of a completely different artifact.
- Kept the page intentionally narrower than the full dashboard: one rerun,
  three charts, and one table, so the story is easier to scan.
- Added `endpoll_10keys_rows.json` as the row-level contract for the page
  instead of making the HTML parse parquet or markdown directly.

### Architectural Decisions

- Left this page in `temp_data_analysis` because it is an exploratory view for
  one rerun rather than the long-term canonical experiment dashboard.
- Kept the data flow browser-friendly:
  - Python analysis script produces JSON
  - HTML fetches JSON
  - charts stay fully client-side

### Validation Run

- Regenerated row-level JSON:
  - `.venv/bin/python temp_data_analysis/endpoll_10keys_analysis.py`
- Confirmed the page only references the new rerun data:
  - `rg -n "endpoll_10keys_|runtime_comparison|dtlb_comparison|hotkey_analysis|access_distribution|top_keys" temp_data_analysis/endpoll_10keys_dashboard.html`
- Checked embedded dashboard JavaScript syntax by extracting the inline script
  and running:
  - `node --check /tmp/endpoll_10keys_dashboard.js`

### Follow-Up Risk

- Like the existing dashboard, this page expects to be opened through a local
  web server because it uses `fetch()` for the JSON files.

## 2026-04-07 - Milestone 12: Percent-Based No-Break Delta In The New Dashboard

### Goal

Change the new rerun dashboard so the `no_break` comparison is shown in percent
instead of only raw seconds.

### Files Changed

- `temp_data_analysis/endpoll_10keys_dashboard.html`

### Behavior Changed

- The runtime delta bar chart now plots percent difference from `no_break`.
- The hotness-versus-impact scatter now uses percent difference from
  `no_break` on the Y-axis.
- The row table now shows the percent delta first and keeps the raw seconds in
  parentheses for context.

### Coding Decisions

- Kept the raw-second delta in tooltips and the table so the user can still
  see the absolute runtime movement while scanning percent-based charts.
- Computed percent deltas client-side from the row runtime mean and the
  `no_break` mean already present in `endpoll_10keys_metrics.json`, so no new
  Python output format was needed.

### Architectural Decisions

- Left the JSON schema unchanged because the dashboard already has everything
  it needs to derive percent deltas in the browser.

### Validation Run

- Re-checked the inline dashboard JavaScript:
  - `node --check /tmp/endpoll_10keys_dashboard.js`
- Verified the updated page text and labels reference the percent-based view:
  - `rg -n "Delta vs no_break|percent|runtime delta" temp_data_analysis/endpoll_10keys_dashboard.html`

### Follow-Up Risk

- The summary cards still keep the main baseline runtimes in seconds, so the
  page intentionally mixes absolute time for the headline numbers with percent
  deltas for the comparisons.

## 2026-04-07 - Milestone 13: Replace The Split Storm With A Base-Pages Baseline

### Goal

Stop treating the all-zero breakpoint row as "split every tracked key before
access 0" and instead make it a cleaner no-THP baseline that starts Redis with
base pages from startup.

### Files Changed

- `python/kernmlops/data_collection/replay_trace.py`
- `python/kernmlops/analysis/generate_breakpoints.py`
- `testing/test_replay_trace.py`
- New runtime-only temp analysis files:
  - `temp_data_analysis/basepages_10keys_analysis.py`
  - `temp_data_analysis/basepages_10keys_dashboard.html`
- New experiment artifacts:
  - `data/test3_basepages_breakpoints_10keys_12rows.parquet`
  - `data/test3_basepages_results_10keys_12rows.parquet`
  - `temp_data_analysis/basepages_10keys_metrics.json`
  - `temp_data_analysis/basepages_10keys_rows.json`
  - `temp_data_analysis/basepages_10keys_summary.md`
  - `temp_data_analysis/basepages_10keys_runtime_impact.png`

### Behavior Changed

- Replay now interprets the all-zero breakpoint row as a special `base_pages`
  baseline.
- For that row only, replay restarts Redis with `--disable-thp yes` and replays
  with an empty effective breakpoint map, so it does not issue a split-thp
  syscall for every tracked key.
- Every non-zero row keeps the old behavior:
  - `no_break` still means all keys stay huge
  - split-only hot-key rows still use replay-time per-key splitting
- The new temp dashboard and graph describe the first row as `base_pages`
  instead of `all_break`.

### Coding Decisions

- Used Redis's built-in `disable-thp yes` startup option instead of changing
  machine-wide THP settings per row. That keeps the special handling tightly
  scoped to the one baseline row.
- Left the breakpoint parquet format unchanged so existing generation commands
  still work. The semantic change lives in replay, not in the file shape.
- Built a new runtime-only dashboard instead of reusing the old endpoll+dTLB
  page, because this rerun is specifically about removing split-overhead
  inflation from the first row.

### Architectural Decisions

- The all-zero row change is global in replay from this point on. Historical
  parquet files are preserved as-is, but future runs should read that row as a
  base-pages baseline.
- The new dashboard is intentionally isolated in `temp_data_analysis` and only
  fetches JSON derived from `data/test3_basepages_results_10keys_12rows.parquet`.

### Validation Run

- Focused tests:
  - `PYTHONPATH=python/kernmlops .venv/bin/python -m unittest testing.test_generate_breakpoints testing.test_replay_trace`
  - result: `Ran 25 tests ... OK`
- Compile checks:
  - `PYTHONPYCACHEPREFIX=/tmp/pycache python -m py_compile python/kernmlops/data_collection/replay_trace.py python/kernmlops/analysis/generate_breakpoints.py temp_data_analysis/basepages_10keys_analysis.py`
- Container rerun:
  - container: `clever_hofstadter`
  - workload inputs reused:
    - `data/redis_traces/snapshot.rdb`
    - `data/redis_traces/monitor_run.log`
  - replay wall-clock: `1478.985s` (`24.6` minutes)
  - result parquet: `data/test3_basepages_results_10keys_12rows.parquet`
- Dashboard checks:
  - `.venv/bin/python temp_data_analysis/basepages_10keys_analysis.py`
  - `node --check /tmp/basepages_10keys_dashboard.js`

### Follow-Up Risk

- The new first row is a cleaner no-THP baseline, but it is no longer directly
  comparable in meaning to older `all_break` rows unless the write-up calls out
  the semantic change explicitly.

## 2026-04-07 - Milestone 14: End-To-End Replay Runbook

### Goal

Write one beginner-friendly document that explains how to run the Redis replay
 experiments end to end, using the same capture, breakpoint, replay, and
 dashboard flow used in the recent multi-hour experiment pass.

### Files Changed

- `replay.md`

### Behavior Changed

- The repo now has a single runbook that explains:
  - capture
  - breakpoint generation
  - runtime-only replay
  - host-root dTLB replay
  - dashboard generation
  - current row semantics, especially `base_pages`

### Coding Decisions

- Put the file at the repo root as `replay.md` because the request was for a
  directly named replay document rather than another report buried under
  `reports/`.
- Wrote it as a runbook instead of a short note so a novice can follow the
  exact commands and understand the current semantics without reading the code.

### Architectural Decisions

- The runbook points people at the shared capture inputs and current stable
  artifact names rather than inventing a new parallel workflow.
- The document explicitly separates the container timing path from the
  host-root dTLB path so readers do not accidentally compare unlike setups.

### Validation Run

- Confirmed there was no existing `replay.md` or `REPLAY.md` file before
  adding the runbook.
- Pulled the command flow and current semantics from:
  - `scripts/capture_redis_trace.sh`
  - `python/kernmlops/analysis/generate_breakpoints.py`
  - `python/kernmlops/data_collection/replay_trace.py`
  - `reports/collector/redis_trace_hotkey_experiments.md`
  - `changes2.md`

### Follow-Up Risk

- Container names change over time, so the runbook uses the recent live
  container as an example but still tells the reader to confirm with
  `docker ps`.
