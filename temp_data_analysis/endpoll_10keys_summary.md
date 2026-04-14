# End-Only 10-Key Rerun Summary

Source parquet: `data/test2_endpoll_results_10keys_12rows.parquet`

## Important Takeaways

- `all_break` averaged `25.206s` and `no_break` averaged `22.405s`.
- `all_break` was `12.5%` slower than `no_break`.
- `8/10` split-only rows were slower than `no_break`.
- Mean split-only slowdown versus `no_break`: `+0.441s`.
- Correlation between access count and runtime delta: `-0.511`.

## Interpretation

- This rerun still says that splitting everything is bad.
- It does not say that the hottest key is always the worst key.
- In this rerun, the hottest tested key was slightly faster than `no_break`, while several medium-hot keys were the worst rows.

## Hottest Tested Keys

- `hot#1` = `user3474737920793767716` with `1772` accesses, runtime delta `-0.207s`.
- `hot#2` = `user764837236214422120` with `926` accesses, runtime delta `+0.288s`.
- `hot#3` = `user8509611283054996699` with `677` accesses, runtime delta `+0.415s`.

## Slowest Rows In This Rerun

- `hot#4` = `user9106096361333785295` with `527` accesses, runtime delta `+1.263s`.
- `hot#8` = `user5921958344919828414` with `283` accesses, runtime delta `+0.722s`.
- `hot#6` = `user2191786966006142104` with `356` accesses, runtime delta `+0.691s`.
