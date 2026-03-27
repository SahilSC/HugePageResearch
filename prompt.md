# Measuring Huge Page Utility in Redis

## Objective

Quantify the **utility of individual 2MB Transparent Huge Pages (THPs)** backing objects in a Redis server. Utility is measured as the **time delta** between two otherwise-identical benchmark runs:

- **Control run**: the target object's huge page is left intact.
- **Experimental run**: the target object's huge page is **split/demoted** into 4KB base pages at a precise point during execution.

A larger slowdown after splitting indicates the huge page was more useful (higher utility). A negligible or negative delta indicates low utility.

---

## Assumptions

1. **Deterministic workload.** The YCSB workload is configured so that the sequence of operations (keys accessed, read/write mix, ordering) is identical across runs. This is required so that the only variable between control and experimental runs is the page split.
2. **Addressable objects.** Redis has been modified with a custom `VA_POINTER` command: given a key, it returns the virtual address of the corresponding object in Redis's address space. This lets us identify which huge page backs a given key.
3. **Reproducible break point.** Because the workload is deterministic, we can define the break point in terms of per-key operational counts (e.g., "after the 500th read of key X") rather than wall-clock time, ensuring consistency across runs.
4. **Kernel-level page splitting is feasible.** The Linux kernel already has internal functions to split THPs. A custom syscall can invoke these, given the virtual address of a page within the target process.

---

## Methods Considered

### Method 1 — External Wrapper Script (Python)

**How it works:** A separate Python script orchestrates the experiment. It launches or communicates with the YCSB workload and independently tracks per-key metadata (read count, write count, total access count). When the threshold is reached for the target key, the script issues the page-break call (via the custom syscall) and the benchmark continues.

**Pros:**
- No modifications to YCSB itself; the workload generator stays stock/clean.
- Easy to change thresholds, target keys, and break conditions without recompiling anything.
- Clear separation of concerns: benchmarking logic vs. experiment control.

**Cons:**
- The script must be able to observe or infer per-key operations externally. This may require parsing YCSB output in real time or intercepting commands, adding complexity.
- Timing accuracy depends on how quickly the external script can react; there may be a lag of several operations between the threshold being hit and the break actually executing.
- Adds an extra process that could introduce noise into performance measurements.

**Implementation outline:**
1. Write a Python wrapper that starts the YCSB benchmark.
2. Intercept or tail YCSB's operation log in real time to maintain per-key counters.
3. When a counter hits the configured threshold, call the custom syscall (e.g., via `ctypes` or a small C helper) with the target virtual address (obtained ahead of time via `VA_POINTER`).
4. Let the benchmark complete, then collect timing results.

---

### Method 2 — Modified YCSB Workload Generator

**How it works:** Modify the YCSB client code directly to maintain internal per-key access counters. When the threshold is hit, YCSB itself triggers the page-break call before continuing with the next operation.

**Pros:**
- Highest accuracy: the break happens at the exact operation boundary, with zero lag.
- No external processes or inter-process communication needed.
- All experiment logic lives in one place.

**Cons:**
- Requires forking/modifying YCSB source, increasing maintenance burden.
- Couples experiment logic to the benchmark tool; harder to reuse with a different workload generator later.
- YCSB is Java-based, so calling a custom syscall requires JNI or shelling out, adding some friction.

**Implementation outline:**
1. Fork the YCSB repository.
2. Add per-key counters in the workload executor (e.g., a `HashMap<String, int[]>` for read/write/total counts).
3. After each operation, check if the target key's counter has hit the threshold.
4. If so, invoke the syscall — either via JNI, `Runtime.exec()` to a small C binary, or a Redis command that triggers it server-side.

---

### Method 3 — Middleman Proxy

**How it works:** Insert a transparent proxy between the YCSB client and the Redis server. The proxy forwards all commands and responses unchanged but internally tallies per-key access counts. When the threshold is reached, the proxy triggers the page break.

**Pros:**
- Neither YCSB nor Redis need modification (beyond the existing `VA_POINTER` addition).
- Accurate counting: the proxy sees every command before it reaches Redis.
- Reusable with any Redis client, not just YCSB.

