# Replay 3-Minute 22-Row Runtime-Only Summary

Source parquet: `data/replay_3min_results_22rows.parquet`

## Important Takeaways

- `base_pages` averaged `173.290s` and `no_break` averaged `171.918s`.
- `base_pages` was `+0.80%` versus `no_break`.
- The `17` hot split-only rows averaged `-0.21%` versus `no_break`.
- The `3` random split-only rows averaged `+0.04%` versus `no_break`.
- Runtime-only timed runs ranged from `162.371s` to `181.247s`.

## Slowest Runtime Rows

- `hot#8` = `user6643137509613500210` with `2348` accesses, runtime delta `+3.42%`.
- `hot#14` = `user7435771059677991585` with `1307` accesses, runtime delta `+1.55%`.
- `hot#16` = `user645607607548788932` with `1118` accesses, runtime delta `+1.10%`.
- `hot#7` = `user975274386571467611` with `2756` accesses, runtime delta `+1.07%`.
- `rand#2` = `user5213947937773218434` with `26` accesses, runtime delta `+0.62%`.
