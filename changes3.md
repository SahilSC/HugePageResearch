# Replay 32-Row One-Minute-Cap Experiment

## Summary

- Start from the existing Docker container `clever_hofstadter`.
- Keep this experiment runtime-only. Do not use `--collect-dtlb`.
- The main `32`-row replay must run in detached host `tmux` session `replay_cap1m_32`.
- The `base_pages` baseline must use replay's THP-disabled all-zero-row behavior.
- Every timed row-run must stay below `60` seconds.

## Progress

- 2026-04-08T01:34:14Z: Confirmed `clever_hofstadter` is still running and Redis inside the container responds with `PONG`.
- 2026-04-08T01:34:14Z: Confirmed the worktree is dirty before this run, so this task will only add new experiment artifacts and `changes3.md`.
- 2026-04-08T01:37:15Z: Captured a fresh replay workload with `OPERATION_COUNT=118784`.
- 2026-04-08T01:37:15Z: Preserved the fresh inputs as `data/redis_traces/replay_cap1m_32rows_snapshot.rdb` and `data/redis_traces/replay_cap1m_32rows_monitor_run.log`.
- 2026-04-08T01:41:27Z: Generated the `3`-row `hotkey_harm` calibration matrix and ran the mock replay once per row.
- 2026-04-08T01:41:27Z: Calibration runtimes were `45.127732s`, `46.047997s`, and `45.722376s`, so the slowest row stayed below the `60s` cap and no retune was needed.
- 2026-04-08T01:41:44Z: Generated `data/replay_cap1m_breakpoints_32rows.parquet` from the accepted preserved trace with `32` total rows.
- 2026-04-08T01:42:12Z: Started the main runtime-only replay in detached host tmux session `replay_cap1m_32`.
- 2026-04-08T01:42:12Z: Verified the tmux pane is live and already logging `[1/32 run 1/3] Restoring snapshot with Redis THP disabled for the base-pages baseline ...`.
- 2026-04-08T01:42:28Z: Re-checked the tmux pane and confirmed the main run advanced into `[1/32 run 1/3] Timing run trace ...`.
- 2026-04-08T01:46:33Z: The detached tmux run is still healthy. Latest visible progress is `[5/32 run 3/3] Timing run trace ...`.
- 2026-04-08T01:46:33Z: Recent completed timed rows stayed under the cap: `43.494s`, `45.148s`, `46.574s`, `44.605s`, `46.317s`, `44.395s`, and `45.072s`.
- 2026-04-08T05:29:29Z: The main tmux job finished successfully with pane status `0` and wrote `data/replay_cap1m_results_32rows.parquet`.
- 2026-04-08T05:29:29Z: Final result shape is `32` rows by `4099` columns with only `runtime_s_1..3` measurement columns and no `dtlb_*` columns.
- 2026-04-08T05:29:29Z: All timed row-runs stayed under the one-minute cap. Observed runtime range was `40.704629s` to `56.374521s`, with overall mean `44.995201s`.
- 2026-04-08T05:38:48Z: Added `temp_data_analysis/replay_cap1m_32rows_analysis.py` to generate a full 32-row dashboard and graph set from `data/replay_cap1m_results_32rows.parquet`.
- 2026-04-08T05:38:48Z: Generated `temp_data_analysis/replay_cap1m_32rows_dashboard.html`, `replay_cap1m_32rows_runtime_impact.png`, `replay_cap1m_32rows_metrics.json`, `replay_cap1m_32rows_rows.json`, and `replay_cap1m_32rows_summary.md`.
- 2026-04-08T05:38:48Z: The generated dashboard summary shows `base_pages` at `45.950s`, `no_break` at `45.184s`, and `10/30` split-only rows slower than `no_break`.
