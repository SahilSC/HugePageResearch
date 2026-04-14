# Redis Trace Hot-Key Experiments

## Why This Work Matters

This report explains how the Redis replay workflow was extended from
runtime-only measurements to runtime plus dTLB metadata, and what the first
hot-key replay results say about the current hypothesis.

The hypothesis for this round is simple: splitting frequent or hot pages should
hurt Redis. The work here turns that into a repeatable workflow with named
parquet outputs, clearer breakpoint generation, and a documented path for
collecting `dtlb_loads_*` and `dtlb_misses_*` beside the existing
`runtime_s_*` columns.

## Short Workflow Summary

There are now two closely related replay paths:

- Container timing path:
  - used for the main runtime calibration and the first full hot-key timing
    experiment
  - chosen because it was the stable replay environment already working in this
    repo
- Host-root instrumentation path:
  - used for dTLB collection
  - chosen because the repo's BPF perf hook attaches successfully on the host
    under `sudo`, but not in the active Docker container

The workflow is now:

1. Start Redis with `./config/redis.conf`.
2. Capture a YCSB-backed trace with `scripts/capture_redis_trace.sh`.
3. Generate a breakpoint matrix with
   `python/kernmlops/analysis/generate_breakpoints.py`.
4. Replay the trace with
   `python/kernmlops/data_collection/replay_trace.py`.
5. For dTLB work, replay on the host under `sudo` with `--collect-dtlb`.

One comparison rule is important: compare runtimes within the same environment.
The container timing path and the host-root dTLB path are both useful, but
their absolute runtimes should not be mixed as if they came from one identical
setup.

## Files That Changed

The implementation in this report comes from these files:

- `scripts/capture_redis_trace.sh`
- `python/kernmlops/analysis/generate_breakpoints.py`
- `python/kernmlops/data_collection/replay_trace.py`
- `testing/test_generate_breakpoints.py`
- `testing/test_replay_trace.py`
- `CHANGES.md`

`CHANGES.md` is the beginner-oriented step log. This report is the higher-level
explanation of why the workflow looks the way it does and what the experiments
showed.

## Breakpoint Matrix Change

Before this work, the default breakpoint generator produced a compatibility
matrix that mixed together:

- all-break
- no-break
- "leave one key unsplit while splitting everything else"
- random combinations

That older shape is still available as `compat`, but it is not the cleanest
way to ask the hot-page question.

The new `hotkey_harm` mode makes the main experiment easier to reason about:

- row 1: `all_break`
- row 2: `no_break`
- remaining rows: `split_only_this_hot_key`

The single-key rows are ordered by hottest key first. This means the first
single-key rows are the most interesting rows when the question is
"does splitting a hot page slow Redis down?"

## Workload Calibration

The capture script keeps the dataset size fixed at `4096` records and only
changes the run-phase operation count. This is important because increasing the
record count would also grow the snapshot and distort the restore cost.

Calibration history:

- `40960` operations:
  - result parquet: `data/calibration_results_3rows.parquet`
  - row runtimes: `19.237s`, `16.526s`, `16.377s`
- `45056` operations:
  - result parquet: `data/calibration_plus10_results_3rows.parquet`
  - row runtimes: `21.845s`, `18.698s`, `19.229s`

The second calibration hit the requested band, so `scripts/capture_redis_trace.sh`
now defaults to `OPERATION_COUNT=45056`.

Captured dataset facts for the calibrated workload:

- unique split targets: `4095`
- total accesses: `45056`
- minimum accesses per key: `1`
- maximum accesses for the hottest key: `1772`

## Test 1: Full Hot-Key Timing Run

The first full experiment used the calibrated workload, the new `hotkey_harm`
matrix, and `3` runs per row inside the existing Docker container.

Artifacts:

- breakpoint matrix: `data/test1_hotkey_breakpoints_32rows.parquet`
- result parquet: `data/test1_hotkey_results_32rows.parquet`

This matrix contains `32` rows total:

- `1` all-break row
- `1` no-break row
- `30` split-only-one-hot-key rows

The full wall-clock time from fresh capture through final parquet write was
`4044s`, or about `67.4` minutes.

### Test 1 Results

Mean runtime summary:

- `all_break`: `20.659s`
- `no_break`: `17.765s`
- slowest single-key split row:
  - `split_only:user2770635419936443086`
  - `19.019s`
- fastest single-key split row:
  - `split_only:user4902807571609768596`
  - `17.244s`

Initial interpretation:

- Splitting everything is clearly the worst baseline.
- Many single-hot-key rows stay close to `no_break`.
- Some hot-key rows are still slower than `no_break`, but the slowdown is much
  smaller than the all-break case.

So the current runtime-only data supports the broad expectation that more
splitting hurts Redis, but it does not yet show that every hot-key split is
catastrophic by itself. The dTLB data is meant to help explain the worst rows.

### Short Exploration From Test 1

A quick time-boxed exploration pass on `data/test1_hotkey_results_32rows.parquet`
showed:

