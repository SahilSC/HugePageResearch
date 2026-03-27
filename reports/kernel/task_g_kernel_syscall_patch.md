# Kernel Syscall Patch

## What I changed

- Added a new x86_64-only syscall entry to the Ubuntu `6.8.0-101.101` tree:
  - `arch/x86/entry/syscalls/syscall_64.tbl`
  - new slot: `462 64 split_thp sys_split_thp`
- Added the x86 syscall prototype:
  - `include/linux/syscalls.h`
  - `asmlinkage long sys_split_thp(pid_t pid, unsigned long vaddr);`
- Added the syscall implementation in:
  - `mm/huge_memory.c`

The new implementation:

- adds the includes needed for syscall and ptrace auth support
- defines a small internal helper that resolves and splits the THP covering exactly one virtual address while `mmap_read_lock(mm)` is held
- defines `SYSCALL_DEFINE2(split_thp, pid_t, pid, unsigned long, vaddr)`

## Why this works

- It does not invent a new THP split mechanism. It reuses the exact Ubuntu-local remote split path already present in `mm/huge_memory.c`.
- The only new kernel responsibility is the syscall wrapper around that path plus the explicit authorization check the user wanted.
- The implementation stays narrow:
  - one pid
  - one virtual address
  - one containing THP
- The syscall is x86_64-only, so patching `syscall_64.tbl` and the x86 prototype is the right scope. There is no need to widen this to asm-generic numbering for the first version.

## Exact behavior of the authored syscall

1. Align `vaddr` down to a page boundary.
2. Resolve the target task with `find_get_task_by_vpid(pid)`.
3. Acquire and authorize the target `mm` with:
   - `mm_access(task, PTRACE_MODE_ATTACH_REALCREDS)`
4. Hold `mmap_read_lock(mm)`.
5. Look up the VMA covering `vaddr`.
6. Reject special / IO / hugetlb VMAs.
7. Resolve the backing page with:
   - `follow_page(vma, vaddr, FOLL_GET | FOLL_DUMP)`
8. Confirm the backing folio is a transparent huge page.
9. Check `can_split_folio()`.
10. Take the folio lock with `folio_trylock()`.
11. Split with `split_folio()`.
12. Drop locks/refs and return the kernel status code.

## Current return/error behavior

- `-ESRCH`
  - target pid not found
  - target has no accessible `mm`
- `-EPERM`
  - ptrace-style authorization denied
- `-EFAULT`
  - address is unmapped or `follow_page()` cannot resolve it
- `-ENOENT`
  - address is in a special/unsupported VMA or is not backed by a THP
- `-EAGAIN`
  - folio exists but cannot be split immediately
- `split_folio()` return value
  - propagated directly on the actual split attempt

## Alternatives considered

- Reuse `get_user_pages_remote()` for lookup.
  - Rejected for now because the exact Ubuntu tree already has a working remote split helper shape based on `vma_lookup()` and `follow_page()`.
- Refactor the debugfs helper to share the new internal helper.
  - Rejected for this step because the user asked to leave the existing debugfs hook untouched at the repo level and keep the patch narrow.
- Update `include/uapi/asm-generic/unistd.h`.
  - Rejected for this first patch because this syscall is intentionally x86_64-only.

## Pros

- Minimal patch surface.
- Reuses the exact Ubuntu THP split path instead of guessing.
- Adds the required same-user ptrace-style authorization.
- Keeps the current debugfs split path available and unchanged for comparison.

## Cons

- Not compile-validated yet.
- Error semantics are now explicit, but they still need verification against real kernel builds and runtime behavior.
- The syscall exists only in the source tree right now; no test helper or user-space verifier has been added yet.
