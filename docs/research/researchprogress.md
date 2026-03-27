# Research Progress Log

This file is the detailed running log for the THP split syscall project and the current kernel install/boot checkpoint. It is meant to be updated as work progresses, with more detail than `RESEARCH.md`. `RESEARCH.md` stays as the distilled, end-state context file and should only be updated at the end of a checkpoint from the content here.

## Current Focus

Implement and validate a custom x86_64 Linux syscall that splits the single Transparent Huge Page containing a given `pid + vaddr` in a target process, then prepare a clean install/boot checkpoint for the patched Ubuntu kernel.

## High-Level State

- The Ubuntu `6.8.0-101.101` source tree is already present at `external/linux/ubuntu-6.8.0-101.101/`.
- The syscall patch is already authored in that tree.
- The dedicated userspace verifier suite exists under `tests/syscall_verification/`.
- The verifier suite builds cleanly.
- Both verifiers preflighted successfully on the current stock `6.8.0-101-generic` kernel up to the expected `ENOSYS` boundary.
- A full kernel build is currently in progress with a safe local install suffix so the patched kernel can coexist with the stock kernel.

## Major Decisions And Why

### Kernel line

- Decision: stay on Ubuntu `6.8.0-101`.
- Why: this matches the host kernel and installed headers, and it avoids drifting into a different ABI line while the work is still experimental.
- Consequence: the kernel work stays grounded in the same Ubuntu packaging assumptions that the repo already uses.

### Syscall shape

- Decision: exact 2-argument ABI `pid + vaddr`.
- Why: it keeps the kernel interface minimal and directly matches the research question.
- Consequence: the syscall can be used as a precise intervention tool rather than a range-based or policy-heavy interface.

### Address scope

- Decision: target the single THP containing `vaddr`, not a range API.
- Why: the research needs a precise split point in execution, not a broad demotion operation.
- Consequence: the syscall semantics stay narrow and easier to reason about.

### Authorization model

- Decision: use same-user ptrace-style authorization for remote-process access.
- Why: it is the right fit for a user-space experiment that needs controlled access across related processes without defaulting to a broad capability like `CAP_SYS_ADMIN`.
- Consequence: unrelated same-user processes can still be blocked by Yama `ptrace_scope`, which matters for manual Redis smoke validation.

### Kernel implementation strategy

- Decision: reuse the existing THP split path in `mm/huge_memory.c` instead of inventing a new page-table splitter.
- Why: modern Linux uses folio-based THP split internals, and Ubuntu already ships a remote THP split helper we can adapt.
- Consequence: lower risk, fewer moving parts, and better alignment with upstream/Ubuntu behavior.

### Repo-level debugfs hook

- Decision: leave the existing debugfs THP split hook untouched in the first implementation chunk.
- Why: changing two split paths at once would make it harder to validate the syscall work itself.
- Consequence: the new syscall is isolated from existing harness behavior.

### Verification location

- Decision: use `tests/syscall_verification/` for syscall-focused tests.
- Why: the user explicitly asked for that path, and it keeps this kernel work separate from the repo’s older `testing/` Python `unittest` tree.
- Consequence: the repo now has a second test convention for a good reason, not by accident.

### Redis smoke strategy

- Decision: keep Redis/VAPTR as a manual smoke path in v1, not the default automated suite.
- Why: the kernel-side syscall can be validated first with synthetic anonymous mappings; Redis adds process setup, module loading, encoding thresholds, and ptrace/Yama caveats.
- Consequence: the manual smoke path is documented but not required for the first kernel verification pass.

### `external/linux` handling

- Decision: do not try to solve the source tree with a top-level `.gitignore`.
- Why: `external/linux` is already a tracked gitlink/submodule path; `.gitignore` does not meaningfully manage that situation.
- Consequence: the human resume doc carries the recreate steps instead.

### Documentation split

