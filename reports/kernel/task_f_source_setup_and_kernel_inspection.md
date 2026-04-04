# Source Setup And Kernel Inspection

## What I did

- Downloaded the exact Ubuntu source package
  - `linux-source-6.8.0_6.8.0-101.101_all.deb`
- Extracted its bundled source tarball into the repo at
  - `external/linux/ubuntu-6.8.0-101.101/`
- Copied the running host kernel config into the tree at
  - `external/linux/ubuntu-6.8.0-101.101/.config`
- Inspected the real Ubuntu source files we will patch:
  - `mm/huge_memory.c`
  - `arch/x86/entry/syscalls/syscall_64.tbl`
  - `include/linux/syscalls.h`
- Checked local build-tool availability and confirmed these are still missing before compile/config refresh:
  - `flex`
  - `bison`
  - `pahole`

## Why this works

- It moves the project off of inferred kernel behavior and onto the exact Ubuntu `6.8.0-101.101` tree that matches the host kernel line.
- It avoids patching against the installed headers alone, which are not sufficient for adding a new syscall.
- It gives us the actual Ubuntu THP split helper implementation to mirror instead of guessing the right page lookup and locking pattern.
- Copying the running `.config` makes later compile validation start from the host-aligned configuration rather than a generic default.

## Key findings

### 1. The exact source tree is now present locally

- Path:
  - `external/linux/ubuntu-6.8.0-101.101/`
- Fetch method:
  - `apt-get download` of the pinned source package
  - `dpkg-deb -x` to unpack the package payload
  - `tar -xjf` of the bundled `/usr/src/linux-source-6.8.0.tar.bz2`

### 2. Ubuntu already has the split helper we should mirror

In `mm/huge_memory.c`, Ubuntu already ships:

- `split_huge_pages_pid(int pid, unsigned long vaddr_start, unsigned long vaddr_end)`

That helper:

- resolves the target process
- acquires `mm` with `get_task_mm()`
- holds `mmap_read_lock(mm)`
- walks the requested range with `vma_lookup()` and `follow_page(..., FOLL_GET | FOLL_DUMP)`
- checks `can_split_folio()`
- locks with `folio_trylock()`
- splits with `split_folio()`

This matters because it gives us a real in-tree pattern for remote THP splitting on the exact Ubuntu line we care about.

### 3. The new syscall should add auth, not invent a new split path

The existing debugfs helper does not enforce the same-user ptrace-style authorization the user wants.

So the syscall should likely:

- obtain the target `task_struct`
- authorize and acquire `mm` with `mm_access(..., PTRACE_MODE_ATTACH_REALCREDS)`
- then perform the single-address lookup/split using the same basic shape as `split_huge_pages_pid()`

That is a better fit than hardcoding a separate `get_user_pages_remote()`-based design before source inspection.

### 4. The x86_64 syscall slot is confirmed in-source

- `arch/x86/entry/syscalls/syscall_64.tbl` ends its common entries at:
  - `461 common lsm_list_modules sys_lsm_list_modules`
- So the new x86_64 syscall can take `462` if we append it there.

## Alternatives considered

- Install `linux-source-6.8.0` system-wide with `apt-get install`.
  - Rejected for this checkpoint because downloading and unpacking the exact `.deb` into the repo is less invasive and still gives the full source tree we need.
- Continue planning from installed headers only.
  - Rejected because headers do not include the editable syscall table or `mm/huge_memory.c` implementation we must patch.
- Freeze the syscall internals before reading the Ubuntu source.
  - Rejected because the real Ubuntu helper already shows the local lookup/split path, and guessing would be more error-prone.

## Pros

- Exact source alignment with the running Ubuntu kernel line.
- Lower-risk next step because patching can now follow Ubuntu's real THP split helper.
- Less host mutation than a system-wide source-package install.
- Clear confirmation that the x86_64 syscall slot is available.

## Cons

- The repo is now much larger because it contains a full kernel source tree.
- Build prerequisites are still missing, so compile validation cannot happen yet.
- This checkpoint only grounds the implementation; it does not yet patch or compile the kernel.
