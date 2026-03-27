# Task A: `messy-dir` Last Commit Review

## Commit under review

- Branch: `messy-dir`
- Commit: `bb88c9e43933006a62c340815bffdc107bebc819`
- Subject: `Implmenting redis object VA lookup + access bit mapping for a va->pa mapping`

## What the commit does

This commit adds a new userspace page-access path and wires it into the existing `vaptr` collector hook.

Main changes:

- `python/kernmlops/data_collection/page_access.py`
  Adds `PageAccessTracker`, which takes `(pid, virtual_address)`, reads `/proc/<pid>/pagemap`, normalizes THP tails to the compound-head PFN via `/proc/kpageflags`, then samples `/sys/kernel/mm/page_idle/bitmap`.
- `python/kernmlops/data_collection/bpf_instrumentation/vaptr_hook.py`
  Extends the `vaptr` collector so that every `VAPTR` sample also records:
  - `mapped_pfn`
  - `tracking_pfn`
  - `physical_page_addr`
  - `tracking_physical_page_addr`
  - `page_idle`
  - `access_bit`
  - `access_bit_valid`
- `python/kernmlops/kernmlops_benchmark/redis.py`
  Adds deterministic YCSB key-name generation so the hook can ask Redis for a fixed initial cohort of keys.
- `redis-module/vaptr.c`
  Changes the Redis module so `VAPTR FIELD field0 <key>` can resolve a direct pointer to the hash field value for hashtable-encoded hashes.
- `config/redis_vaptr_access_bit_e2e.yaml`
  Adds the end-to-end Redis config for this path.

## Did it try to achieve “idle bit page tracking given a PID and VA of another process”?

Yes.

Evidence in code:

