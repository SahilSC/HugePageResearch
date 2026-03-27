# Userspace Verifier Suite

## Summary

This task added a standalone userspace verification suite under
`tests/syscall_verification/` for the custom `split_thp(pid, vaddr)` syscall.
The suite is intentionally independent of the existing `testing/` Python
`unittest` tree because the user explicitly requested a dedicated
`tests/syscall_verification/` path for this kernel-facing work.

## What Changed

Added:

- `tests/syscall_verification/Makefile`
- `tests/syscall_verification/common.h`
- `tests/syscall_verification/common.c`
- `tests/syscall_verification/split_thp_cli.c`
- `tests/syscall_verification/mapping_info.c`
- `tests/syscall_verification/self_split_verify.c`
- `tests/syscall_verification/cross_process_split_verify.c`
- `tests/syscall_verification/README.md`
- `tests/syscall_verification/.gitignore`

Updated:

- `RESEARCH.md`
- `MEMORY_BLOAT_README.md`

## Why This Works

The suite mirrors the real syscall contract and the real verification boundary:

1. `self_split_verify` creates a 2 MiB aligned anonymous mapping, fills it,
   forces THP collapse with `MADV_COLLAPSE`, confirms the mapping is THP-backed
   via `/proc/<pid>/smaps`, invokes `split_thp(getpid(), vaddr)`, and then
   confirms the THP is gone while the bytes remain unchanged.
2. `cross_process_split_verify` exercises the same logic on a child process, so
   the remote-process authorization and mm lookup path are covered without
   needing Redis or the existing harness.
3. `mapping_info` exposes the exact `smaps` entry for any `pid + vaddr`, which
   makes manual Redis/VAPTR smoke testing practical.
4. `split_thp_cli` is the minimal manual syscall invoker needed for both ad hoc
   testing and the Redis path.

The suite deliberately uses `smaps`-level verification instead of a heavier
PFN-level dependency because:

- `smaps` is available to the same user
- it directly exposes `AnonHugePages`
- it keeps the verifier runnable without extra privileges or repo coupling

## Verification Run

I built the suite with:

```bash
make -C tests/syscall_verification
```

I then ran both verifiers on the current stock `6.8.0-101-generic` kernel:

- `tests/syscall_verification/self_split_verify`
- `tests/syscall_verification/cross_process_split_verify`

Observed behavior:

- both verifiers successfully created and synchronously collapsed a THP
- both reported `AnonHugePages: 2048 kB` before the syscall attempt
- both then stopped with `ENOSYS`

That is the expected result on the current host because the patched kernel has
not been installed or booted yet. This gives useful pre-boot evidence:

- THP setup and `smaps` inspection logic work
- the verifier binaries build and run
- the remaining gate is the kernel boot/install boundary, not a userspace bug

## Redis Smoke Design

The Redis smoke path is documented in `tests/syscall_verification/README.md`.
The important design choice there is:

- do not use the repo's default small-value Redis workload for manual `VAPTR`
  smoke

Reason:

- the default Redis workload keeps hash values in `listpack`
- `VAPTR FIELD` only gives the direct pointer we care about in the `hashtable`
  case

So the README instead points to:

- an ad hoc 2 MiB hash-field insertion, or
- the repo's large-value e2e configs:
  - `config/redis_vaptr_e2e.yaml`
  - `config/redis_vaptr_access_bit_e2e.yaml`

## Alternatives Considered

### Reuse `testing/`

Rejected for this task.

Pros:

- keeps one test convention

Cons:

- mixes syscall-specific C helpers into an unrelated Python unittest tree
- works against the explicit user choice of `tests/syscall_verification/`

### PFN-level pagemap/kpageflags verification

Rejected for v1.

Pros:

- stronger physical-page evidence

Cons:

- more privilege-sensitive
- more code
- not necessary to prove the syscall contract at this checkpoint

### Redis-first verification instead of synthetic mappings

Rejected for first-pass automation.

Pros:

- closest to the eventual experiment

Cons:

- drags in Redis process setup, module loading, encoding thresholds, and
  ptrace/Yama policy
- makes failures harder to localize

## Pros And Cons

Pros:

- small, direct, and kernel-focused
- validates both self and remote-process use cases
- includes manual CLIs for Redis/VAPTR smoke work
- already preflighted successfully on the stock kernel up to the syscall boundary

Cons:

- full pass still requires booting the patched kernel
- manual Redis smoke may hit `EPERM` on Ubuntu when `ptrace_scope=1`
- `smaps` verification is strong enough for this checkpoint but not PFN-level
