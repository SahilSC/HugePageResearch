# VAPTR Root-Cause Fix

Date: 2026-03-26

## Problem

The original `vaptr` access-bit collector was producing a saturated signal: once a sampled Redis key became available, nearly every later sample reported `access_bit=true`.

The main root cause was sampling order inside `VAPtrHook.poll()`:

1. Run Redis `VAPTR` to resolve the current key address.
2. Translate that address to PFN and read the page-idle bitmap.

That ordering lets the `VAPTR` lookup itself touch Redis object memory before the idle-bit read. Since the collector was reading the idle state after walking Redis internals, the measurement window was contaminated by the measurement itself.

## Changes

### 1. Split page-access tracking into explicit phases

File: `python/kernmlops/data_collection/page_access.py`

Added:

- `resolve_many(...)`
- `read_many(...)`
- `arm_many(...)`

This separates:

- VA -> PFN resolution
- reading the previous idle-bit state
- re-arming those PFNs as idle for the next interval

### 2. Convert `vaptr` sampling to a two-phase interval

File: `python/kernmlops/data_collection/bpf_instrumentation/vaptr_hook.py`

Added a pending-sample state so each poll now does:

1. Read the idle-bit state for pages armed during the previous poll.
2. Emit rows for that previous interval.
3. Only then run `VAPTR` again to fetch fresh addresses.
4. Resolve and arm those pages for the next interval.

This removes the self-interference bug from the access-bit measurement.

### 3. Add regression coverage

File: `testing/test_vaptr_hook.py`

Added a test that verifies the hook reads the previous interval before refreshing Redis addresses.

### 4. Fix the end-to-end verifier

File: `testing/verify_vaptr_access_bit.py`

The verifier previously read only `process_trace.end.parquet`, which can miss the early `redis-server` exec/start rows because `process_trace` is sharded. It now concatenates all `process_trace.*.parquet` files, matching the cleaner logic.

## Evidence

### Before fix

Run: `data/curated/redis/vaptr-access-bit-docker-20260326T082856357702`

- `vaptr` rows: 4910
- valid rows: 1232
- `access_bit=true`: 1232
- Every valid row was positive.
- The generated heatmap was effectively a solid block.

### After fix

Run: `data/curated/redis/vaptr-access-bit-fixed-20260326T154401960207`

- `vaptr` rows: 680
- valid rows: 680
- `access_bit=true`: 143
- Per-key positives now vary from `8` to `24` across `68` samples.

Per-key final counts:

- rank 1: `user5465015992139406178` -> `24`
- rank 2: `user6873002678636213555` -> `21`
- rank 3: `user9105318085603802964` -> `19`
- rank 4: `user1820151046732198393` -> `15`
- rank 5: `user1000385178204227360` -> `13`
- rank 6: `user7697331399106995587` -> `13`
- rank 7: `user8517097267634966620` -> `12`
- rank 8: `user3232700585171816769` -> `9`
- rank 9: `user6284781860667377211` -> `9`
- rank 10: `user4052466453699787802` -> `8`

That is the expected directional change: the signal is no longer degenerate and now differentiates among pages.

## Side Effects

1. Valid rows now describe the previous sampling interval.
   - A valid row at poll `N` corresponds to the address/PFN snapshot armed at poll `N-1`.

2. The first poll is effectively an arming poll.
   - There is no prior interval to report yet.

3. Row counts changed.
   - The old implementation mixed “current lookup” and “previous interval” semantics.
   - The new implementation emits one real access-bit observation per completed interval.

4. `testing/verify_vaptr_access_bit.py` is now robust to sharded `process_trace` output.
   - This was a verifier bug, not a `vaptr` collector bug.

## Verification Performed

- Local `py_compile` on the modified Python files
- Docker unit tests:
  - `python -m unittest testing.test_page_access testing.test_vaptr_hook`
- Docker end-to-end verifier:
  - `python testing/verify_vaptr_access_bit.py --run-root data/curated/redis --prefix vaptr-access-bit-fixed`
- Re-generated the fixed figure:
  - `figures/vaptr_access_over_time_fixed.png`

## Remaining Unrelated Issue

`make lint` still fails because the repo has many pre-existing import-order violations unrelated to the `vaptr` work. I cleaned one broken file in `data_schema/perf/__init__.py`, but did not mass-reformat the rest of the repository as part of this fix.