- [`page_access.py`](/users/SahilSC/HugePageResearch/python/kernmlops/data_collection/page_access.py#L95) exposes `sample(pid, virtual_address)` and [`sample_many()`](/users/SahilSC/HugePageResearch/python/kernmlops/data_collection/page_access.py#L98).
- [`_read_mapped_pfn()`](/users/SahilSC/HugePageResearch/python/kernmlops/data_collection/page_access.py#L176) resolves PFNs from `/proc/<pid>/pagemap`.
- [`_read_page_idle()`](/users/SahilSC/HugePageResearch/python/kernmlops/data_collection/page_access.py#L236) and [`_mark_idle_pfns()`](/users/SahilSC/HugePageResearch/python/kernmlops/data_collection/page_access.py#L245) operate on `/sys/kernel/mm/page_idle/bitmap`.

That part is generic for any target PID and VA. In this commit it is then used specifically for Redis:

- [`vaptr_hook.py`](/users/SahilSC/HugePageResearch/python/kernmlops/data_collection/bpf_instrumentation/vaptr_hook.py#L115) discovers the Redis PID with `redis-cli INFO server`.
- [`vaptr_hook.py`](/users/SahilSC/HugePageResearch/python/kernmlops/data_collection/bpf_instrumentation/vaptr_hook.py#L159) turns the returned Redis VAs into `PageAccessRequest`s for that PID.

So the intent matches your description.

## Exact `vaptr` collection timing

At a high level:

1. The collector polling thread calls every hook’s `poll()` once per `collector_config.generic.poll_rate`.
2. For `vaptr`, [`poll_instrumentation()`](/users/SahilSC/HugePageResearch/python/kernmlops/cli/collect.py#L31) calls [`VAPtrHook.poll()`](/users/SahilSC/HugePageResearch/python/kernmlops/data_collection/bpf_instrumentation/vaptr_hook.py#L194).
3. `VAPtrHook.poll()` first runs `redis-cli --raw VAPTR FIELD ...` to get key/value virtual addresses.
4. It timestamps the sample with `CLOCK_BOOTTIME`.
5. It page-aligns those VAs and calls `PageAccessTracker.sample_many()`.
6. `sample_many()`:
   - resolves PFNs from pagemap
   - reads the current idle bit for already-armed PFNs
   - then re-arms those PFNs as idle for the next interval
7. The resulting rows are buffered and later written to `vaptr.end.parquet`.

Important detail:

- The access bit is interval-based. The first time a page is seen, [`access_bit_valid`](/users/SahilSC/HugePageResearch/python/kernmlops/data_collection/page_access.py#L137) is false because that first sample only arms the page for the next interval.

## What I verified

### End-to-end run

I followed the Docker path and ran:

```bash
python python/kernmlops collect -v \
  -c config/redis_vaptr_access_bit_e2e.yaml \
  -p vaptr-access-bit-docker \
  --benchmark redis
```

Result:

- Run dir: `data/curated/redis/vaptr-access-bit-docker-20260326T082856357702`
- `testing/verify_vaptr_access_bit.py` passed
- `vaptr.end.parquet` had:
  - `4910` total rows
  - `1232` rows with `access_bit_valid=true`
  - `1232` rows with `access_bit=true`
- Redis PID from `process_trace.end.parquet`: `46808`

### Within-run address stability

For the sampled keys, addresses are stable once they become available:

- Each key had exactly one non-`n/a` virtual address within the run.
- The apparent `2` unique addresses per key came from `n/a` rows before the key/value was resolvable and the later real address.

### Cross-run address stability

I ran three fresh Redis loads with the same workload and `kernel.randomize_va_space=0`.

Result:

- None of the 10 sampled keys kept the same address across all three runs.
- Every key had `2` or `3` distinct addresses across the three fresh Redis process starts.

Conclusion:

- Key names are deterministic.
- Addresses are not deterministic across fresh Redis runs.

## Is it buggy?

My conclusion is: **yes, currently it is not trustworthy as a workload-access signal**.

### Bug / risk 1: `VAPTR` runs before idle-bit sampling

In [`vaptr_hook.py`](/users/SahilSC/HugePageResearch/python/kernmlops/data_collection/bpf_instrumentation/vaptr_hook.py#L200), `VAPTR` is executed before [`_sample_page_access()`](/users/SahilSC/HugePageResearch/python/kernmlops/data_collection/bpf_instrumentation/vaptr_hook.py#L262) reads the previous interval’s idle state.

That means the “previous interval” already includes the current poll’s own `VAPTR` lookup work.

Why that matters:

- [`redis-module/vaptr.c`](/users/SahilSC/HugePageResearch/redis-module/vaptr.c#L46) traverses Redis internals with `dictFind`, `dictGetVal`, and `sdslen`.
- That lookup can touch the same object/page you are trying to classify as accessed vs idle.

So even if the workload did nothing, the act of asking Redis for the pointer can bias the page toward looking accessed.

### Bug / risk 2: on this host, the idle bit does not remain set for a known-idle frozen page

I tested the kernel interface directly on a separate stopped process page:

- created a child process with one anonymous page
- resolved its PFN once
- sent `SIGSTOP` so it could not touch the page
- marked the PFN idle repeatedly
- reread `/sys/kernel/mm/page_idle/bitmap`

Observed result:

- the bit was cleared every time
- the same happened through `PageAccessTracker.sample()`

So on this machine, even a deliberately frozen page looks “accessed” immediately. That means the current `access_bit=true` rows are not strong evidence of real workload accesses here.

### Bug / risk 3: the current data is nearly non-discriminative

From the successful Redis run:

- all `1232` valid rows had `access_bit=true`
- the cumulative positive counts per key only differed by `1`

That is not what you want from page-rank tracking.

## Verdict

The commit **does implement the intended PID+VA -> PFN -> idle-bit pipeline** and it **does integrate it into the `vaptr` collector**.

But for the question “does it correctly measure whether the workload accessed that page during the interval?”, my answer is:

- **not reliably on this host**
- **and likely not cleanly even in principle**, because the current ordering lets `VAPTR` contaminate the measurement window

Practical summary:

- Good: VA lookup, PFN resolution, THP head normalization, collector integration, parquet output.
- Not yet trustworthy: interval access attribution.
