# Replay Cap-1m 32-Row Summary

Source parquet: `data/replay_cap1m_results_32rows.parquet`

## Important Takeaways

- `base_pages` averaged `45.950s` and `no_break` averaged `45.184s`.
- `base_pages` was `+1.70%` versus `no_break`.
- `10/30` split-only rows were slower than `no_break`.
- Mean split-only delta versus `no_break`: `-0.227s` (`-0.50%`).
- Correlation between access count and runtime delta percent: `+0.116`.
- Overall timed-run range was `40.705s` to `56.375s`.

## Hottest Tested Keys

- `hot#1` = `user3474737920793767716` with `4704` accesses, runtime delta `-0.311s` (`-0.69%`).
- `hot#2` = `user764837236214422120` with `2319` accesses, runtime delta `+0.648s` (`+1.43%`).
- `hot#3` = `user8509611283054996699` with `1823` accesses, runtime delta `-0.346s` (`-0.77%`).
- `hot#4` = `user9106096361333785295` with `1368` accesses, runtime delta `-0.135s` (`-0.30%`).
- `hot#5` = `user4782239706702012629` with `1103` accesses, runtime delta `+0.663s` (`+1.47%`).

## Slowest Split-Only Rows

- `hot#14` = `user1582021617399532796` with `497` accesses, runtime delta `+3.981s` (`+8.81%`).
- `hot#27` = `user6163094074735340348` with `192` accesses, runtime delta `+1.985s` (`+4.39%`).
- `hot#7` = `user2770635419936443086` with `753` accesses, runtime delta `+1.688s` (`+3.74%`).
- `hot#9` = `user4305774350114537338` with `652` accesses, runtime delta `+1.346s` (`+2.98%`).
- `hot#5` = `user4782239706702012629` with `1103` accesses, runtime delta `+0.663s` (`+1.47%`).

## Fastest Split-Only Rows

- `hot#10` = `user1715321609418666813` with `639` accesses, runtime delta `-2.475s` (`-5.48%`).
- `hot#30` = `user5355842018100905482` with `175` accesses, runtime delta `-2.018s` (`-4.47%`).
- `hot#12` = `user4031140238279856721` with `514` accesses, runtime delta `-1.804s` (`-3.99%`).
- `hot#26` = `user974154465547186715` with `195` accesses, runtime delta `-1.771s` (`-3.92%`).
- `hot#25` = `user281664170776959239` with `219` accesses, runtime delta `-1.680s` (`-3.72%`).
