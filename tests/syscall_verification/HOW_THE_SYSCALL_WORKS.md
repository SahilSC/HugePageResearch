# How the `split_thp` Syscall Works — A Complete Guide

This document explains **exactly** how the custom `split_thp` Linux kernel syscall
works, step by step, with code examples. It is written so that someone with no
kernel experience can follow along.

---

## Table of Contents

1. [What Problem Does This Solve?](#1-what-problem-does-this-solve)
2. [What Is a Syscall?](#2-what-is-a-syscall)
3. [The `split_thp` Syscall at a Glance](#3-the-split_thp-syscall-at-a-glance)
4. [Step-by-Step: How We Added the Syscall to the Kernel](#4-step-by-step-how-we-added-the-syscall-to-the-kernel)
5. [Step-by-Step: What the Kernel Does When You Call `split_thp`](#5-step-by-step-what-the-kernel-does-when-you-call-split_thp)
6. [Step-by-Step: How Userspace Calls the Syscall](#6-step-by-step-how-userspace-calls-the-syscall)
7. [Error Codes Explained](#7-error-codes-explained)
8. [The Verification Tests Explained](#8-the-verification-tests-explained)
9. [Putting It All Together: End-to-End Example with Redis](#9-putting-it-all-together-end-to-end-example-with-redis)
10. [Background Concepts](#10-background-concepts)

---

## 1. What Problem Does This Solve?

Linux uses **Transparent Huge Pages (THP)** — 2 MiB memory pages instead of the
normal 4 KiB pages — to speed up memory access. But sometimes a huge page is
wasteful: if only a tiny part of that 2 MiB is "hot" (frequently accessed), the
rest wastes memory and can cause performance issues.

**Before this syscall**, the only way to split a THP from userspace was through a
debug filesystem interface (`/sys/kernel/debug/split_huge_pages`), which was
clunky, required root, and couldn't target a specific address in a specific
process.

**This syscall** lets a program say: *"Hey kernel, split the huge page at this
address in this process back into 512 normal 4 KiB pages."*

```
Before split_thp:                 After split_thp:
+---------------------------+     +----+----+----+----+----+---
|                           |     | 4K | 4K | 4K | 4K | 4K |...
|     One 2 MiB huge page   |  -> |page|page|page|page|page| (512 total)
|                           |     +----+----+----+----+----+---
+---------------------------+
```

---

## 2. What Is a Syscall?

A **system call (syscall)** is how a user program asks the kernel to do something
it can't do on its own — like reading files, allocating memory, or (in our case)
splitting a huge page.

Here's the analogy: your program is a customer at a restaurant. The kernel is the
kitchen. You can't walk into the kitchen yourself — you hand the waiter (the
syscall interface) a slip of paper with your order (the syscall number +
arguments). The kernel processes it and hands back a result.

```
Your program                    Linux kernel
-----------                     ------------
syscall(462, pid, addr)  --->   Look up handler for syscall #462
                                Run sys_split_thp(pid, addr)
                         <---   Return 0 (success) or negative error
```

Every syscall has a **number**. Ours is **462**.

---

## 3. The `split_thp` Syscall at a Glance

| Property         | Value                                              |
|------------------|----------------------------------------------------|
| **Name**         | `split_thp`                                        |
| **Number**       | 462 (x86_64)                                       |
| **Arguments**    | `pid_t pid` — which process; `unsigned long vaddr` — which address |
| **Returns**      | `0` on success, negative error code on failure     |
| **Kernel file**  | `mm/huge_memory.c`                                 |
| **Kernel version** | Ubuntu `6.8.0-101.101`, custom build `6.8.12-splitthp` |

Calling it from C:

```c
#include <sys/syscall.h>
#include <unistd.h>

#define __NR_split_thp 462

long result = syscall(__NR_split_thp, target_pid, target_address);
if (result == 0) {
    printf("Huge page was split successfully!\n");
} else {
    perror("split_thp failed");
}
```

That's it. Two arguments, one return value.

---

## 4. Step-by-Step: How We Added the Syscall to the Kernel

Three files were patched in the Ubuntu 6.8.0 kernel source tree. The full patch
is archived in git at commit `1fcdd1e` as
`kernel-patches/ubuntu-6.8.0-101.101-split_thp.patch`.

### Step 1: Register the syscall number

**File**: `arch/x86/entry/syscalls/syscall_64.tbl`

This file is a table that maps syscall numbers to handler functions. We added one
line at the end:

```
462	64	split_thp		sys_split_thp
```

What this means:
- `462` — the syscall number (next available after 461)
- `64` — this is a 64-bit only syscall
- `split_thp` — the name
- `sys_split_thp` — the C function the kernel will call

### Step 2: Declare the function prototype

**File**: `include/linux/syscalls.h`

We added one line in the x86-specific section:

```c
asmlinkage long sys_split_thp(pid_t pid, unsigned long vaddr);
```

This tells the compiler "this function exists somewhere, here's its signature."

### Step 3: Write the actual implementation

**File**: `mm/huge_memory.c`

This is where the real logic lives. We added two new `#include` lines and ~80
lines of code. The next section breaks this down line by line.

### Step 4: Build and install the kernel

```bash
# Inside the kernel source tree:
make -j$(nproc) bzImage modules        # compile
sudo make modules_install              # install modules
sudo make install                      # install kernel + initrd
sudo reboot                            # boot into 6.8.12-splitthp
```

After reboot, `uname -r` shows `6.8.12-splitthp`, confirming the custom kernel.

---

## 5. Step-by-Step: What the Kernel Does When You Call `split_thp`

Here is the complete kernel implementation with annotations. There are two
functions: the main syscall entry point, and a helper that does the actual split.

### The main entry point: `SYSCALL_DEFINE2(split_thp, ...)`

```c
SYSCALL_DEFINE2(split_thp, pid_t, pid, unsigned long, vaddr)
{
```

`SYSCALL_DEFINE2` is a kernel macro that creates a function named `sys_split_thp`
with 2 arguments. The macro handles all the boilerplate for safely copying
arguments from userspace.

#### Step 5a: Align the address to a page boundary

```c
    vaddr &= PAGE_MASK;
```

Virtual addresses point to individual bytes, but pages start at 4096-byte
boundaries. `PAGE_MASK` is `0xFFFFFFFFFFFFF000` — this zeros out the bottom 12
bits. For example, if you pass address `0x7f1234567890`, this becomes
`0x7f1234567000`.

Why? Because pages are the smallest unit the kernel manages. The address
`0x7f1234567890` and `0x7f1234567000` are on the same page, so both should find
the same huge page.

#### Step 5b: Find the target process

```c
    task = find_get_task_by_vpid(pid);
    if (!task)
        return -ESRCH;
```

The kernel looks up the process by its PID. If no process with that PID exists
(maybe it already exited), return `ESRCH` ("no such process").

#### Step 5c: Check permissions (ptrace-style authorization)

```c
    mm = mm_access(task, PTRACE_MODE_ATTACH_REALCREDS);
    put_task_struct(task);
    if (IS_ERR(mm)) {
        ret = PTR_ERR(mm);
        return ret == -EACCES ? -EPERM : ret;
    }
    if (!mm)
        return -ESRCH;
```

This is the security check. `mm_access()` asks: "Does the calling process have
permission to poke at the target process's memory?" It uses the same rules as
`ptrace` (the debugger interface):

- **Same user, parent process**: usually allowed
- **Root**: always allowed
- **Random unrelated process**: denied (`EPERM`)
- **Controlled by**: `/proc/sys/kernel/yama/ptrace_scope`

The `mm` (memory descriptor) is the kernel's representation of a process's entire
virtual address space. If we get it, we have permission to proceed.

`put_task_struct(task)` releases our reference to the process — we don't need the
process struct anymore, just its memory descriptor.

#### Step 5d: Lock the address space and do the split

```c
    mmap_read_lock(mm);
    ret = split_thp_vaddr_locked(mm, vaddr);
    mmap_read_unlock(mm);
    mmput(mm);
    return ret;
}
```

The `mmap_read_lock` prevents the target process from changing its memory layout
(adding/removing mappings) while we're walking through it. Then we call the
helper function (below). Then we unlock and release the memory descriptor.

### The helper function: `split_thp_vaddr_locked()`

This does the actual work of finding and splitting the huge page.

#### Step 5e: Find the VMA (Virtual Memory Area)

```c
    vma = vma_lookup(mm, vaddr);
    if (!vma)
        return -EFAULT;
```

The kernel organizes a process's memory as a list of **VMAs** — contiguous
regions with the same permissions. For example, one VMA for your code, one for
your stack, one for each `mmap()` allocation.

`vma_lookup()` finds which VMA contains our address. If the address isn't mapped
at all, return `EFAULT` ("bad address").

```
Process memory layout (simplified):
  0x400000-0x401000  [code]        <- VMA 1
  0x7f0000-0x7f2000  [heap]        <- VMA 2 (our target might be here)
  0x7ffd00-0x7fff00  [stack]       <- VMA 3

vma_lookup(mm, 0x7f0500) -> returns VMA 2
vma_lookup(mm, 0x500000) -> returns NULL (not mapped)
```

#### Step 5f: Reject unsuitable VMAs

```c
    if (vma_not_suitable_for_thp_split(vma))
        return -ENOENT;
```

Some VMAs can never contain THPs we can split:
- **Special huge VMAs** (DAX, device memory)
- **I/O mapped memory** (`VM_IO`)
- **HugeTLB pages** (these are a different kind of huge page, not THP)

#### Step 5g: Resolve the physical page

```c
    page = follow_page(vma, vaddr, FOLL_GET | FOLL_DUMP);
    if (IS_ERR_OR_NULL(page))
        return -EFAULT;
```

`follow_page()` walks the process's page tables to find the actual physical page
backing our virtual address. Think of it like following a chain of pointers:

```
Virtual addr  -->  Page Table Entry  -->  Physical page in RAM
  0x7f0500          (kernel walks           (the actual 4K or 2M
                     the page tables)        chunk of memory)
```

`FOLL_GET` takes a reference (so the page won't disappear), and `FOLL_DUMP`
skips special zero pages.

#### Step 5h: Check if it's actually a THP

```c
    folio = page_folio(page);
    if (!is_transparent_hugepage(folio)) {
        ret = -ENOENT;
        goto out_put;
    }
```

A **folio** is the kernel's container for a set of related pages. A THP folio
contains 512 contiguous 4 KiB pages (= 2 MiB). A regular folio contains just
one 4 KiB page.

If the folio isn't a THP, there's nothing to split — return `ENOENT`.

#### Step 5i: Check if the THP can be split right now

```c
    if (!can_split_folio(folio, NULL)) {
        ret = -EAGAIN;
        goto out_put;
    }
```

Some THPs are temporarily pinned (e.g., in the middle of I/O). `can_split_folio`
checks the reference count. If it's pinned, return `EAGAIN` ("try again later").

#### Step 5j: Lock the folio and split it

```c
    if (!folio_trylock(folio)) {
        ret = -EAGAIN;
        goto out_put;
    }

    ret = split_folio(folio);
    folio_unlock(folio);
```

`folio_trylock` is a non-blocking lock attempt. If someone else holds the lock,
we don't wait — just return `EAGAIN`.

`split_folio()` is the existing kernel function that does the actual split. It:
1. Allocates new page table entries for each of the 512 small pages
2. Updates all reverse mappings (so the kernel knows where each small page is used)
3. Updates accounting (memory statistics, cgroup charges, etc.)
4. Frees the compound page metadata

After this call, what was one 2 MiB page is now 512 independent 4 KiB pages.
**The data in memory is unchanged** — only the page table structure changes.

#### Step 5k: Clean up

```c
out_put:
    folio_put(folio);
    return ret;
}
```

Release our reference to the folio and return the result.

### Full kernel code in one block

For reference, here is the complete implementation as a single listing:

```c
/* Added to mm/huge_memory.c */

static inline bool vma_not_suitable_for_thp_split(struct vm_area_struct *vma)
{
    return vma_is_special_huge(vma) || (vma->vm_flags & VM_IO) ||
            is_vm_hugetlb_page(vma);
}

static int split_thp_vaddr_locked(struct mm_struct *mm, unsigned long vaddr)
{
    struct vm_area_struct *vma;
    struct page *page;
    struct folio *folio;
    int ret;

    vma = vma_lookup(mm, vaddr);
    if (!vma)
        return -EFAULT;

    if (vma_not_suitable_for_thp_split(vma))
        return -ENOENT;

    page = follow_page(vma, vaddr, FOLL_GET | FOLL_DUMP);
    if (IS_ERR_OR_NULL(page))
        return -EFAULT;

    folio = page_folio(page);
    if (!is_transparent_hugepage(folio)) {
        ret = -ENOENT;
        goto out_put;
    }

    if (!can_split_folio(folio, NULL)) {
        ret = -EAGAIN;
        goto out_put;
    }

    if (!folio_trylock(folio)) {
        ret = -EAGAIN;
        goto out_put;
    }

    ret = split_folio(folio);
    folio_unlock(folio);

out_put:
    folio_put(folio);
    return ret;
}

SYSCALL_DEFINE2(split_thp, pid_t, pid, unsigned long, vaddr)
{
    struct task_struct *task;
    struct mm_struct *mm;
    int ret;

    vaddr &= PAGE_MASK;

    task = find_get_task_by_vpid(pid);
    if (!task)
        return -ESRCH;

    mm = mm_access(task, PTRACE_MODE_ATTACH_REALCREDS);
    put_task_struct(task);
    if (IS_ERR(mm)) {
        ret = PTR_ERR(mm);
        return ret == -EACCES ? -EPERM : ret;
    }
    if (!mm)
        return -ESRCH;

    mmap_read_lock(mm);
    ret = split_thp_vaddr_locked(mm, vaddr);
    mmap_read_unlock(mm);
    mmput(mm);

    return ret;
}
```

---

## 6. Step-by-Step: How Userspace Calls the Syscall

### The simplest possible call

```c
#include <sys/syscall.h>
#include <unistd.h>
#include <stdio.h>

#define __NR_split_thp 462

int main(void) {
    pid_t target_pid = 12345;              // PID of target process
    unsigned long target_addr = 0x7f0000;  // address inside a THP

    long rc = syscall(__NR_split_thp, target_pid, target_addr);

    if (rc == 0)
        printf("Split succeeded!\n");
    else
        perror("split_thp");

    return rc != 0;
}
```

### How the wrapper in this repo works (`common.c`)

The repo wraps the raw syscall in a helper that captures both the return code and
`errno`:

```c
// From tests/syscall_verification/common.c

struct syscall_result invoke_split_thp(pid_t pid, unsigned long vaddr)
{
    struct syscall_result result;

    errno = 0;
    result.rc = syscall(__NR_split_thp, pid, vaddr);
    result.err = errno;
    return result;
}
```

Usage:

```c
struct syscall_result r = invoke_split_thp(getpid(), (unsigned long)my_buffer);
if (r.rc == 0) {
    // success — the huge page at my_buffer is now 512 small pages
} else if (r.err == ENOSYS) {
    // the running kernel doesn't have our custom syscall
} else if (r.err == ENOENT) {
    // there's no THP at that address (already small pages)
}
```

### How to set up a THP to test with

Before you can split a THP, you need one. Here's how the test suite creates one:

```c
// 1. Allocate 2 MiB of memory, aligned to a 2 MiB boundary
char *mapping;
size_t length;
create_aligned_mapping(&mapping, &length);  // length = 2 MiB

// 2. Write data into it (so the pages get physically allocated)
fill_pattern(mapping, length, 0x42);

// 3. Tell the kernel "please use a huge page here"
madvise(mapping, length, MADV_HUGEPAGE);

// 4. Tell the kernel "collapse these pages into a THP right now"
madvise(mapping, length, MADV_COLLAPSE);

// 5. Verify it worked by reading /proc/self/smaps
//    AnonHugePages should show 2048 kB
```

The alignment matters because THPs must start at a 2 MiB boundary. The helper
`create_aligned_mapping()` over-allocates and trims to guarantee alignment:

```c
// Allocate 6 MiB (3x THP_SIZE) to guarantee a 2 MiB-aligned region exists
void *reservation = mmap(NULL, 3 * THP_SIZE, PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

// Find the first 2 MiB-aligned address within the reservation
unsigned long aligned = (base + THP_SIZE - 1) & ~(THP_SIZE - 1);

// Unmap the prefix and suffix we don't need
munmap(before_aligned, prefix_size);
munmap(after_aligned_plus_thp, suffix_size);

// Result: exactly 2 MiB at a 2 MiB-aligned address
```

---

## 7. Error Codes Explained

When the syscall fails, it returns `-1` and sets `errno` to one of these:

| errno     | Value | When it happens | Plain English |
|-----------|-------|-----------------|---------------|
| `ENOSYS`  | 38    | Kernel doesn't have syscall 462 | You're on the wrong kernel — boot `6.8.12-splitthp` |
| `ESRCH`   | 3     | `find_get_task_by_vpid()` returns NULL | That PID doesn't exist (typo, or process exited) |
| `EPERM`   | 1     | `mm_access()` returns `-EACCES` | You don't have permission to touch that process's memory |
| `EFAULT`  | 14    | `vma_lookup()` or `follow_page()` returns NULL | That address isn't mapped in the target process |
| `ENOENT`  | 2     | `is_transparent_hugepage()` returns false | There's no THP at that address (it's regular 4K pages) |
| `EAGAIN`  | 11    | `can_split_folio()` or `folio_trylock()` fails | The page is busy right now — try again |

### Decision flowchart

```
syscall(462, pid, addr)
    |
    v
Does PID exist?
    |-- No  --> ESRCH
    |-- Yes --> Do we have permission?
                    |-- No  --> EPERM
                    |-- Yes --> Is addr mapped?
                                    |-- No  --> EFAULT
                                    |-- Yes --> Is it a THP?
                                                    |-- No  --> ENOENT
                                                    |-- Yes --> Can we split it now?
                                                                    |-- No  --> EAGAIN
                                                                    |-- Yes --> split_folio()
                                                                                    |-- 0 (success!)
```

---

## 8. The Verification Tests Explained

### `self_split_verify.c` — Split your own THP

This test proves the syscall works when a process splits its own huge page.

**What it does, step by step:**

1. **Create a 2 MiB aligned mapping** — `create_aligned_mapping()`
2. **Fill it with a known pattern** — `fill_pattern(mapping, length, 0x5a)`
   - Writes a deterministic byte at every offset so we can verify data survives
3. **Collapse into a THP** — `collapse_to_thp(mapping, length)`
   - Uses `MADV_HUGEPAGE` + `MADV_COLLAPSE`
4. **Read `/proc/self/smaps`** — confirms `AnonHugePages: 2048 kB`
5. **Call the syscall** — `invoke_split_thp(getpid(), mapping + 4096)`
   - Note: passes `mapping + 4096` (not the start) to prove the kernel
     correctly handles any address within the huge page
6. **Read `/proc/self/smaps` again** — confirms `AnonHugePages: 0 kB`
7. **Verify data integrity** — `verify_pattern(mapping, length, 0x5a)`
   - Every byte must be unchanged after the split
8. **Test error cases:**
   - Split same mapping again -> `ENOENT` (it's already small pages)
   - Split a regular (non-THP) mapping -> `ENOENT`
   - Split unmapped address 0 -> `EFAULT`
   - Split with bogus PID `INT_MAX` -> `ESRCH`

### `cross_process_split_verify.c` — Split another process's THP

This test proves the syscall works **across processes** — a parent splitting a
child's THP.

**What it does, step by step:**

1. **Fork a child process**
2. **Child**: creates a THP mapping, fills it with pattern `0x33`, sends the
   address to the parent via a pipe, then waits for commands
3. **Parent**: receives the child's THP address
4. **Parent reads child's `/proc/<child>/smaps`** — confirms THP before split
5. **Parent calls the syscall** — `invoke_split_thp(child_pid, child_addr + 4096)`
6. **Parent reads smaps again** — confirms THP is gone
7. **Parent sends `'V'` (verify) command to child** — child checks its own data
   and reports back that every byte is intact
8. **Parent sends `'Q'` (quit) command** — child exits
9. **Parent tries splitting the dead child's PID** — confirms `ESRCH`

### `split_thp_cli.c` — Manual one-shot tool

A simple command-line tool for manual testing:

```bash
# Split a THP in process 12345 at address 0x7f4a00000000
./split_thp_cli 12345 0x7f4a00000000
```

### `mapping_info.c` — Inspect a mapping

Shows the `/proc/<pid>/smaps` entry for a given address — useful for checking
before/after state:

```bash
./mapping_info 12345 0x7f4a00000000
# Output:
# start=0x7f4a00000000
# end=0x7f4a00200000
# size_kb=2048
# anon_huge_pages_kb=2048    <-- THP present
# kernel_page_size_kb=4
# mmu_page_size_kb=4
# contains_thp=yes
```

---

## 9. Putting It All Together: End-to-End Example with Redis

This is the real-world use case: splitting a THP inside a running Redis server.

### Why Redis?

This project studies THP behavior under real workloads. Redis allocates large
hash tables that often get backed by THPs. Our `VAPTR` Redis module can report
the exact virtual address of a hash field, giving us a target for `split_thp`.

### Step-by-step walkthrough

```bash
# 1. Boot the patched kernel
#    After reboot, verify:
uname -r
# Expected: 6.8.12-splitthp

# 2. Start Redis with the VAPTR module
redis-server ./config/redis.conf --loadmodule ./redis-module/vaptr.so

# 3. Insert a large value (forces THP-eligible allocation)
python3 -c "print('X' * (2*1024*1024), end='')" | redis-cli -x HSET user-proof field0

# 4. Get the PID and virtual address
REDIS_PID=$(pgrep -n redis-server)
ADDR=$(redis-cli --raw VAPTR FIELD field0 user-proof)

# 5. Check: is there a THP at that address?
./tests/syscall_verification/mapping_info "$REDIS_PID" "$ADDR"
# Look for: anon_huge_pages_kb=2048, contains_thp=yes

# 6. Split it!
./tests/syscall_verification/split_thp_cli "$REDIS_PID" "$ADDR"
# Expected: split_thp(pid=..., vaddr=0x...) succeeded

# 7. Confirm the THP is gone
./tests/syscall_verification/mapping_info "$REDIS_PID" "$ADDR"
# Look for: anon_huge_pages_kb=0, contains_thp=no

# 8. Verify Redis still works
redis-cli HGET user-proof field0 | head -c 20
# Should still return data — the split preserves all content
```

---

## 10. Background Concepts

### What is a Transparent Huge Page (THP)?

Normal memory pages are 4 KiB. A THP is 2 MiB (512 normal pages glued together).
The "transparent" part means the kernel creates and manages them automatically —
your program doesn't need to ask for them. The kernel promotes groups of 4 KiB
pages into a 2 MiB page when it thinks it'll help performance (fewer TLB misses).

### What is a folio?

A folio is the kernel's internal container for one or more contiguous pages that
form a logical unit. A THP folio wraps 512 pages. When we "split" a folio, those
512 pages become 512 independent folios, each wrapping one page.

### What is a VMA?

A Virtual Memory Area describes a contiguous range of virtual addresses with the
same permissions and backing. When you call `mmap()`, the kernel creates a VMA.
Your process might have dozens of VMAs — one for code, one for stack, one for
each allocation.

### What is ptrace authorization?

`ptrace` is the Linux mechanism that debuggers use. The kernel reuses its
permission model: "Can process A inspect/modify process B's memory?" This depends
on:
- Are they the same user?
- Is A a parent/ancestor of B?
- What does `/proc/sys/kernel/yama/ptrace_scope` say?

We chose ptrace-style auth instead of requiring root (`CAP_SYS_ADMIN`) because
it's more granular — a monitoring tool can split THPs in its own child processes
without needing root.

### What is `PAGE_MASK`?

`PAGE_MASK` = `0xFFFFFFFFFFFFF000` on x86_64 (where page size = 4096 = 0x1000).
ANDing an address with `PAGE_MASK` rounds it down to the start of its page:

```
0x7f1234567890 & PAGE_MASK = 0x7f1234567000
```

---

## File Map

All syscall-related files in this repository:

| File | Purpose |
|------|---------|
| `tests/syscall_verification/common.h` | Shared constants, structs, function declarations |
| `tests/syscall_verification/common.c` | Syscall wrapper, THP setup helpers, smaps parser |
| `tests/syscall_verification/self_split_verify.c` | Automated self-process test |
| `tests/syscall_verification/cross_process_split_verify.c` | Automated cross-process test |
| `tests/syscall_verification/split_thp_cli.c` | Manual CLI split tool |
| `tests/syscall_verification/mapping_info.c` | Manual smaps inspection tool |
| `tests/syscall_verification/Makefile` | Build system |
| `tests/syscall_verification/README.md` | Quick reference and usage |
| `kernel-patches/ubuntu-6.8.0-101.101-split_thp.patch` | The actual kernel patch (in git history at `1fcdd1e`) |
