# Redis THP Outer-Repeat 10 Comparison

## What I changed

- Added six new matched Redis configs so the THP comparison can run without
  mutating the existing `redis_always.yaml`, `redis_madvise.yaml`, and
  `redis_never.yaml` files.
- Added `temp_data_analysis/run_redis_thp_compare_in_container.sh` to launch the
  six `collect` commands against the newest Docker container, preserve each
  `redis_benchmark.log`, and fail fast if a collection id or curated parquet is
  missing.
- Added `temp_data_analysis/monitor_redis_thp_compare.sh` so the host logs a
  pane heartbeat every 20 minutes while the long run is active.
- Added `temp_data_analysis/redis_thp_runtime_compare.py` to validate the new
  config family, parse the preserved benchmark logs, read
  `collection_time_sec`, and generate the markdown, PNG, and HTML outputs.
- Extended `testing/test_redis_thp_replication.py` with a synthetic parser test
  that verifies the `outer_repeat=10` phase grouping and fail-fast count
  checks.

## Why it works

- The new configs are symmetric across `always`, `madvise`, and `never`; the
  only intentional difference is the THP mode.
- The runner copies `redis_benchmark.log` immediately after each collection, so
  the analysis step reads stable per-run logs instead of a later overwrite.
- The report script groups YCSB `[OVERALL], RunTime(ms)` lines by the benchmark
  wrapper's existing `Load phase ... starting` and `Run phase ... starting`
  markers, which gives exact load/run totals even though the `collect` path does
  not call the benchmark's final `wait()` wrapper.
- The 20-minute monitor log preserves a simple audit trail that the long `tmux`
  run kept moving through the queue.

## Approach choices

- I kept the original configs untouched and created a new matched family instead
  of editing `redis_never.yaml` in place. That keeps older replay and benchmark
  workflows easier to reason about.
- The newest container, `heuristic_allen`, did not have `tmux` installed, so
  the long run used a host-side `tmux` session that executes inside the
  container with `sudo docker exec`. The benchmark commands themselves still ran
  in that newest container.
- I added a small post-process watcher session so the report generation starts as
  soon as the final collection finishes instead of waiting for a manual follow-up.

## Results snapshot

- Baseline `outer10` suite:
  - `always` total time: `1438.026s`
  - `madvise` total time: `1505.956s`
  - `never` total time: `1501.505s`
- `outer10_update50_delete50` suite:
  - `always` total time: `1698.802s`
  - `madvise` total time: `1791.512s`
  - `never` total time: `1783.827s`
- The 50/50 update/delete suite increased total time by about `18-19%` for all
  three THP modes, almost entirely because the summed run-phase time jumped from
  about `45.5s` to about `284-290s`.
- In both suites, `always` finished about `4-5%` faster overall than `never`
  and `madvise`, with the largest difference showing up in summed load time.

## Output files

- `temp_data_analysis/results.md`
- `temp_data_analysis/redis_thp_outer10_runtime_compare.png`
- `temp_data_analysis/redis_thp_outer10_update50_delete50_runtime_compare.png`
- `temp_data_analysis/redis_thp_runtime_compare_dashboard.html`

## Tradeoffs

- The grouped charts use `0` standard deviation because each THP mode was run
  once per suite in this collection pass. The markdown report still keeps the
  raw per-run values so a later repeated run can add variance cleanly.
- The monitor output logs the live pane text, which is easy to audit but a bit
  noisy because Redis/YCSB prints many `OK` and count lines during the run.
