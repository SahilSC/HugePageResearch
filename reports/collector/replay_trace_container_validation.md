# Replay Trace Container Validation

## Summary

Validated the `replay_trace.py` flow inside the existing Docker container
`quirky_carson` and then simplified the replay restart contract.

The bug was that Redis can report `executable:/KernMLOps/redis-server` in
`INFO server` when it is started from the repo root with `redis-server
./config/redis.conf`. `restore_snapshot()` reused that path directly, so replay
failed before Redis could restart. The final fix does not try to recover from
that case automatically. Instead, replay now fails fast with an explicit
operator message, and the docs now prescribe one canonical host-side startup
command that uses `REDIS_BIN="$(command -v redis-server)"`.

## What Was Done

1. Used the existing container instead of creating a new one.
2. Confirmed the container had:
   - the repo mounted at `/KernMLOps`
   - the patched `6.8.12-splitthp` kernel visible
   - `redis-server`, `redis-cli`, `.venv`, `snapshot.rdb`, `monitor_run.log`,
     and `redis-module/vaptr.so`
3. Started Redis from the repo root without `--loadmodule` to test the exact
   startup path new users are likely to use.
4. Reproduced the failure:
   - Redis reported `executable:/KernMLOps/redis-server`
   - `replay_trace.py` failed in `restore_snapshot()` with
     `FileNotFoundError: [Errno 2] No such file or directory:
     '/KernMLOps/redis-server'`
5. Updated `python/kernmlops/data_collection/replay_trace.py` so replay trusts
   `INFO server["executable"]` directly and fails fast with a guided error if
   that path is missing.
6. Added a focused regression test in `testing/test_replay_trace.py` for the
   guided fail-fast case.
7. Generated a temporary one-key breakpoint parquet from the captured trace so
   the replay smoke would stay fast while still exercising the split path.
8. Ran:

```bash
docker exec quirky_carson bash -lc '
  cd /KernMLOps &&
  REDIS_BIN="$(command -v redis-server)" &&
  "$REDIS_BIN" ./config/redis.conf --daemonize yes
'

docker exec quirky_carson bash -lc '
  cd /KernMLOps &&
  PYTHONPATH=./python/kernmlops .venv/bin/python \
    python/kernmlops/data_collection/replay_trace.py \
    data/redis_traces/snapshot.rdb \
    data/redis_traces/monitor_run.log \
    --breakpoints data/replay_check_oneuser.parquet \
    --output data/replay_check_results.parquet \
    --runs 1 -v
'
```

## Why It Works

The replay restart path now has one clear precondition: the running Redis
instance must already report a real executable path in `INFO server`. The docs
enforce that by telling the operator to start Redis from `~/HugePageResearch`
with:

```bash
REDIS_BIN="$(command -v redis-server)"
"$REDIS_BIN" ./config/redis.conf --loadmodule ./redis-module/vaptr.so
```

That keeps replay simple. If the operator instead starts Redis as bare
`redis-server ...`, replay now stops immediately with a guided error instead of
hiding the problem with extra executable-recovery logic.

The validation run proved the intended behavior:

- replay restored the large snapshot successfully
- replay restarted Redis successfully
- the restarted Redis instance auto-loaded `vaptr.so`
- replay finished all `5774` commands and wrote a results parquet
- when Redis was started as bare `redis-server ./config/redis.conf ...`,
  replay failed immediately with the new guided operator message instead of
  trying to recover silently

After the replay-triggered restart, `redis-cli MODULE LIST` showed:

```text
name
vaptr
ver
1
path
/KernMLOps/redis-module/vaptr.so
```

## Result

The one-key smoke replay completed successfully. The targeted key still hit the
known THP caveat and returned `ENOENT` from `split_thp`, but that happened after
`VAPTR` lookup and syscall invocation were already working. This was a valid
replay-path success, not a module-loading failure.

The temporary validation artifacts were removed after the run so the repo does
not keep extra replay scratch files in `data/` or the root directory.

Observed replay result:

- `5774` commands replayed
- `runtime_s_1 = 2.115122`
- the bad startup path raised the guided
  `Redis INFO server reported an executable path that replay cannot restart`
  error before replay attempted a restart

## Approaches Considered

- Full generated breakpoint matrix:
  too slow and noisy for a first validation pass because the trace contains
  `2292` keys.
- One-key smoke replay:
  chosen because it still exercises snapshot restore, replay, VAPTR lookup, and
  the syscall path while keeping runtime short enough for quick iteration.

## Pros And Cons

Pros:

- Reproduced a real user-facing failure instead of only running mocked tests.
- Validated the documented host-side replay workflow with one canonical Redis
  startup command.
- Kept the runtime check short and repeatable.

Cons:

- This was a smoke validation, not a full breakpoint-matrix experiment.
- `ENOENT` on the chosen key still means the exact value address was not inside
  a split-eligible THP at replay time, which is expected in this research setup.