- Decision: keep the detailed running log in `docs/archive/researchprogress.md` and only fold a final distilled summary back into `RESEARCH.md` at the end.
- Why: the checkpoint needs a live scratchpad with more detail than the stable research context file.
- Consequence: `RESEARCH.md` remains cleaner and easier to use for future handoffs.

## Verified Findings

### Host and kernel environment

- Host kernel observed: `6.8.0-101-generic`.
- Build dependencies installed locally:
  - `flex`
  - `bison`
  - `dwarves` / `pahole`
  - `libssl-dev`
  - `libelf-dev`
- Build disk space is sufficient for the current checkpoint.
- `sudo` is available without prompting.
- `sudo` is passwordless.
- Disk space available at the checkpoint:
  - `/` has about `37G` free
  - `/boot` is on the same mountpoint here, so it has the same available space

### Source tree and build configuration

- The Ubuntu source tree exists at `external/linux/ubuntu-6.8.0-101.101/`.
- The copied config has the trusted-key settings cleared for local builds:
  - `CONFIG_SYSTEM_TRUSTED_KEYS=""`
  - `CONFIG_SYSTEM_REVOCATION_KEYS=""`
- `make olddefconfig` has already been run successfully.
- The source tree Makefile reports `6.8.12` as the upstream base version.
- The copied `.config` carries the Ubuntu signature string:
  - `Ubuntu 6.8.0-101.101-generic 6.8.12`
- The current tree carries `CONFIG_LOCALVERSION="-splitthp"`.
- The install/build will be kept separate from the stock distro kernel by using `LOCALVERSION=-splitthp`, rather than editing `RESEARCH.md` mid-checkpoint.
- `make -s kernelrelease LOCALVERSION=-splitthp` reports `6.8.12-splitthp`.
- An earlier generated header still showed a doubled suffix:
  - `UTS_RELEASE "6.8.12-splitthp-splitthp"`
- That happened because `LOCALVERSION` was applied both in `.config` and once on the command line, so a cleanup rebuild is needed before install.

### Syscall feasibility

- `mm_access(struct task_struct *task, unsigned int mode)` is available.
- `PTRACE_MODE_ATTACH_REALCREDS` is available.
- Ubuntu already has a remote THP split helper in `mm/huge_memory.c`:
  - `split_huge_pages_pid(int pid, unsigned long vaddr_start, unsigned long vaddr_end)`
- That helper already uses the exact style we want to reuse:
  - `find_task_by_vpid()` / `get_task_mm()`
  - `mmap_read_lock()`
  - `vma_lookup()`
  - `follow_page(..., FOLL_GET | FOLL_DUMP)`
  - `can_split_folio()`
  - `folio_trylock()`
  - `split_folio()`
- The syscall patch follows that same shape and adds `mm_access(..., PTRACE_MODE_ATTACH_REALCREDS)` authorization.

### Syscall patch status

- The syscall patch exists in the Ubuntu tree.
- The syscall number is `462` on x86_64.
- The generated headers now expose:
  - `__NR_split_thp 462`
  - `__NR_syscalls 463`
- The relevant patched objects compiled successfully earlier in the workflow:
  - `arch/x86/entry/syscall_64.o`
  - `mm/huge_memory.o`
- The full safe build completed successfully.
- Built artifacts now exist:
  - `vmlinux`
  - `System.map`
  - `arch/x86/boot/bzImage`
- The patch-specific compile gates had already passed earlier in the workflow.

### THP and `madvise`

- `MADV_COLLAPSE` exists on this host.
- It is independent of the `/sys/kernel/mm/transparent_hugepage/*` sysfs mode.
- Host THP settings at the time of the research pass showed:
  - THP enabled state set to `never`
  - defrag set to `madvise`
  - khugepaged scan sleep set very large
- This means THP can still be forced in the verifier with `MADV_COLLAPSE` even when the sysfs default is `never`.

### Redis and VAPTR behavior

- The default Redis workload in this repo uses small values.
- `config/redis.conf` sets:
  - `hash-max-listpack-entries 512`
  - `hash-max-listpack-value 64`
