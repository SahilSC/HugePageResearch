# 80% read / 20% delete · exp2 verification

Source parquet: `data/exp2_read80_delete20_delEqHalfRecord_2122_2min3_verification_results_5rows.parquet`

## Important Takeaways

- `base_pages` averaged `13.372s` and `no_break` averaged `13.704s`.
- `base_pages` was `-2.43%` versus `no_break`.
- Hot split success rate: `100.00%`.
- Random split success rate: `100.00%`.
- Observed max split attempts was `1` with configured limit `2`.
- Timed runs ranged from `12.985s` to `13.988s`.

## Slowest Successful Split Rows

- `hot#2 (1/1)` = `user1353058054183258464` with `79` accesses, runtime delta `+2.08%`, split success `1/1`.
- `rand#1 (1/1)` = `user6762367138279133398` with `3` accesses, runtime delta `-3.45%`, split success `1/1`.
- `hot#1 (1/1)` = `user7938808774216806086` with `164` accesses, runtime delta `-5.25%`, split success `1/1`.

## Zero-Success Rows

- Every split-only row had at least one successful split.

## Manifest Notes

- Quick runtime-only verification for this workload.
- No dTLB or other hardware counters are collected.
