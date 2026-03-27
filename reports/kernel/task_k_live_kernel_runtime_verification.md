# Live Kernel Runtime Verification

## Summary

This task rebooted the machine into the installed `6.8.12-splitthp` kernel and
ran the dedicated userspace verifier suite against the live syscall
implementation. Both the self-process and same-user cross-process verifier paths
passed.

## What Changed

- Rebooted into the installed custom kernel:
  - `uname -r` now reports `6.8.12-splitthp`
- Re-ran the verifier suite under:
  - `tests/syscall_verification/self_split_verify`
  - `tests/syscall_verification/cross_process_split_verify`
- Updated:
  - `RESEARCH.md`
  - `MEMORY_BLOAT_README.md`
  - `docs/archive/researchprogress.md`

## Why This Works

The runtime result is meaningful because it exercises the real installed kernel,
not just the source tree or compile artifacts.

Observed behavior:

- `self_split_verify`
  - created a THP-backed anonymous mapping
  - observed `AnonHugePages: 2048 kB` before the syscall
  - invoked `split_thp(getpid(), vaddr)`
  - observed `AnonHugePages: 0 kB` after the syscall
  - exited successfully
- `cross_process_split_verify`
  - created a THP-backed child mapping
  - observed `AnonHugePages: 2048 kB` before the syscall
  - invoked the syscall from the parent against the child mapping
  - observed `AnonHugePages: 0 kB` after the syscall
  - exited successfully

This confirms the authored syscall works for both:

- self-process use
- same-user remote-process use

It also confirms the kernel preserved mapping contents while demoting the THP.

## Concrete Runtime Evidence

Verifier outcomes:

- `self_split_verify: PASS`
- `cross_process_split_verify: PASS`
- both commands exited `0`

THP transition observed in both cases:

- before split:
  - `anon_huge_pages_kb=2048`
  - `contains_thp=yes`
- after split:
  - `anon_huge_pages_kb=0`
  - `contains_thp=no`

## What This Does Not Yet Cover

- Redis/VAPTR manual smoke validation is still not run in this checkpoint.
- The verifier suite already covers the main negative cases internally, but this
  checkpoint did not add extra ad hoc manual negative-case runs.

## Alternatives Considered

### Stop after install without rebooting

Rejected for this checkpoint.

Pros:

- avoids live-machine disruption

Cons:

- leaves the most important syscall behavior unverified

### Go straight to Redis/VAPTR instead of the dedicated verifier suite

Rejected for first live runtime validation.

Pros:

- closer to the final research workload

Cons:

- adds Redis encoding, process-control, and Yama/ptrace policy variables
- makes failures harder to localize than the dedicated verifier suite

## Pros And Cons

Pros:

- first end-to-end proof that the syscall works on the live patched kernel
- validates both self and cross-process cases
- keeps the remaining Redis smoke work clearly separated as an optional next step

Cons:

- Redis/VAPTR validation still remains
- success here does not prove every possible permission-policy configuration
