# Syscall Feasibility And Decision Matrix

## What I did

- Re-read the active handoff files:
  - `AGENTS.md`
  - `RESEARCH.md`
  - `THREAD_PROMPT.md`
  - `reports/archive/research_handoff.md`
- Audited the current repo state to separate prompt intent from actual code:
  - verified `external/linux/` is empty in this checkout
  - traced the current THP intervention path in `python/kernmlops/data_collection/bpf_instrumentation/thp_intervention_hook.py`
  - checked the hook registry, config layout, and existing `testing/` surface
  - re-read `prompt.md`, `breakdaemon.md`, and `docs/research/vaptr.md`
- Verified the live host/kernel packaging state:
  - host kernel: `6.8.0-101-generic`
  - installed headers: `/usr/src/linux-headers-6.8.0-101*`
  - exact source package available from apt metadata:
    - `linux-source-6.8.0=6.8.0-101.101`
    - package size: about `356 MB`
- Used the installed `6.8.0-101` headers and local man pages to reduce uncertainty around syscall plumbing and test feasibility:
  - confirmed `mm_access()` and `PTRACE_MODE_ATTACH_REALCREDS` exist
  - confirmed the x86_64 syscall number window currently ends at `__NR_syscalls = 462`
  - confirmed `MADV_COLLAPSE` exists locally and is documented as independent of the global THP sysfs mode

## Why this works

- It anchors the design in the actual host and repo state instead of assuming the prompt still matches the codebase.
- It avoids guessing against the wrong kernel line: the exact Ubuntu `6.8.0-101.101` source package is available, so we can patch the same line the host is running.
- It gives a verification strategy that is deterministic enough to be worth implementing:
  - create/collapse a THP in-process with `MADV_COLLAPSE`
  - call the syscall
  - verify demotion with `/proc/*/smaps` plus pagemap/kpageflags-style checks

## Main findings

### 1. The implementation blocker is source acquisition, not design feasibility

- The repo does not currently contain a populated kernel source tree.
- The host does contain exact matching headers and apt metadata for the exact Ubuntu source package.
- So the right next move is to fetch the exact `6.8.0-101.101` source tree if the user approves.

### 2. The current user-space split path already exists and should inform the syscall integration

- `thp_intervention_hook.py` already issues split commands through `/sys/kernel/debug/split_huge_pages`.
- That gives us a concrete baseline for behavior and for later A/B verification.
- It does not remove the need for the syscall, but it means the new path should be introduced with a comparison story, not in a vacuum.

### 3. Test placement is currently ambiguous

- Existing low-level tests live in `testing/` and use `unittest`.
- The handoff prompt asks for `tests/syscall_verifcation/` and spells `verification` as `verifcation`.
- Creating a new tree without deciding whether to preserve or fix that spelling will create unnecessary churn.

## Proposed implementation shape

### Kernel patch plan

In the exact Ubuntu `6.8.0-101.101` source tree, the likely minimal patch surface is:

- `arch/x86/entry/syscalls/syscall_64.tbl`
- `include/linux/syscalls.h`
- `mm/huge_memory.c`

Likely syscall shape:

- x86_64-only `SYSCALL_DEFINE2(..., pid_t pid, unsigned long vaddr)`
- authorize with `mm_access(task, PTRACE_MODE_ATTACH_REALCREDS)`
- locate the target mapping/folio for `vaddr`
- verify that the address lies inside a split-eligible THP
- reuse the existing THP split path in `mm/huge_memory.c`
- return normal kernel errors for:
  - bad pid
  - no accessible mm
  - unmapped address
  - address not inside a split-eligible THP
  - permission denial

### Verification test plan

#### Self-process test

Shape:

- small C helper allocates and faults a 2 MiB-aligned anonymous region
- helper uses `madvise(..., MADV_COLLAPSE)` to synchronously create a THP
- test calls the syscall on `getpid(), target_va`
- verifier checks pre/post state with:
  - `/proc/self/smaps`
  - pagemap + kpageflags-style inspection

Pros:

- deterministic precondition
- no cross-process auth ambiguity
- best first proof of correctness

Cons:

- still requires privileged verification machinery for pagemap/kpageflags-style checks
- needs careful handling of alignment and residency

#### Cross-process same-user test

Shape:

- parent starts helper child that creates/collapses a THP and publishes pid + target VA
- same-user parent invokes the syscall on child pid + VA
- verify split succeeded
- add negative cases:
  - unmapped VA
  - non-THP VA
  - dead pid

Pros:

- exercises the actual remote-process contract
- validates the intended ptrace-style permission model

Cons:

- somewhat more synchronization logic
- permission-negative cases are harder to test without another uid setup

#### Redis + VAPTR integration test

Shape:

- launch Redis with the repo’s `vaptr` module
- load a small deterministic dataset that forces hash-table encoding
- use `redis-cli --raw VAPTR FIELD field0 <key>` to get a VA
- call the syscall on the Redis pid + returned VA
- verify the containing THP is gone immediately after the split

Pros:

- end-to-end proof against the actual research target
- reuses the repo’s validated `VAPTR` work

Cons:

- slower and more environment-sensitive than the first two tests
- not ideal as the first correctness gate

## Decision matrix for the user

### Decision 1: fetch exact Ubuntu source now?

Option A: yes, fetch `linux-source-6.8.0=6.8.0-101.101`

Pros:

- exact match to the running host line
- highest confidence patch target
- avoids accidental drift to newer Ubuntu or upstream-only behavior

Cons:

- large download/unpack step
- increases local workspace size immediately

Option B: delay source fetch and only finish user-space scaffolding/docs first

Pros:

- lighter immediate change set
- can finalize test layout and helper design first

Cons:

- cannot finish the core syscall patch
- risks designing around guessed kernel details

### Decision 2: keep debugfs path alongside the syscall?

Option A: keep both temporarily

Pros:

- lets us compare old vs new split behavior
- lower migration risk

Cons:

- two intervention paths to maintain
- more documentation complexity

Option B: make syscall the only intended path after verification

Pros:

- cleaner long-term story
- less ambiguity for experiment orchestration

Cons:

- less fallback if the first syscall patch has edge cases

### Decision 3: what test path should we create?

Option A: preserve prompt literal `tests/syscall_verifcation/`

Pros:

- matches the current handoff prompt exactly

Cons:

- bakes in a spelling error

Option B: normalize to `tests/syscall_verification/`

Pros:

- cleaner long-term layout

Cons:

- deviates from the literal prompt unless we update the handoff docs too

## Recommended order once approved

1. Fetch exact Ubuntu `6.8.0-101.101` source into the workspace.
2. Implement the syscall in the Ubuntu tree with ptrace-style auth.
3. Add self-process verification first.
4. Add cross-process verification second.
5. Add Redis + `VAPTR` integration verification third.
6. Stop before any boot/reboot step.
