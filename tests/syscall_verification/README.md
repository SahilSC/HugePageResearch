# Syscall Verification

This directory contains standalone user-space verifiers for the custom
`split_thp(pid, vaddr)` syscall added to the Ubuntu `6.8.0-101.101` kernel tree.

If you are starting on a fresh machine and need the full kernel build,
patch-application, reboot, and verification flow, follow [`SETUP.md`](../../SETUP.md)
first and then return here for the verifier details.

## Files

- `split_thp_cli`: small manual syscall invoker for `pid + vaddr`
- `mapping_info`: prints the `smaps` entry covering a `pid + vaddr`
- `self_split_verify`: self-process THP create/collapse/split verifier
- `cross_process_split_verify`: parent/child verifier for remote split behavior

## Build

```bash
cd ~/HugePageResearch/tests/syscall_verification
make
```

## Important Runtime Note

These binaries will build on the stock host kernel, but the split syscall itself
will only work after booting the patched kernel. On an unpatched kernel the
verifiers should stop with `ENOSYS`.

## Automated Verifiers

Run the self-process verifier:

```bash
cd ~/HugePageResearch/tests/syscall_verification
./self_split_verify
```

Expected behavior on the patched kernel:

- creates a 2 MiB aligned anonymous mapping
- forces THP collapse with `MADV_COLLAPSE`
- confirms `AnonHugePages: 2048 kB` before the syscall
- calls `split_thp(getpid(), vaddr)`
- confirms `AnonHugePages: 0 kB` after the syscall
- verifies the mapping contents are unchanged
- checks:
  - second split on the same mapping returns `ENOENT`
  - a mapped non-THP address returns `ENOENT`
  - an unmapped address returns `EFAULT`
  - a bogus pid returns `ESRCH`

Run the parent/child verifier:

```bash
cd ~/HugePageResearch/tests/syscall_verification
./cross_process_split_verify
```

Expected behavior on the patched kernel:

- child creates and collapses one THP-backed anonymous mapping
- parent splits that THP in the child
- parent confirms the child mapping is no longer THP-backed
- child confirms the bytes did not change
- checks:
  - second split on the same child mapping returns `ENOENT`
  - child non-THP mapping returns `ENOENT`
  - child unmapped address returns `EFAULT`
  - dead target pid returns `ESRCH`

## Manual Redis Smoke

The default Redis workload in this repo uses small field values and will usually
stay in `listpack` encoding, which is not the direct-pointer case for `VAPTR`.
For manual smoke validation, use a field value large enough to force
`hashtable` encoding and to give Redis a real THP candidate.

Build the tools:

```bash
cd ~/HugePageResearch/tests/syscall_verification
make
```

Build the Redis module:

```bash
cd ~/HugePageResearch/redis-module
make
```

The commands in this section are written for the host checkout at
`~/HugePageResearch`. If you are inside the Docker container started by
`make docker`, the same repo is mounted at `/KernMLOps`.

Start Redis with the repo config and the `VAPTR` module:

```bash
cd ~/HugePageResearch
REDIS_BIN="$(command -v redis-server)"
"$REDIS_BIN" ./config/redis.conf --loadmodule ./redis-module/vaptr.so
```

For replay and manual VAPTR smoke runs, do not start Redis as bare
`redis-server ...`. `replay_trace.py` expects `INFO server.executable` to
already be a real executable path.

Insert one hash with a 2 MiB field value:

```bash
python3 - <<'PY' | redis-cli -x HSET user-proof field0
print('X' * (2 * 1024 * 1024), end='')
PY
redis-cli OBJECT ENCODING user-proof
```

Expected encoding:

```text
hashtable
```

Resolve the Redis pid and target address:

```bash
REDIS_PID="$(pgrep -n redis-server)"
ADDR="$(redis-cli --raw VAPTR FIELD field0 user-proof)"
printf 'pid=%s addr=%s\n' "$REDIS_PID" "$ADDR"
```

Check the target mapping before the split:

```bash
cd ~/HugePageResearch/tests/syscall_verification
./mapping_info "$REDIS_PID" "$ADDR"
```

Invoke the syscall:

```bash
./split_thp_cli "$REDIS_PID" "$ADDR"
```

Or invoke the replay-time Python helper that now performs the same
`INFO server` -> `VAPTR FIELD field0` -> `split_thp` flow used by
`python/kernmlops/data_collection/replay_trace.py`:

```bash
cd ~/HugePageResearch
PYTHONPATH=./python/kernmlops .venv/bin/python - <<'PY'
import redis
from data_collection.replay_trace import break_page

client = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
redis_pid = int(client.info("server")["process_id"])
print(break_page(client, redis_pid, "user-proof"))
PY
```

Check the same mapping again:

```bash
./mapping_info "$REDIS_PID" "$ADDR"
```

What to expect:

- before the split, the mapping should report nonzero `anon_huge_pages_kb` if
  the address is inside a THP
- after the split, the same mapping should report `anon_huge_pages_kb=0`

Important caveat:

- `mapping_info` reads one `smaps` entry for the whole VMA, not a per-4 KiB
  page verdict for the exact `VAPTR` address
- a VMA can report nonzero `anon_huge_pages_kb` even when the specific
  `VAPTR` address sits outside the split-eligible THP extent
- in that case `split_thp_cli` can raise `ENOENT`, while the Python
  `break_page(...)` helper retries and then returns `False`

If the ad hoc 2 MiB insert still does not land inside a THP, use the repo's
large-value Redis e2e configs instead:

- `config/redis_vaptr_e2e.yaml`
- `config/redis_vaptr_access_bit_e2e.yaml`

For a more reliable manual smoke, load several 2 MiB keys and try them until
one returns `True` from `break_page(...)`. In the live Docker validation for
this repo, the first inserted key shared a VMA with THP-backed pages but was
not itself inside the split-eligible THP; a later large key in the same run did
split successfully.

## Authorization Note

The syscall intentionally uses ptrace-style authorization. On Ubuntu systems
with `/proc/sys/kernel/yama/ptrace_scope=1`, an unrelated same-user Redis
process may return `EPERM` even when the address is otherwise valid. If that
happens, either:

- run the smoke test as root, or
- temporarily lower `ptrace_scope`, or
- use the parent/child verifier, which naturally fits the ptrace ancestry rule