- `26` of the `30` split-only rows were slower than `no_break`
- `4` of the `30` split-only rows were faster than `no_break`
- mean delta versus `no_break`: `+0.341s`
- median delta versus `no_break`: `+0.318s`
- simple correlation between key access count and slowdown:
  - about `0.03`

This is an important nuance. The single-key rows usually are slower than
`no_break`, but hotter keys are not obviously the slowest rows just from access
count alone. That is exactly why the dTLB columns matter: they may explain
which hot-key splits are expensive even when raw hotness does not.

## How dTLB Collection Was Added

The important implementation change is in
`python/kernmlops/data_collection/replay_trace.py`.

Before this work, each replay row only wrote:

- `runtime_s_1`
- `runtime_s_2`
- `runtime_s_3`

After this work, replay can also write:

- `dtlb_loads_1`
- `dtlb_loads_2`
- `dtlb_loads_3`
- `dtlb_misses_1`
- `dtlb_misses_2`
- `dtlb_misses_3`

### New Replay Flags

Two new CLI flags were added:

- `--redis-bin PATH`
  - tells replay which Redis binary to restart after restoring the snapshot
- `--collect-dtlb`
  - enables the dTLB perf-collection path

### Why The dTLB Path Uses Host Root

This repo already had a BPF perf hook implementation, so the main question was
where it could be used reliably on this machine.

Observed environment behavior:

- Host under `sudo`:
  - the repo's `PerfBPFHook` loads and polls successfully
- Active Docker container:
  - the same perf-event attach fails
- Host `perf` userspace tool:
  - mismatched to the custom kernel, so it is not the preferred integration
    point here

That led to the final architecture:

- keep container replay for the stable timing path
- use host-root replay for dTLB collection
- fail fast if `--collect-dtlb` is requested without root privileges

### Replay Collector Structure

The new replay-time dTLB path is intentionally small and explicit:

1. Restore the snapshot.
2. Find the Redis TGID for that replay run.
3. Create a `ReplayDTLBCollector`.
4. Enable counters for only the timed replay window.
5. Replay the monitor log.
6. Disable counters and summarize totals for that Redis TGID.
7. Append the totals to the same output row as the runtime.

This keeps the result parquet easy to read because every row still describes
one breakpoint configuration, and every `*_1`, `*_2`, `*_3` column still refers
to one timed run of that same configuration.

## Test 2 Smoke Validation

Before launching the long dTLB run, a smaller host-root smoke test checked that
the new columns were really written.

Artifacts:

- breakpoint matrix: `data/test2_dtlb_smoke_breakpoints_4rows.parquet`
- result parquet: `data/test2_dtlb_smoke_results_4rows.parquet`

Smoke rows:

- `all_break`
- `no_break`
- hottest split-only row
- second-hottest split-only row

The first live smoke used the original `1000 Hz` perf-hook default and proved
the schema, but it also showed too much overhead for a practical long run. That
led to one more replay-specific refinement: keep the generic perf hook default
at `1000 Hz`, but let replay request a lighter `100 Hz` sample rate because it
only needs the final cumulative dTLB totals.

Updated smoke artifacts after that change:

- result parquet: `data/test2_dtlb_smoke_results_4rows_100hz.parquet`

Updated smoke output:

- `all_break`: `35.224s`, `dtlb_loads_1=4051830389`,
  `dtlb_misses_1=14599281`
- `no_break`: `32.210s`, `dtlb_loads_1=5031339604`,
  `dtlb_misses_1=16909926`
- `split_only:user3474737920793767716`: `33.857s`,
  `dtlb_loads_1=2252961265`, `dtlb_misses_1=10519021`
- `split_only:user764837236214422120`: `31.620s`,
  `dtlb_loads_1=3100618656`, `dtlb_misses_1=12510377`

This smoke run was primarily a schema and wiring check. It proved that:

- the host-root replay path works end to end
- the result parquet gets both dTLB columns
- the new metrics are non-zero and row-specific
- replay can use a lighter perf sampling rate without losing the dTLB columns

One more issue showed up during the first long `32`-row restart: replay
eventually failed with `OSError: [Errno 24] Too many open files`. The cause was
that replay created a brand-new `ReplayDTLBCollector` on every run, which kept
opening new perf-event file descriptors.

That led to the final collector design:

- one long-lived `ReplayDTLBCollector` per benchmark job
- explicit perf-event group-FD cleanup in `PerfBPFHook.close()`

The corrected smoke artifact is:

- result parquet: `data/test2_dtlb_smoke_results_4rows_100hz_reuse.parquet`

Corrected smoke output:

- `all_break`: `36.501s`, `dtlb_loads_1=2771581722`,
  `dtlb_misses_1=12099323`
- `no_break`: `31.110s`, `dtlb_loads_1=13789187278`,
  `dtlb_misses_1=44758412`
- `split_only:user3474737920793767716`: `31.782s`,
  `dtlb_loads_1=11538766998`, `dtlb_misses_1=39820795`
