# 80% read / 20% delete · exp2

Source parquet: `data/exp2_read80_delete20_delEqHalfRecord_2122_2min3_results_16rows.parquet`

## Important Takeaways

- `base_pages` averaged `117.267s` and `no_break` averaged `113.106s`.
- `base_pages` was `+3.68%` versus `no_break`.
- Hot split success rate: `41.67%`.
- Random split success rate: `50.00%`.
- Observed max split attempts was `2` with configured limit `2`.
- Timed runs ranged from `104.847s` to `127.844s`.

## Slowest Successful Split Rows

- `hot#8 (3/3)` = `user5267642368719093717` with `182` accesses, runtime delta `+10.25%`, split success `3/3`.
- `hot#10 (3/3)` = `user8125243236669674938` with `145` accesses, runtime delta `+4.65%`, split success `3/3`.
- `hot#1 (3/3)` = `user2351990715801970311` with `1335` accesses, runtime delta `+3.75%`, split success `3/3`.
- `hot#12 (3/3)` = `user405020020949719660` with `109` accesses, runtime delta `+3.40%`, split success `3/3`.
- `hot#6 (3/3)` = `user5938475048244035132` with `269` accesses, runtime delta `+3.36%`, split success `3/3`.

## Zero-Success Rows

- `hot#2 (0/3)` = `user8621025099142059187` had `0/3` successful splits, `3` failed split events, and observed max attempts `2`.
- `hot#3 (0/3)` = `user2390393595058248558` had `0/3` successful splits, `3` failed split events, and observed max attempts `2`.
- `hot#4 (0/3)` = `user6636553130839969302` had `0/3` successful splits, `3` failed split events, and observed max attempts `2`.
- `hot#5 (0/3)` = `user317048932254693363` had `0/3` successful splits, `3` failed split events, and observed max attempts `2`.
- `hot#7 (0/3)` = `user4323410974463024952` had `0/3` successful splits, `3` failed split events, and observed max attempts `2`.
- `hot#9 (0/3)` = `user2144571683223468943` had `0/3` successful splits, `3` failed split events, and observed max attempts `2`.
- `hot#11 (0/3)` = `user8357566562958791958` had `0/3` successful splits, `3` failed split events, and observed max attempts `2`.
- `rand#2 (0/3)` = `user3540491433731367504` had `0/3` successful splits, `3` failed split events, and observed max attempts `2`.

## Manifest Notes

- Runtime-only exp2 replay. No dTLB or other hardware counters were collected.
- Row layout: 2 base + 12 hottest + 2 random = 16 rows.
- Runtime target: 120s (acceptance band 105s-135s).
- Delete count is targeted to roughly match half of the starting record count.
- Calibration attempt 1: record_count=8000, operation_count=20000, base_pages=69.489s, accepted=false
- Calibration attempt 2: record_count=13815, operation_count=34538, base_pages=115.910s, accepted=true
