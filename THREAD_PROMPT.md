# Follow-On Thread Prompt

Read `AGENTS.md` and `RESEARCH.md` first.

Continue the Redis THP utility project from the current repo state.

## Immediate objective

Implement the custom x86_64 Linux syscall for splitting the single Transparent Huge Page containing a given virtual address in a target process, plus user-space verification tests and documentation.

## Hard constraints

- Do not boot or reboot automatically into a patched kernel. Stop and tell me first so I can save or push work.
- Stay aligned with the Ubuntu `6.8.0-101` kernel line unless you uncover a concrete blocker.
- Use the repo's existing Redis `VAPTR` module for object-address discovery.
- Keep the syscall ABI as exact 2 arguments: `pid + vaddr`.
- Use same-user ptrace-style authorization semantics for remote-process access.
- Reuse the kernel's existing THP split path in `mm/huge_memory.c`-style code instead of inventing a bespoke page-table split implementation.

## Important validated finding

We explicitly verified live in Docker that even with:

- `kernel.randomize_va_space = 0`
- `transparent_hugepages = never`
- `khugepaged/scan_sleep_millisecs = 8640000`
- the same deterministic Redis dataset and same 10 sampled keys

the relative ordering of those sampled Redis objects in memory still changes across fresh Redis runs.

Measured pairwise ordering flips across the 10 sampled keys:

- run0 vs run1: `14 / 45`
- run0 vs run2: `18 / 45`
- run1 vs run2: `22 / 45`

So do not assume that "key X" corresponds to the same page neighborhood or same THP neighborhood across separate fresh runs.

## Research implication

A naive control/experimental comparison across separate fresh Redis runs does not support a strong claim of "specific huge page utility" unless the target is additionally matched to the same THP extent across runs or the memory image is cloned/snapshotted from the same warmed state.

If you need to discuss experiment interpretation, frame this carefully:

- strongest safe claim without extra controls: utility of the THP containing the target key in that run
- stronger claim requiring additional control: utility of the same specific THP identity across runs

## Expected deliverables

- Kernel patch for the syscall
- Verification tests under `tests/syscall_verifcation/`
- Documentation covering:
  - syscall behavior
  - return/error cases
  - why the chosen kernel approach is correct
  - tradeoffs and alternatives considered
- Updates to `RESEARCH.md` as you learn more

## Recommended next actions

1. Locate or obtain the matching Ubuntu `6.8.0-101` source tree to patch.
2. Implement the syscall plumbing and split logic.
3. Add self-test and cross-process verification first.
4. Add Redis integration verification using `VAPTR`.
5. Stop before any boot/reboot step and report status.
