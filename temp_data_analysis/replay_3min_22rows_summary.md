# Replay 3-Minute 22-Row TLB Summary

Source parquet: `data/replay_3min_dtlb_results_22rows.parquet`

## Important Takeaways

- `base_pages` averaged `227.303s` and `no_break` averaged `229.669s` in the dTLB-instrumented run.
- `base_pages` was `-1.03%` versus `no_break` in the dTLB-instrumented run.
- The 17 hot split-only rows averaged `-2.28%` versus `no_break`.
- The 3 random split-only rows averaged `-3.04%` versus `no_break`.
- `base_pages` dTLB loads were `+14.88%` versus `no_break` in the same run.
- `base_pages` dTLB misses were `+6.66%` versus `no_break` in the same run.
- Timed runs in this parquet ranged from `203.390s` to `247.218s`.

## Slowest Runtime Rows

- `hot#16` = `user645607607548788932` with `1118` accesses, runtime delta `+2.19%`, dTLB miss delta `-10.25%`.
- `hot#17` = `user2441528601425904855` with `1050` accesses, runtime delta `+0.25%`, dTLB miss delta `-11.68%`.
- `hot#10` = `user1115706900580575198` with `1910` accesses, runtime delta `+0.17%`, dTLB miss delta `-9.78%`.
- `hot#7` = `user975274386571467611` with `2756` accesses, runtime delta `-0.75%`, dTLB miss delta `-8.01%`.
- `hot#8` = `user6643137509613500210` with `2348` accesses, runtime delta `-1.25%`, dTLB miss delta `-8.03%`.

## Highest dTLB-Miss Rows

- `hot#1` = `user65420917376365171` with `17199` accesses, dTLB miss delta `+3.01%`, runtime delta `-2.09%`.
- `rand#1` = `user6989102676742543724` with `67` accesses, dTLB miss delta `-1.66%`, runtime delta `-2.95%`.
- `hot#3` = `user6515083660125774322` with `7210` accesses, dTLB miss delta `-1.92%`, runtime delta `-4.76%`.
- `hot#6` = `user7258708961263658095` with `3215` accesses, dTLB miss delta `-2.98%`, runtime delta `-5.87%`.
- `hot#2` = `user4921677201590050203` with `8637` accesses, dTLB miss delta `-4.45%`, runtime delta `-2.69%`.