- That means small hashes stay in `listpack` encoding.
- The Redis YCSB workload in `scripts/setup-benchmarks/redis-workload.properties` uses `fieldcount=256` and `fieldlength=16`, so it is still a `listpack`-style workload.
- The VAPTR e2e configs force a large field value:
  - `config/redis_vaptr_e2e.yaml`
  - `config/redis_vaptr_access_bit_e2e.yaml`
- Those configs are the right place to smoke-test direct-pointer access because they push the hash to `hashtable` encoding.
- `VAPTR FIELD field0 ...` only gives the stable in-place direct pointer when the hash is `hashtable`, not `listpack`.

### Redis address stability

- Within a single Redis run, a resolved key address is stable for that run.
- Across fresh Redis runs, addresses and relative object ordering are not stable.
- Live Docker validation already showed that the same 10 sampled keys changed relative ordering across runs, with pairwise flips recorded earlier.
- Research implication:
  - a fresh-run experiment cannot assume that the same key lands in the same THP neighborhood across runs without an extra control

## Current Build / Install Strategy

1. Build the patched Ubuntu kernel tree with a separate local version suffix:
   - `LOCALVERSION=-splitthp`
2. Keep the patched kernel distinct from the stock distro kernel:
   - expected local release string: `6.8.12-splitthp`
3. Perform a cleanup rebuild to remove the doubled `-splitthp-splitthp` release string before install.
4. Install the patched kernel and modules only after the cleanup rebuild completes.
5. Stop before rebooting and tell the user so they can save or push work.

The safe install suffix matters because the stock kernel is still the active host kernel and should not be overwritten in place.

## Verifier Status

### Built

The userspace verifier suite under `tests/syscall_verification/` builds cleanly.

Current components:

- `split_thp_cli`
- `mapping_info`
- `self_split_verify`
- `cross_process_split_verify`

### What the verifiers do

- `self_split_verify`
  - creates a 2 MiB aligned anonymous mapping
  - fills it with a known byte pattern
  - forces THP collapse with `MADV_COLLAPSE`
  - checks `AnonHugePages: 2048 kB`
  - calls the syscall on `getpid(), vaddr`
  - checks `AnonHugePages: 0 kB` after the split
  - verifies the data pattern did not change
  - checks expected negative cases:
    - second split on the same mapping returns `ENOENT`
    - a mapped non-THP address returns `ENOENT`
    - an unmapped address returns `EFAULT`
    - a bogus pid returns `ESRCH`

- `cross_process_split_verify`
  - child creates and collapses a THP-backed mapping
  - parent splits the child mapping
  - both sides verify the mapping contents did not change
  - checks expected negative cases:
    - second split on the same child mapping returns `ENOENT`
    - child non-THP mapping returns `ENOENT`
    - child unmapped address returns `EFAULT`
    - dead target pid returns `ESRCH`

- `split_thp_cli`
  - small manual syscall invoker for `pid + vaddr`

- `mapping_info`
  - prints the `smaps` entry covering `pid + vaddr`

### Runtime preflight result

- Both verifiers were run on the current stock kernel.
- Both successfully created and collapsed a THP.
- Both observed `AnonHugePages: 2048 kB` before the syscall attempt.
- Both then stopped with `ENOSYS`, which is expected because the patched kernel is not booted yet.
- This confirms the harness logic is correct and the remaining boundary is kernel install/boot, not userspace setup.

## Current Live Progress

### What is running now

- The full safe kernel build completed successfully.
- The build command used `LOCALVERSION=-splitthp`.
- The build had already progressed far into unrelated subsystems and did not show any syscall-specific build failure.
- The remaining issue is version-string cleanup before install, not a compile failure.

### What was confirmed right before the build

