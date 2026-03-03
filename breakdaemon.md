# Break Daemon (THP Split by VA) - Design Notes

## Goal

Break a specific transparent huge page (THP) in a running process by virtual address (VA), starting with:

- "break the **nth** huge page in my Redis process"

and make it **generalizable** so later we can target:

- exact VA
- nth THP in process
- nth THP in a specific VMA
- predicate-based targets (age, address range, etc.)

Most important requirement:

- be able to say with high confidence that the page was actually split

## Terms (important)

- **VA**: virtual address
- **VMA**: virtual memory area (a range in `/proc/<pid>/smaps` / `/proc/<pid>/maps`)
- **THP**: transparent huge page (typically 2 MiB PMD-mapped on x86_64)

We want to target a **THP extent by VA**, not just a VMA.

## Existing Repo Support We Should Reuse

This repo already has a THP intervention path in `smaps_harness`:

1. It finds the target Redis PID by regex:
   - `python/kernmlops/data_collection/bpf_instrumentation/smaps_harness.py`
   - `_find_target_pid()` scans `/proc/*/comm`

2. It already knows the debugfs split interface path:
   - `HugepageHarnessConfig.split_debugfs_path`
   - default: `/sys/kernel/debug/split_huge_pages`
   - `python/kernmlops/kernmlops_config/hugepage_harness.py`

3. It already issues split commands using:
   - `"{pid},{start_hex},{end_hex}"`
   - written to `split_debugfs_path`
   - `_run_intervention(...)` in `smaps_harness.py`

This means we do **not** need to invent a new split mechanism first. We can build a more precise selector/verifier around the same interface.

## Why the current `smaps_harness` is not enough for "nth THP by VA"

`smaps_harness` parses VMAs and tracks THP-related VMA stats (for example `anon_hugepages_kb`, `thp_eligible`), but:

- it samples **VMA regions**, not individual THP extents
- a single VMA may contain multiple THPs + 4K pages
- `AnonHugePages` is aggregated per VMA, so it is not enough to prove a specific THP at a specific VA was split

So for **certainty** and **nth-THP targeting**, we need a more exact enumerator/verifier.

## Proposed Approach (V1)

### High-level idea

Build a small "break daemon" (or one-shot tool with daemon mode) that:

1. Resolves the target process (`redis-server` PID or explicit PID)
2. Enumerates THP extents by VA (exact mode)
3. Selects target THP (nth or exact VA)
4. Issues split via existing debugfs interface
5. Verifies the THP at that VA is no longer huge
6. Logs proof (before/after evidence)

## Target Selection Modes (generalizable API)

Start with these modes:

1. `nth_in_process` (first implementation)
   - example: split the 5th THP in the process address space
2. `exact_va`
   - split the THP containing VA `0x...`
3. `nth_in_vma` (later)
   - select a VMA, then the nth THP inside it

Recommended default for user-facing CLI:

- `nth_in_process` with **1-based indexing**

Internal representation can still use 0-based indexing if easier.

## Process Resolution (Redis first, generalizable later)

### Redis default

Use the same strategy as `smaps_harness`:

- scan `/proc/*/comm`
- match regex (default `redis-server`)
- choose newest/highest PID if multiple match

This is already configurable via:

- `HugepageHarnessConfig.redis_name_regex`

### Generalizable behavior

Support either:

- `--pid <pid>` (explicit, preferred for deterministic runs)
- `--comm-regex <regex>` (Redis default)

If both are provided:

- `--pid` wins

## Exact THP Enumeration by VA (for certainty)

### Why exact enumeration is needed

To break "the nth huge page" and be certain it split, we need an actual list of THP extents like:

- `[start_va, end_va)` per THP

not just VMA-level aggregates.

### Recommended exact source of truth

Use:

- `/proc/<pid>/pagemap` (VA -> PFN/present)
- `/proc/kpageflags` (PFN flags, including compound/THP-related flags)

This requires root / appropriate permissions.

### Exact enumeration algorithm (V1)

1. Read candidate VMAs from `/proc/<pid>/smaps` or `/proc/<pid>/maps`
   - limit to anonymous/private writable VMAs (Redis heap-ish regions)
   - keep start/end VA ranges
2. For each VMA, walk VA in 4 KiB steps (or 2 MiB-aligned stepping + local refinement)
3. For each 4 KiB VA:
   - read pagemap entry
   - if not present, skip
   - get PFN
   - read `/proc/kpageflags` for PFN
4. Identify THP extents by compound page flags (head/tail)
   - group contiguous 4 KiB pages that belong to a THP mapping
5. Emit THP extents as:
   - `start_va`
   - `end_va`
   - `size_bytes` (expected ~2 MiB)
   - `vma_start`, `vma_end`
   - optional PFN summary for proof

### Simpler fallback mode (less certain)

If exact pagemap/kpageflags inspection is unavailable:

