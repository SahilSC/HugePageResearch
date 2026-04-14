# 100% Read · 15000 Items · 2-5-2 · 1min-2

Source parquet: `data/replay_read100_15000_252_1min2_results_9rows.parquet`

## Important Takeaways

- `base_pages` averaged `62.607s` and `no_break` averaged `62.502s`.
- `base_pages` was `+0.17%` versus `no_break`.
- The `5` hot split-only rows averaged `+1.21%` versus `no_break`.
- The `2` random split-only rows averaged `-1.51%` versus `no_break`.
- Timed runs ranged from `58.889s` to `65.441s`.

## Slowest Split Rows

- `hot#1` = `user8709897793643524497` with `385` accesses, runtime delta `+3.84%`.
- `hot#4` = `user8493199810341118767` with `100` accesses, runtime delta `+1.39%`.
- `hot#5` = `user5822375050137704963` with `81` accesses, runtime delta `+1.35%`.
- `hot#3` = `user4640345626374325581` with `177` accesses, runtime delta `-0.11%`.
- `rand#2` = `user9094484155246688141` with `1` accesses, runtime delta `-0.24%`.
