# Task C: Runtime Validation, Address Stability, and Graphs

## Environment and run

I used the Docker-based flow and ran:

```bash
python python/kernmlops collect -v \
  -c config/redis_vaptr_access_bit_e2e.yaml \
  -p vaptr-access-bit-docker \
  --benchmark redis
```

Key outputs:

- Raw run:
  `data/curated/redis/vaptr-access-bit-docker-20260326T082856357702`
- Cleaned run:
  `data/curated/redis/cleanedvaptr-access-bit-docker-20260326T082856357702`

Files produced:

- `vaptr.end.parquet`
- `process_trace.end.parquet`
- `system_info.end.parquet`

## Verification results

`testing/verify_vaptr_access_bit.py` passed against the raw run.

Observed values:

- `vaptr_rows=4910`
- `vaptr_valid_rows=1232`
- `vaptr_accessed_rows=1232`
- `redis_tgids=[46808]`

Interpretation:

- the parquet schema and plumbing are working
- but every valid access-bit sample was positive

## Sampling cadence

From the collected `vaptr.end.parquet`:

- valid sampled timestamps: `125`
- mean interval: `120.992 ms`
- median interval: `120.784 ms`

This is consistent with the configured `poll_rate: 0.1`, plus user-space overhead from:

- `redis-cli VAPTR`
- PID discovery / cached PID reuse
- pagemap lookups
- kpageflags reads
- idle-bitmap reads and writes

## Address stability

### Within a run

Once a sampled key became available, its resolved address was stable within that run:

- each key had exactly one non-`n/a` virtual address in the successful collection
- the extra apparent uniqueness came from early `n/a` rows before the value pointer was resolvable

### Across fresh Redis runs

I repeated fresh Redis load + `VAPTR FIELD field0 ...` three times with:

- the same workload
- the same 10 deterministic sampled keys
- `kernel.randomize_va_space=0`

Result:

- none of the 10 keys kept the same address across all 3 runs
- every key had 2 or 3 distinct addresses across those 3 runs

Example:

- `user6284781860667377211`
  - run 0: `0x7fffefc80009`
  - run 1: `0x7ffff7995005`
  - run 2: `0x7ffff427b009`

So:

- key selection is deterministic
- object addresses are **not** deterministic across fresh Redis runs

## Direct idle-bit sanity check

I also tested the kernel interface directly on a separate stopped process page.

Procedure:

1. create a child process with one anonymous page
2. resolve its PFN once
3. send `SIGSTOP`
4. mark the PFN idle
5. reread the idle bitmap

Observed result:

- the idle bit was cleared every time

This happened:

- through the repo’s `PageAccessTracker`
- and through direct raw reads/writes of `/sys/kernel/mm/page_idle/bitmap`

That means the host’s current idle-bit behavior is not giving me a clean “known-idle page stayed idle” control case.

## Graphs produced

I added:

- `python/kernmlops/analysis/vaptr_access_over_time.py`

and generated:

- `figures/vaptr_access_over_time.png`

What the figure shows:

- top panel: cumulative `access_bit=true` counts over time for the 10 sampled keys, ranked by final count
- bottom panel: per-sample access-bit heatmap by final rank

Observed summary from the plotted run:

- final cumulative counts were `123` or `124` for every sampled key
- the lines are almost identical
- the heatmap is effectively always “accessed” once valid

So the current signal is not giving a useful page-rank separation for this run.

## Practical conclusion

For your current data:

- you **do** have time-series data for sampled Redis object pages
- you **do not** have a trustworthy whole-process page-rank-over-time dataset yet
- and the current `access_bit` signal is too uniformly positive to be a good ranking signal on this host

For whole-process page-rank tracking over time, you would need a design that combines:

- `main` branch breadth (many PFNs for a PID)
- with time-series output, not only final aggregates

For sampled-key time series, `messy-dir` already stores timestamps, but the measured access signal needs to be made trustworthy first.