- `split_only:user764837236214422120`: `31.998s`,
  `dtlb_loads_1=19961271510`, `dtlb_misses_1=79964146`

This is the smoke configuration the final long run now uses.

## Final dTLB Run

The final host-root artifact is:

- `data/test2_dtlb_results_32rows.parquet`

This parquet has:

- `32` rows
- `runtime_s_1..3`
- `dtlb_loads_1..3`
- `dtlb_misses_1..3`

Approximate wall-clock time from launch to parquet write was about `88`
minutes.

### Final Runtime Summary

Host-root baseline means:

- `all_break`: `35.551s`
- `no_break`: `31.253s`

So on the host-root dTLB path, splitting everything was about `4.299s` slower
than `no_break`, or roughly `13.8%` slower.

Split-only row summary:

- `14` of `30` split-only rows were slower than `no_break`
- `16` of `30` split-only rows were faster than `no_break`
- mean split-only delta versus `no_break`: `+0.190s`
- median split-only delta versus `no_break`: `-0.114s`

The slowest split-only rows in the final dTLB run were:

- `user1009539227024920839`: `+2.121s` versus `no_break`
- `user2770635419936443086`: `+1.946s`
- `user9089019697497438129`: `+1.849s`
- `user1715321609418666813`: `+1.684s`
- `user974154465547186715`: `+1.599s`

Two of those rows were already among the slowest rows in Test 1:

- `user1009539227024920839`
- `user2770635419936443086`

That overlap makes them stronger candidates for follow-up than rows that only
appeared once.

### What The Final Data Says About Hot Keys

The final data does show a runtime penalty from excessive splitting:

- `all_break` is clearly worse than `no_break`

But it does **not** show a simple rule that the hottest tested key always has
the biggest runtime impact.

Examples from the tested keys:

- hottest tested key:
  - `user3474737920793767716`
  - `1772` accesses
  - only `+0.760s` versus `no_break`
- second hottest tested key:
  - `user764837236214422120`
  - `926` accesses
  - only `+0.396s`
- third through sixth hottest tested keys were actually faster than `no_break`

The simple correlation between access count and runtime delta in the final
host-root run was only about `0.07`, which is very weak.

So the better summary is:

- splitting everything hurts a lot
- splitting one chosen key can hurt, but the effect depends on more than just
  raw access count

### What The dTLB Columns Add

The final parquet does contain stable dTLB totals for every run, but the
relationship to runtime is not a clean monotonic one yet.

Observations:

- `no_break` actually has higher raw dTLB loads and misses than `all_break`
- normalized comparisons such as miss rate and misses-per-second also do not
  line up cleanly with runtime slowdown
- simple correlations between runtime delta and dTLB deltas were weak

This means the current dTLB data is useful context, but it does not by itself
explain the runtime outliers in a simple one-number way.

## Testing And Validation

Source-level validation already completed:

- `PYTHONPATH=python/kernmlops .venv/bin/python -m unittest testing.test_generate_breakpoints testing.test_replay_trace`
- `python -m py_compile python/kernmlops/data_collection/replay_trace.py`
- `bash -n scripts/capture_redis_trace.sh`

Observed result:

- `Ran 21 tests ... OK`

## Takeaway

This work already established three useful things:

1. The hot-key experiment shape is now much easier to interpret.
2. The calibrated workload produces the requested `20-25s` timing range on the
   stable container timing path.
3. The replay pipeline can now append dTLB loads and misses to the same result
   parquet as the runtime columns.

The strongest conclusion from this round is:

- aggressive splitting is bad for Redis

The weaker conclusion is:

- hot-key splitting can matter, but raw key hotness alone is not a strong
  predictor of slowdown in this dataset

The best next follow-up is to investigate the repeat offenders
`user1009539227024920839` and `user2770635419936443086`, because they stayed
slow across both the container timing run and the host-root dTLB run.

## End-Only Polling Rerun

After the main `32`-row run, a smaller rerun answered a follow-up question:

- poll the perf buffers only after each replay run ends
- keep only the top `10` hot-key rows plus the two baselines

Artifacts:

- `data/test2_endpoll_smoke_results_4rows.parquet`
- `data/test2_endpoll_results_10keys_12rows.parquet`

The final rerun took about `27.8` minutes wall-clock.

Rerun baseline means:

- `all_break`: `25.206s`
- `no_break`: `22.405s`

So the smaller rerun still shows the same big-picture result:

- `all_break` is worse than `no_break`

For the `10` tested hot-key rows:

- `8/10` were slower than `no_break`
- `2/10` were faster than `no_break`
- mean split-only delta versus `no_break`: `+0.441s`

One useful nuance is that the hottest tested key was not the worst row:

- `user3474737920793767716`
- `1772` accesses
- `-0.207s` versus `no_break`

So the end-only rerun reinforces the same overall message as the larger runs:

- aggressive splitting hurts
- some single-key splits hurt
- raw hotness alone still does not explain which keys hurt the most