- use `smaps` VMA samples and assume 2 MiB-aligned THP positions within a VMA

This is acceptable for coarse experiments, but **not** for certainty.

## How to Break the Target THP

Once target extent is selected:

- `target_start = selected_thp.start_va`
- `target_end = selected_thp.end_va`

Issue split through existing debugfs interface:

- write `"{pid},{hex(target_start)},{hex(target_end)}\n"` to `/sys/kernel/debug/split_huge_pages`

This is exactly the command format already used by `smaps_harness`.

## How to Be Certain It Broke (Verification)

### Strong verification (recommended)

Immediately re-run exact THP enumeration and verify:

- there is **no THP extent** covering the target VA range anymore

Success condition:

- target VA was THP before
- target VA is present after
- target VA pages are now 4 KiB pages (not compound THP)

This is the strongest practical proof for a live process.

### Additional confirmation signals (optional)

Use one or more:

1. `smaps` delta on the containing VMA
   - `AnonHugePages` decreases (not always exactly 2048 kB if multiple THPs/mixed state)
2. vmstat counters (`vmstat_harness`)
   - `thp_split_page` / `thp_split_pmd` increments
3. THP trace events (`thp_trace` hook)
   - split-related events near intervention timestamp
4. Intervention record
   - `thp_interventions.success == True`
   - note: this only proves command write succeeded, not split success by itself

### Handle re-collapse race

In a live Redis workload, khugepaged or memory activity may re-collapse later.

So the verifier should record:

- `pre` snapshot (THP exists)
- `post_immediate` snapshot (THP absent -> success)
- optional `post_stable` snapshot after X ms (to detect re-collapse)

This avoids false negatives/positives.

## "nth Huge Page" Selection Details

### Deterministic ordering

Sort all discovered THP extents by:

1. `start_va` ascending

Then select:

- `n`th extent (1-based in CLI)

### Scope controls (important for generalization)

Support filters before ranking:

- whole process (default)
- only anonymous VMAs
- only specific VMA range (`--vma-start`, `--vma-end`)
- only regions matching a predicate (future)

This makes "nth" reproducible and avoids surprises when mappings shift.

## Suggested CLI (one-shot first, daemon-capable)

### One-shot mode (implement first)

Example:

```bash
python -m kernmlops.analysis.breakdaemon \
  --pid 12345 \
  --selector nth \
  --n 5 \
  --verify exact \
  --split-debugfs-path /sys/kernel/debug/split_huge_pages
```

Redis-friendly example:

```bash
python -m kernmlops.analysis.breakdaemon \
  --comm-regex '^redis-server' \
  --selector nth \
  --n 1 \
  --verify exact
```

### Daemon mode (later)

Run a polling loop and split on trigger:

- `--daemon`
- `--poll-ms 200`
- `--trigger once|interval|rpc`

But do **not** start with daemon mode if the goal is to validate correctness quickly.

## Suggested Output / Proof Artifacts

For each attempted split, emit a structured record (JSON or parquet row):

- timestamp
- pid/tgid
- selector mode
- chosen `n`
- target `start_va`, `end_va`
- pre-check result (`is_thp_before`)
- split command string
- debugfs write success/error
- post-check result (`is_thp_after`)
- proof summary (`verified_split: true/false`)

This is the minimum to make the tool auditable.

## How This Fits the Current Repo

### Reuse now

- `HugepageHarnessConfig`:
  - `redis_name_regex`
  - `split_debugfs_path`
- `smaps_harness` command format for split
- `smaps_vma_samples` / `thp_interventions` as auxiliary evidence

### New code needed (not yet in repo)

1. Exact THP enumerator (pagemap + kpageflags)
2. Selector abstraction (`nth`, `exact_va`, later `nth_in_vma`)
3. Strong verifier
4. Small CLI / tool wrapper (one-shot first)

## Failure Modes / Edge Cases (must handle)

1. `split_debugfs_path` missing or debugfs not mounted
   - fail fast with clear message
2. No matching Redis PID
   - fail fast
3. No THPs found
   - fail fast (or dry-run output)
4. `n` out of range
   - print count and fail
5. Target THP disappears before split (workload churn)
   - retry selection or fail deterministically
6. Debugfs write succeeds but no split observed
   - report as failure (`command accepted != verified split`)
7. Re-collapse shortly after split
   - report immediate success + later re-collapse separately

## Practical V1 Recommendation (minimal but correct)

Implement **one-shot** first:

- explicit `--pid` and `--comm-regex`
- selector: `nth` + `exact_va`
- split via debugfs
- strong exact verification via pagemap/kpageflags
- JSON log output

Then add daemon mode once correctness is proven.

## Why this meets your request

- Targets by **VA**
- Supports **nth huge page** now
- Is **generalizable** to other selectors later
- Provides a path to be **certain** the split actually happened (strong verification), not just "the kernel accepted a command"
