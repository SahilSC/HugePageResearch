# Replay Trace Fresh-Machine Workflow And Multi-Combo Fix

## Summary

This task did two things:

- documented one clear container-capture and host-replay workflow for a new
  machine
- fixed the replay restore loop so the full breakpoint matrix can run past
  combo 1

The root cause of the multi-combo failure was process reaping. After the first
restore, replay starts Redis itself with `subprocess.Popen(...)`. On the next
restore, `SHUTDOWN NOSAVE` stops Redis, but the exited child can remain
unreaped briefly. The old code polled `os.kill(pid, 0)`, which still succeeds
for a zombie, so replay timed out even though Redis had already exited.

## What Changed

- `python/kernmlops/data_collection/replay_trace.py`
  - added `_redis_process_exited(...)` and `_wait_for_redis_exit(...)`
  - `restore_snapshot(...)` now waits for either:
    - a reaped child Redis process, or
    - a non-child Redis process to disappear from the process table
  - tightened the guided startup error so it points users at the matching host
    Redis `7.4.2` binary instead of suggesting any `redis-server` on `PATH`
- `testing/test_replay_trace.py`
  - added regression coverage for:
    - replay-started child Redis being reaped with `waitpid(..., WNOHANG)`
    - fallback process lookup for the initial host-started Redis
- `SETUP.md`
  - kept the existing top TODO comments
  - added a dedicated `Redis Trace Capture And Replay` checklist
  - documented the validated split:
    - capture in the Docker container
    - replay on the host with `/tmp/redis-7.4.2/src/redis-server`
- `README.md`
  - now points Redis replay users at `SETUP.md`
- `tests/syscall_verification/README.md`
  - now uses the matching host Redis binary guidance

## Validated Workflow

Used the existing Docker container `admiring_lamport` and reran the real flow:

1. Container capture
   - regenerated:
     - `data/redis_traces/snapshot.rdb`
     - `data/redis_traces/monitor_run.log`
   - observed monitor log size:
     - `5799` lines
2. Host Redis replay startup
   - used:
     - `/tmp/redis-7.4.2/src/redis-server`
     - `/users/SahilSC/HugePageResearch/redis-module/vaptr.so`
   - confirmed:
     - `PONG`
     - `MODULE LIST` included `vaptr`
3. Baseline replay
   - completed successfully
   - result parquet:
     - `/tmp/baseline-replay-results.parquet`
   - observed runtime:
     - `runtime_s_1 = 1.964231`
4. Breakpoint generation
   - result parquet:
     - `/tmp/real-breakpoints.parquet`
   - observed summary:
     - `Unique split targets: 2260`
     - `Total accesses: 4096`
     - `Wrote 10 breakpoint combinations`
5. Full breakpoint replay
   - result parquet:
     - `/tmp/real-replay-results.parquet`
   - observed result shape:
     - `10` rows x `2261` columns
   - observed runtime range:
     - min `2.183248`
     - max `4.023739`
     - mean `3.446534`

## Why This Works

The restore loop now handles both Redis lifetimes that matter in this repo:

- the original host-started Redis, which is not a child of replay
- later replay-started Redis processes, which are children and must be reaped

That keeps the restart model simple while removing the false timeout. The live
validation proved the fix in the important place: combo 1 finished, combo 2
started cleanly, and the full 10-combo replay completed.
