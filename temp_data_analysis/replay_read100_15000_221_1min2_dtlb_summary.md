# 100% Read · 15000 Items · 2-2-1 · 1min-2 · dTLB

Source parquet: `data/replay_read100_15000_221_1min2_dtlb_results_5rows.parquet`

## Important Takeaways

- `base_pages` runtime was `62.626s` and `no_break` runtime was `63.880s`.
- `base_pages` runtime delta vs `no_break` was `-1.96%`.
- `base_pages` dTLB misses were `12544064` on average and `+10.42%` versus `no_break`.
- The `2` hot split-only rows averaged `-4.36%` dTLB misses versus `no_break`.
- The `1` random split-only row averaged `+0.76%` dTLB misses versus `no_break`.

