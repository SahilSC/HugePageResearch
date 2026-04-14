# Replay Overnight 2026-04-09

## Overview

This report tracks the April 9, 2026 overnight Redis replay matrix.

The session is runtime-only:

- no `--collector-config`
- no dTLB measurements
- no hardware counters of any kind

The overnight runner performs:

- preflight checks against the existing `clever_hofstadter` container
- one quick verification replay per workload (`2-2-1`, `1` run)
- one full-size calibration replay where needed (`2-2-1`, `1` run)
- one full experiment replay (`2-14-2`, `3` runs)
- manifest, HTML dashboard, JSON summaries, and markdown summaries

## Code And Runbook Changes

- `python/kernmlops/replay/replay_trace.py` now writes runtime-only split stats into the result parquet for every run.
- `temp_data_analysis/replay_runtime_experiment_dashboard.py` renders the generic runtime-only HTML dashboard with std bars, split-success labels, config metadata, and footer metadata.
- `replay.md` now documents the runtime-only replay columns, the runtime-only dashboard flow, and the host-side `tmux` workflow.
- The overnight run is driven by `python/kernmlops/replay/run_overnight_runtime_matrix.py`.
## Preflight

- container-side start time: `2026-04-09T12:10:45Z`
- redis ping: `PONG`
- `VAPTR` command present: `True`
- free space on repo volume at start: `22.22 GiB`
- all overnight replays are runtime-only and do not pass `--collector-config`

## control Verification

- slug: `control_read100_2142_2min3_verification`
- workload: `100% read`
- record count: `1024`
- operation count: `4096`
- result parquet shape: `(5, 991)`
- runtime range: `26.436s` to `30.758s`
- capture log: `/KernMLOps/data/control_read100_2142_2min3_verification_capture.log`
- runtime log: `/KernMLOps/data/control_read100_2142_2min3_verification_runtime.log`
- dashboard: `/KernMLOps/temp_data_analysis/control_read100_2142_2min3_verification_dashboard.html`
- runtime-only verification passed with no dTLB columns.