- `make -s kernelrelease LOCALVERSION=-splitthp` reports `6.8.12-splitthp`.
- The source tree Makefile itself reports base release `6.8.12`.
- The stock host kernel remains `6.8.0-101-generic`.
- Disk space is sufficient to continue the build and eventual install:
  - `37G` free on `/`
  - same available space on `/boot` because it is the same mountpoint here
- Stock Ubuntu module vermagic still reflects the distro kernel ABI, for example:
  - `6.8.0-101-generic SMP preempt mod_unload modversions`
- That is concrete evidence that the stock modules do not cleanly match the custom-built kernel release string.

### Why this checkpoint is separate

- The build is a long-running, mostly unrelated verification pass after the syscall patch and verifier work are already done.
- The install/boot boundary is the first point where the workflow stops being purely local and becomes user-visible.
- The user wants a checkpoint before any reboot, so the next action must be explicit.

## Remaining Work

1. Let the full kernel build finish.
2. Install the patched kernel and modules with the `-splitthp` release suffix.
3. Update the bootloader entry if needed.
4. Stop before rebooting and report back to the user.
5. After the user approves, boot the patched kernel.
6. Rerun:
   - `tests/syscall_verification/self_split_verify`
   - `tests/syscall_verification/cross_process_split_verify`
7. If needed, run the Redis/VAPTR smoke path next.
8. Only after this checkpoint is complete, fold the durable summary into `RESEARCH.md`.

## Notes For The Next Session

- Do not edit `RESEARCH.md` yet from the progress log alone.
- Do not assume the Redis smoke path will work with the default workload; use a hashtable-forming configuration.
- Do not rely on a top-level `.gitignore` entry to manage `external/linux`.
- Keep the install separate from the stock kernel so a reboot failure cannot strand the machine on a half-replaced ABI.
- Keep the build/install progress log here until the checkpoint is complete, then copy the distilled facts into `RESEARCH.md`.

## Resumed Install Checkpoint

### Re-checked context

- `RESEARCH.md` was re-read at the start of this resumed checkpoint to confirm the standing goals, constraints, and install boundary.
- The repo status at resume time is clean except for the expected untracked progress log:
  - `docs/archive/researchprogress.md`
- No other tracked files were changed for this checkpoint resume.

### Kernel tree verification

- The kernel tree still reports the expected release string:
  - `make -s kernelrelease` returns `6.8.12-splitthp`
- This confirms the tree is still on the intended safe-install path.
- The local version suffix remains the one chosen to avoid colliding with the stock Ubuntu kernel ABI.

### Pre-install artifacts confirmed

- The required pre-install artifacts exist in the kernel tree:
  - `vmlinux`
  - `System.map`
  - `arch/x86/boot/bzImage`
  - `include/generated/utsrelease.h`
  - `include/config/kernel.release`
- This means the tree is in a build-complete state and ready for the next install-oriented step once the version-string cleanup decision is applied.

### Install checkpoint meaning

- The resumed checkpoint is now anchored on a verified, build-complete tree rather than an in-progress compile.
- The remaining work is install-oriented, not compile-oriented.
- The next decision point is whether to do the cleanup rebuild before installation so the release string and generated `utsrelease.h` stay consistent.

## Install Results

### Safe install completed

- The patched kernel installation completed successfully using:
  - `sudo make modules_install install`
- The install ran with the safe split release suffix and did not overwrite the stock distro kernel in place.
- The machine is still running the stock kernel until reboot.

### Module tree and depmod

- `/lib/modules/6.8.12-splitthp/` was populated by the install.
- `depmod` ran as part of the module installation flow.
- This confirms the module tree is now registered for the custom kernel release string.

### Boot artifacts

- The `/boot` install completed successfully.
- `update-initramfs` generated:
  - `/boot/initrd.img-6.8.12-splitthp`
- The `/boot/initrd.img` symlink now points to the `splitthp` initrd.
- The kernel image and boot metadata are now staged on disk for the new release.

### DKMS rebuild

