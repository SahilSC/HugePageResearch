# 3-Minute Replay Docker Experiment

This document is the living log for the longer replay experiment. It is kept in
sync with `PLANS.md`: progress stays timestamped, decisions stay explicit, and
surprises are recorded when they materially change the work.

## Summary

We are updating the replay flow so the all-zero breakpoint row becomes a real
`base_pages` baseline. That row must set THP to `never` before Redis restore
and then replay with no split syscalls. The first full experiment is
runtime-only. The second full experiment reuses the exact same accepted trace
and breakpoint matrix, adding only dTLB hardware collection inside Docker.

## Progress

- [x] (2026-04-08 00:00Z) Re-read `replay.md`, `generate_breakpoints.py`, and `python/kernmlops/data_collection/replay/replay_trace.py` to ground the current semantics before editing.
- [x] (2026-04-08 00:00Z) Confirmed the direct hardware collector path already uses begin/end-only counter reads and does not need a polling redesign.
- [x] (2026-04-08 00:00Z) Started implementing `base_pages` replay semantics in `replay_trace.py` and updated breakpoint docs/tests to treat the all-zero row as the sentinel baseline.
- [x] (2026-04-08 00:00Z) Repaired the replay and breakpoint tests. `PYTHONPATH=python/kernmlops .venv/bin/python -m unittest testing.test_generate_breakpoints testing.test_replay_trace testing.test_replay_hardware_collectors` now passes.
- [x] (2026-04-08 00:00Z) Rewrote `replay.md` so the runbook matches the current contract: `base_pages` first row, Docker runtime-only first round, matching Docker dTLB second round, and canonical replay entrypoint under `python/kernmlops/data_collection/replay/`.
- [x] (2026-04-08 00:00Z) Ran the tiny 5-row Docker runtime-only smoke test. The first row logged the THP-disabled `base_pages` restore, and `data/replay_3min_smoke_results_5rows.parquet` contains only `runtime_s_1` beside the breakpoint columns.
- [x] (2026-04-08 00:00Z) Ran the matching tiny 5-row Docker dTLB smoke test. `data/replay_3min_smoke_dtlb_results_5rows.parquet` contains `runtime_s_1`, `dtlb_loads_1`, and `dtlb_misses_1`, so the Docker counter path is viable here.
- [x] (2026-04-08 00:00Z) Tightened the "random" rows to random single-key rows and updated `generate_breakpoints.py`, its tests, and `replay.md` after the first smoke run showed that broad random combo rows are not stable in this delete-heavy replay.
- [x] (2026-04-08 00:00Z) Updated `scripts/capture_redis_trace.sh` to move the finished `dump.rdb` into `data/redis_traces/snapshot.rdb` instead of copying it, because the larger calibration snapshot would otherwise require two full copies on disk at once.
- [x] (2026-04-08 00:00Z) Captured and calibrated the full 12288-record trace. The first full attempt at `245760` operations produced a `96.709s` `base_pages` row, so it was rejected. The accepted retune at `458752` operations produced `177.104s` for `base_pages`, with the full 5-row calibration landing at `177.104s`, `170.844s`, `166.671s`, `172.588s`, and `170.325s`.
- [x] (2026-04-08 00:00Z) Launched the two long Docker-backed tmux runs. The runtime-only round completed first and wrote `data/replay_3min_results_22rows.parquet` with `22` rows, `runtime_s_1..3`, and no `dtlb_*` columns. The matched dTLB round then completed from the same accepted trace and wrote `data/replay_3min_dtlb_results_22rows.parquet` with `22` rows, `runtime_s_1..3`, `dtlb_loads_1..3`, and `dtlb_misses_1..3`.
- [x] (2026-04-08 00:00Z) Regenerated the paired dashboard artifacts after both long rounds completed. The final outputs are `temp_data_analysis/replay_3min_22rows_dashboard.html`, `temp_data_analysis/replay_3min_22rows_runtime_dtlb.png`, `temp_data_analysis/replay_3min_22rows_metrics.json`, `temp_data_analysis/replay_3min_22rows_rows.json`, and `temp_data_analysis/replay_3min_22rows_summary.md`.
- [x] (2026-04-08 00:00Z) Added a separate runtime-only HTML view at `temp_data_analysis/replay3min22rowsdashboard.html` so the longer replay can be read without any dTLB cards or charts.

## Surprises & Discoveries

- Observation: The analysis dashboards and summaries already describe the
  all-zero row as `base_pages`, but the actual replay loop still treated that
  row like an ordinary breakpoint vector.
  Evidence: `temp_data_analysis/basepages_10keys_analysis.py` describes
  `disable-thp yes`, while `python/kernmlops/data_collection/replay/replay_trace.py`
  still replayed the all-zero row through the normal breakpoint path.

