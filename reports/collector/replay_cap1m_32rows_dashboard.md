# Replay Cap-1m 32-Row Dashboard

## Summary

Built a new dashboard and analysis pipeline for the finished capped 32-row
runtime-only replay result. The output mirrors the newer base-pages 10-key
dashboard so the full 32-row run can be reviewed with the same JSON, PNG, and
HTML artifact pattern.

The new generated artifacts are:

- `temp_data_analysis/replay_cap1m_32rows_dashboard.html`
- `temp_data_analysis/replay_cap1m_32rows_runtime_impact.png`
- `temp_data_analysis/replay_cap1m_32rows_metrics.json`
- `temp_data_analysis/replay_cap1m_32rows_rows.json`
- `temp_data_analysis/replay_cap1m_32rows_summary.md`

## What Was Done

Added `temp_data_analysis/replay_cap1m_32rows_analysis.py` to read
`data/replay_cap1m_results_32rows.parquet`, reconstruct per-key hotness from
`data/redis_traces/replay_cap1m_32rows_monitor_run.log`, classify the replay
rows as `base_pages`, `no_break`, or `split_only:<key>`, and emit the same kind
of metrics and row-level JSON used by the existing dashboard flow.

The script reuses the existing
`temp_data_analysis/basepages_10keys_dashboard.html` template rather than
forking another large HTML file. It rewrites the visible copy and JSON file
names for the 32-row run, then embeds fresh inline preview data so the HTML can
be opened directly or served through `python -m http.server`.

Added `testing/test_replay_cap1m_dashboard_preview.py` to guard the new
dashboard-template path. The test checks that inline JSON replacement still
works and that the derived dashboard HTML points at the new 32-row JSON files.

## Why It Works

The finished replay parquet already stores the full breakpoint vectors and the
three runtime columns. The only missing context for a dashboard is the per-key
access count, which the script reconstructs from the preserved monitor log with
the existing `generate_breakpoints.py` parser. That keeps the hotness ranking
aligned with the exact trace that produced the `32`-row result.

The reused dashboard template expects the same metric keys and row-record
fields as the 10-key base-pages dashboard. The new script writes a compatible
JSON shape, so the existing charts, story cards, and row-details table can be
reused without changing the front-end JavaScript.

## Output Highlights

- `base_pages` mean runtime: `45.950s`
- `no_break` mean runtime: `45.184s`
- `base_pages` vs `no_break`: `+1.70%`
- split-only rows slower than `no_break`: `10/30`
- mean split-only delta vs `no_break`: `-0.227s` (`-0.50%`)
- overall timed-run range: `40.705s` to `56.375s`

## Approaches Considered

### Copy the 10-key dashboard HTML into a second standalone file

This would have worked, but it would create another large HTML document to keep
in sync with every future chart or styling tweak.

### Reuse the existing 10-key dashboard template

This was chosen. The visual layout and JSON contract already fit the new 32-row
run, so only the copy, artifact names, and inline preview data needed to
change.

## Pros And Cons

Pros:

- Keeps the 32-row output visually consistent with the existing base-pages
  dashboard.
- Reuses the current JSON contract instead of inventing a second front-end
  format.
- Produces both directly openable HTML and standalone JSON/PNG artifacts.

Cons:

- The reused copy still reflects the base-pages-oriented narrative, so it is
  not a completely custom front-end for the 32-row run.
- The scatter plot is denser with `30` split-only rows than it was with `10`,
  so labels are busier than the smaller dashboard.