- `DKMS` rebuilt `emulab-ipod-dkms` successfully for `6.8.12-splitthp`.
- This is important because it confirms at least one out-of-tree module package rebuilt cleanly against the custom kernel release.
- The DKMS result is another signal that the install environment is internally consistent enough to boot-test.

### GRUB update

- `zz-update-grub` ran successfully.
- It found both:
  - the custom `6.8.12-splitthp` kernel
  - the stock `6.8.0-101-generic` kernel
- The GRUB update finished cleanly, which means the boot menu now includes the custom kernel entry alongside the stock entry.

### Current machine state

- The machine is still running the stock kernel at the moment this log entry was written.
- A reboot is still required before the new kernel can be exercised.
- That means the kernel-side verification boundary has moved from install completion to controlled reboot planning.

### Why this matters

- The install stage is now complete enough that the remaining risk is not build/install failure, but runtime behavior after reboot.
- The stock kernel remains available as a fallback entry in GRUB.
- Because the machine has not been rebooted yet, the user still has a clean opportunity to save or push work before switching kernels.

## Boot Registration

### Kernel still running

- `uname -r` is still `6.8.0-101-generic` before reboot.
- This confirms the machine is still executing the stock kernel even though the custom kernel has been installed and registered.
- The boot work is therefore staged, not yet activated.

### `/boot` symlinks

- `/boot/vmlinuz` now points to the installed `6.8.12-splitthp` kernel image.
- `/boot/initrd.img` now points to the installed `6.8.12-splitthp` initrd.
- These symlinks are the default boot targets that GRUB and the initramfs tooling expect on the next boot.

### GRUB menu entry

- The top-level `Ubuntu` entry in `/boot/grub/grub.cfg` explicitly loads:
  - `/boot/vmlinuz-6.8.12-splitthp`
  - `/boot/initrd.img-6.8.12-splitthp`
- The same custom kernel/initrd pair is also used in the recovery submenu entries generated by GRUB.
- This is direct confirmation that the custom kernel is registered in the boot menu, not just installed on disk.

### Default boot selection

- `/etc/default/grub` has `GRUB_DEFAULT=0`.
- The generated GRUB config also sets the default to `0` for the menu entry.
- Therefore a plain reboot should select the custom kernel unless the user explicitly changes the boot entry interactively.

### Practical implication

- The custom kernel is installed, linked, and first in the default boot path.
- The stock kernel remains available as a fallback entry if the user needs it.
- The only remaining action needed to activate the new kernel is a controlled reboot.

## Post-Boot Runtime Verification

### Reboot result

- The machine rebooted successfully into the custom kernel:
  - `6.8.12-splitthp`
- This confirms the kernel install was not just staged on disk but actually activated at runtime.

### Boot artifact persistence

- The `/boot` symlinks still point to the splitthp kernel artifacts after reboot:
  - `/boot/vmlinuz` -> `vmlinuz-6.8.12-splitthp`
  - `/boot/initrd.img` -> `initrd.img-6.8.12-splitthp`
- That means the expected boot targets remained in place across the reboot boundary.

### Self-process syscall verification

- `tests/syscall_verification/self_split_verify` passed on the booted splitthp kernel.
- The test observed:
  - before split `AnonHugePages = 2048 kB`
  - after split `AnonHugePages = 0 kB`
- The process exited with status `0`.

### Cross-process syscall verification

- `tests/syscall_verification/cross_process_split_verify` passed on the booted splitthp kernel.
- The child process showed the same THP transition:
  - before split `AnonHugePages = 2048 kB`
  - after split `AnonHugePages = 0 kB`
- The parent and child both exited with status `0`.

### Interpretation

- This is the first live confirmation that the syscall works in both supported cases:
  - self split
  - same-user cross-process split
- The result validates the patched kernel path end-to-end, not just compile-time plumbing or preboot staging.
- The observed `AnonHugePages` drop to `0 kB` is the direct runtime evidence that the single THP containing the target virtual address was actually split on the booted custom kernel.