- Observation: Docker access from this shell is blocked unless we go through a
  privileged path.
  Evidence: `docker ps` returned `permission denied while trying to connect to the docker API`.

- Observation: The older top-level replay shim file is missing from the current
  tree, so the canonical entrypoint has to be the package path.
  Evidence: `python/kernmlops/data_collection/replay_trace.py` is absent in the
  worktree, while `python/kernmlops/data_collection/replay/replay_trace.py`
  exists and the tests now import that module directly.

- Observation: Random multi-key combo rows are not robust for this delete-heavy
  replay shape because they can schedule lots of meaningless split attempts.
  Evidence: the first smoke run produced repeated `VAPTR did not resolve a
  direct pointer` failures on the random row when many keys were scheduled for
  replay-time splits.

- Observation: The larger capture needs the snapshot step to avoid duplicate
  full-size RDB copies on disk.
  Evidence: the repo only had about `13G` free after cleanup, while the older
  `4096`-record accepted snapshot is already `4.1G`, so a `12288`-record copy
  plus duplicate temporary copy would not fit.

- Observation: The replay restore path also needed to avoid duplicating the
  accepted 13G snapshot on every restore.
  Evidence: the first full capture produced a `13G`
  `replay_3min_22rows_snapshot.rdb`, and disk free space dropped to well under
  `1G` until replay was changed to stage `dump.rdb` with a hard link.

- Observation: The accepted `458752`-operation calibration no longer failed on
  the two hottest split-only rows; the only visible split warning during the
  accepted 5-row pass came from the random row.
  Evidence: accepted calibration logs showed no warnings for rows 3-4 and one
  `split_thp(...)=ENOENT` warning on row 5 for
  `user8659209663180495551`.

- Observation: The full 22-row runtime-only run has already shown at least one
  split failure on a hot row even though the 5-row accepted calibration did not
  show that warning for the same key.
  Evidence: `replay_3min_runtime` logged
  `split_thp(...)=ENOENT` for `user65420917376365171` on row 3 run 1.

## Decision Log

- Decision: Keep the current `--hot-keys` and `--random-rows` CLI shape instead
  of introducing another combo mode.
  Rationale: The row structure we need already exists. Only the meaning of the
  first all-zero row has to change.
  Date/Author: 2026-04-08 / Codex

- Decision: Remove the old `all_break` meaning instead of preserving a
  compatibility branch.
  Rationale: The user explicitly said not to spend effort on backwards
  compatibility, and the new research question needs `base_pages` to be the
  first-class baseline.
  Date/Author: 2026-04-08 / Codex

- Decision: Interpret the "3 random" rows as random single-key split rows
  rather than random many-key combo rows.
  Rationale: That matches the original user phrasing more closely, and the
  smoke run showed that broad random combo rows create invalid split attempts in
  this delete-heavy workload.
  Date/Author: 2026-04-08 / Codex

- Decision: Accept `OPERATION_COUNT=458752` for the long 22-row experiments.
  Rationale: The retuned 5-row calibration produced a `177.104s` `base_pages`
  row, which sits inside the requested `170s-190s` band while staying within
  the allowed `40x` cap for `RECORD_COUNT=12288`.
  Date/Author: 2026-04-08 / Codex

- Decision: Preserve the accepted trace by renaming the generic capture files
  instead of copying them.
  Rationale: the accepted snapshot is `13G`, so duplicating it for a nicer file
  name would exhaust the workspace filesystem.
  Date/Author: 2026-04-08 / Codex

## Outcomes & Retrospective

The full paired experiment completed successfully. The runtime-only parquet
contains all `22` rows with `runtime_s_1..3` and no accidental hardware
columns, while the matched dTLB parquet contains all `22` rows with
`runtime_s_1..3`, `dtlb_loads_1..3`, and `dtlb_misses_1..3`.

The accepted longer run stayed close to the requested three-minute target on
the `base_pages` baseline without crossing the `190s` ceiling during
calibration. In the runtime-only round, `base_pages` averaged `173.290s` and
`no_break` averaged `171.918s`, so `base_pages` landed `+0.80%` slower than the
intact-THP baseline in this replay. The dTLB round completed with the same row
ordering and same accepted inputs, and the final summary shows `base_pages`
coming in `+14.88%` higher on dTLB loads and `+6.66%` higher on dTLB misses
than `no_break`.

The main caveat is that split-only rows still showed repeated
`split_thp(...)=ENOENT` warnings during the long runs. The runtime-only log
recorded `48` such warnings and the dTLB log recorded `45`. That means the
paired dashboard should be read as "what this exact replay path measured," not
as proof that every requested split succeeded on every run.
