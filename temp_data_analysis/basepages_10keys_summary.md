# Base-Pages 10-Key Rerun Summary

Source parquet: `data/test3_basepages_results_10keys_12rows.parquet`

## Important Takeaways

- `base_pages` averaged `18.273s` and `no_break` averaged `17.796s`.
- `base_pages` was `+2.7%` versus `no_break`.
- `5/10` split-only rows were slower than `no_break`.
- Mean split-only delta versus `no_break`: `+0.176s` (`+0.99%`).
- Correlation between access count and runtime delta percent: `+0.867`.

## Interpretation

- This rerun isolates the cost of running without THP from the old split-thp syscall storm.
- `base_pages` should now be read as a no-THP Redis startup baseline, not as "split every page before access 0."
- The split-only rows still test whether breaking one hot key hurts relative to the intact THP baseline.

## Hottest Tested Keys

- `hot#1` = `user3474737920793767716` with `1772` accesses, runtime delta `+1.160s` (`+6.52%`).
- `hot#2` = `user764837236214422120` with `926` accesses, runtime delta `+0.266s` (`+1.49%`).
- `hot#3` = `user8509611283054996699` with `677` accesses, runtime delta `+0.232s` (`+1.30%`).

## Slowest Rows In This Rerun

- `hot#1` = `user3474737920793767716` with `1772` accesses, runtime delta `+1.160s` (`+6.52%`).
- `hot#6` = `user2191786966006142104` with `356` accesses, runtime delta `+0.330s` (`+1.85%`).
- `hot#2` = `user764837236214422120` with `926` accesses, runtime delta `+0.266s` (`+1.49%`).
