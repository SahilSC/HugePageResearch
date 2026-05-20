# Replay Zero-Poll dTLB Refactor

## Summary

Refactored replay into `python/kernmlops/replay/` and replaced
the old replay-time dTLB path with a replay-local direct perf collector.

From a user point of view, replay still writes the same runtime columns and the
same `dtlb_loads_*` / `dtlb_misses_*` columns. The main change is internal:
`--collect-dtlb` now opens direct begin/end `perf_event_open` counters for the
Redis thread set instead of relying on the repo's buffered BPF perf hook.

## What Was Done

1. Moved the replay implementation into:
   - `python/kernmlops/replay/replay_trace.py`
   - `python/kernmlops/replay/hardware_collectors.py`
   - `python/kernmlops/replay/__init__.py`
2. Removed the older replay entrypoints so the package path is the only
   supported way to run replay.
3. Added `hardware_collectors.py` with:
   - `DTLBCounterMetrics`
   - `start_dtlb_loads()` / `stop_dtlb_loads()`
   - `start_dtlb_misses()` / `stop_dtlb_misses()`
   - `start_counters()` / `stop_counters()`
4. Changed replay to use `start_counters`/`stop_counters` around the timed replay window.
5. Removed the replay dependency on `PerfBPFHook`, perf-buffer polling, and
   the table summarization path.
6. Added focused unit coverage in:
   - `testing/test_replay_hardware_collectors.py`
   - `testing/test_replay_trace.py`
7. Updated `replay.md` so the runbook matches the new package path and host
   dTLB collection behavior.

## Why It Works

Replay only needs one answer per run: the final total dTLB loads and misses for
the Redis process while the replay commands were executing.

The new collector now does exactly that:

- discover the Redis thread IDs from `/proc/<tgid>/task/*`
- open a direct perf counter for each thread
- enable the counters immediately before replay starts
- disable and read them immediately after replay ends
- sum the per-thread totals into one replay result

That makes the replay dTLB path much simpler than the old sampling design. We
no longer depend on:

- BPF perf handlers
- perf-buffer polling
- draining buffered events after the run
- per-CPU table summarization to approximate a final total

## Approaches Considered

### Keep the old BPF hook and just lower its sampling overhead

Pros:

- minimal code movement
- no new low-level perf code

Cons:

- replay would still depend on perf-buffer polling
- replay would still summarize buffered samples after the fact
- it would not match the intended "begin/end only" collector design

### Direct begin/end perf counters

Pros:

- much simpler replay-specific collector
- no BPF dependency for replay dTLB collection
- deterministic unit tests with mocked open/read/close behavior

Cons:

- replay now owns a small amount of low-level `perf_event_open` logic
- the host still needs perf permissions, so `sudo` / `perf_event_paranoid`
  remain part of the operator story

This second approach was chosen because it matches the actual replay need much
more closely.

## Validation

The replay-focused unit tests passed with:

```bash
cd /users/SahilSC/HugePageResearch
.venv/bin/python -m unittest \
  testing.test_replay_hardware_collectors \
  testing.test_replay_trace
```

Observed result:

- `Ran 24 tests in 0.054s`
- `OK (skipped=1)`

The single skipped test is the optional root smoke test that only runs when
`RUN_REPLAY_DTLB_SMOKE=1` is set.

## Pros And Cons

### Pros

- replay dTLB collection is now local to replay instead of piggybacking on the
  broader collector stack
- the new collector follows the actual experiment shape more directly
- one package path now covers the replay entrypoints cleanly

### Cons

- direct perf counters are Linux-specific and tuned to this repo's host setup
- replay still expects host-side permissions for `--collect-dtlb`
- the collector opens one file descriptor per counter per Redis thread, which
  is fine for Redis here but is still more state than a single aggregate read

## Result

The refactor keeps the replay CLI and parquet output shape stable while making
the dTLB collection path simpler, more local, and easier to reason about.
