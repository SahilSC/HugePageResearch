# Replay 1:1 15360/15360 Runtime-Only Summary

Source parquet: `data/replay_1to1_15360_results_14rows.parquet`

## Important Takeaways

- `base_pages` averaged `7.170s` and `no_break` averaged `7.282s`.
- `base_pages` was `-1.54%` versus `no_break`.
- The `10` hot split-only rows averaged `-0.42%` versus `no_break`.
- The `2` random split-only rows averaged `-5.97%` versus `no_break`.
- Timed runs ranged from `6.276s` to `8.040s`.

## Slowest Runtime Rows

- `hot#4` = `user5504320477202123438` with `158` accesses, runtime delta `+5.26%`.
- `hot#2` = `user6185976841615236091` with `326` accesses, runtime delta `+3.75%`.
- `hot#1` = `user3798598596772897818` with `549` accesses, runtime delta `+2.05%`.
- `hot#3` = `user1583359853641655575` with `241` accesses, runtime delta `+0.03%`.
- `hot#6` = `user3904206309816411729` with `111` accesses, runtime delta `-0.02%`.