**Cons:**
- Adds latency to every operation (extra network hop or IPC), which may skew the very performance measurements you're trying to take.
- More infrastructure to build and maintain (a custom Redis proxy is nontrivial).
- Must handle the Redis protocol correctly, including pipelining and multi/exec if used.

**Implementation outline:**
1. Write a lightweight TCP proxy (Python, C, or Go) that binds on a local port and forwards to Redis.
2. Parse incoming Redis commands to extract the key and operation type; increment per-key counters.
3. On threshold hit, trigger the syscall.
4. Point YCSB at the proxy port instead of the Redis port.

---

## Comparison Summary

| Criteria | External Wrapper | Modified YCSB | Middleman Proxy |
|---|---|---|---|
| Break-point accuracy | Moderate (lag possible) | Highest (exact operation) | High (sees every command) |
| Implementation effort | Low–Medium | Medium (Java + JNI) | Medium–High (protocol parsing) |
| Benchmark interference | Low (separate process) | Minimal | Higher (extra hop) |
| YCSB modifications needed | None | Yes | None |
| Reusability | Moderate | Low (tied to YCSB) | High |
| Maintenance burden | Low | Higher (forked YCSB) | Medium |

---

## Page-Breaking Mechanism: Syscall vs. Kernel Module

Two approaches were discussed for actually performing the THP split:

**Custom syscall (chosen approach):** Add a new syscall to the kernel that takes a process ID and virtual address, looks up the corresponding huge page, and calls the kernel's internal THP splitting function (e.g., `split_huge_page_to_list` or similar). Invoked from user space like any other syscall.

**Kernel module:** Load a kernel module that exposes a similar interface (e.g., via `/proc` or `ioctl`). However, modules may have limited access to certain internal page-table manipulation functions, and exposing the interface cleanly from user space adds complexity. This was deprioritized in favor of the syscall approach.

---

## General To-Do List

- [ ] Finalize which orchestration method to use (Method 1, 2, or 3) and begin implementation.
- [ ] Implement the custom syscall for THP splitting.
- [ ] Validate that `VA_POINTER` returns stable, correct addresses across the deterministic workload.
- [ ] Design the experiment matrix: which keys to target, which thresholds to test, how many runs per configuration.
- [ ] Build the end-to-end pipeline: start Redis → warm up → run workload with/without page break → collect timing.
- [ ] Validate correctness: confirm that after the syscall fires, the target huge page is actually demoted (e.g., check `/proc/<pid>/smaps`).
- [ ] Run initial experiments and present results at the next lab meeting.

## Your To-Do List (Syscall Implementation)

- [ ] **Research existing kernel internals.** Read through `mm/huge_memory.c` — functions like `split_huge_page`, `split_huge_page_to_list`, and `dequeue_huge_page` are likely relevant. Understand their arguments, locking requirements, and return values.
- [ ] **Add the syscall entry.** Register a new syscall number in the syscall table for your architecture (x86-64). Add the entry in `arch/x86/entry/syscalls/syscall_64.tbl` and the prototype in the appropriate header.
- [ ] **Implement the syscall handler.** The handler should accept a target PID and virtual address, then:
  1. Find the target process's `mm_struct` (via `find_get_task_by_vpid` or similar).
  2. Walk the page tables to find the huge page at the given address.
  3. Call the internal split function.
  4. Return success/failure to user space.
- [ ] **Handle permissions and safety.** Decide whether this syscall requires `CAP_SYS_ADMIN` or root. Add appropriate checks so it can't be used to corrupt arbitrary process memory.
- [ ] **Write a user-space test harness.** A small C program that calls the syscall (via `syscall()`) on a known huge-page-backed allocation and verifies the split occurred by checking `/proc/self/smaps`.
- [ ] **Test against Redis.** Use `VA_POINTER` to get an address, call the syscall, and confirm via `smaps` that the 2MB region was replaced by 4KB pages.
- [ ] **Document the syscall interface** (arguments, return codes, error conditions) so the orchestration layer can call it correctly.
