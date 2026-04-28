# Replay `break_page` via VAPTR and `split_thp`

## Summary

This task replaced the replay-time `break_page` stub with a real Redis-to-THP
split path. The new code asks Redis for its pid, resolves a key's value address
through `VAPTR`, and calls the custom `split_thp(pid, vaddr)` syscall directly
from Python.

The live Docker smoke passed, with one important caveat: a VMA can report
nonzero `AnonHugePages` even when the exact `VAPTR` address for a key is not
inside the split-eligible THP extent. A single-key smoke can therefore fail
with `ENOENT` even though the surrounding VMA still contains THP-backed pages.

## What Changed

- Updated `python/kernmlops/replay/replay_trace.py` to add:
  - Redis pid lookup via `INFO server`
  - cached Redis pid lookup once per replay run
  - VAPTR address lookup via `VAPTR FIELD field0 <key>`
  - direct syscall `462` invocation via Python `ctypes`
  - fail-fast Redis/VAPTR setup behavior with retry-and-warn syscall handling
  - restart of the active Redis process shape instead of `systemctl restart redis`
- Added focused unit coverage in:
  - `testing/test_replay_trace.py`
- Updated:
  - `tests/syscall_verification/README.md`
  - `RESEARCH.md`

## Why This Works

The new `break_page(...)` path follows the same data flow the research needs at
replay time:

1. Resolve the live Redis pid from Redis itself.
2. Resolve the live value address for the target key with `VAPTR`.
3. Call `split_thp(pid, vaddr)` against that exact address.

This keeps the split decision keyed to Redis object identity instead of asking
the operator to manually copy a pid and address into a separate tool.

## Automated Validation

Executed:

- `/users/SahilSC/HugePageResearch/.venv/bin/python -m unittest testing.test_replay_trace testing.test_vaptr_hook testing.test_redis_vaptr_benchmark`

Result:

- `Ran 17 tests ... OK`

Covered behavior:

- success path for pid lookup + `VAPTR` + syscall invocation
- `(nil)` and malformed `VAPTR` replies fail fast
- missing pid metadata
- restart of the active ad hoc Redis process shape
- restore wait uses a snapshot-sized timeout instead of the old 30 second limit
- restart does not feed Redis its rewritten `127.0.0.1:6379` process title
- syscall failure retry, warning, and replay continuation

## Replay Bring-Up Note

While validating the user-facing replay flow, two restore bugs were confirmed
and fixed:

- replay must restart the active ad hoc Redis process instead of assuming
  `redis-server.service`
- replay must *not* reuse the `127.0.0.1:6379` token from Redis's process
  title, because Redis treats that as a config path on startup

After those fixes, the baseline replay command completed successfully on the
captured trace and wrote a one-row result file with `runtime_s_1 = 2.075621`.

## Docker Smoke Result

Environment:

- entered through `make docker`
- used `sg docker -c 'cd /users/SahilSC/HugePageResearch && make docker'`
  because the host shell could not talk to `/var/run/docker.sock` directly
- container ran as `root`
- Redis smoke used port `6380` to avoid the already-running host Redis on `6379`

Observed behavior:

- First single-key smoke:
  - inserted one `2 MiB` hash value
  - `VAPTR` returned a real address
  - `mapping_info` showed `anon_huge_pages_kb=2048`
  - both `split_thp_cli` and Python `break_page(...)` returned `ENOENT`
- Interpretation:
  - the VMA contained THP-backed pages, but the exact key address was not inside
    the split-eligible THP extent

Successful smoke:

- restarted Redis fresh
- inserted `16` keys with `2 MiB` hash values
- retried keys until `break_page(...)` returned `True`
- first observed success:
  - key: `user-smoke-01`
  - address: `0x7fffef800009`
  - VMA `anon_huge_pages_kb`: `8192 -> 6144`
- explicit follow-up success:
  - key: `user-smoke-03`
  - address: `0x7fffef580009`
  - `break_page_result=True`
  - VMA `anon_huge_pages_kb`: `6144 -> 4096`

## Why The Smoke Is Still Valid

The successful multi-key smoke is the right acceptance for this feature. The
goal is not "every large key splits on the first try"; the goal is "given a key
whose exact address lies inside a split-eligible THP, the replay-time Python
path can discover that address through `VAPTR` and split the THP."

The failed single-key attempt was still useful because it exposed an important
measurement caveat: `smaps` is VMA-level, so it can overstate how directly a
single object is tied to the THP shown in that VMA.

## Alternatives Considered

### Shell out to `split_thp_cli`

Rejected for the implementation.

Pros:

- reuses the existing C helper exactly as-is

Cons:

- adds an extra subprocess hop for every replay breakpoint
- keeps the main replay path dependent on a separate built binary
- makes error handling and logging less direct in Python

### Keep the smoke to one key only

Rejected after live testing.

Pros:

- simpler procedure

Cons:

- produced a misleading false negative
- hides the difference between "VMA contains THP" and "exact key address is in
  the THP extent"

## Pros And Cons

Pros:

- replay-time breakpoints can now target live Redis object addresses
- unit coverage exists for the new failure modes
- live Docker validation confirms the Python path can split Redis-backed THPs

Cons:

- one-key smoke results are not deterministic enough to trust blindly
- the full replay-trace benchmark loop still has not been exercised end-to-end
- the current smoke depends on temporarily forcing THP `enabled=always`
