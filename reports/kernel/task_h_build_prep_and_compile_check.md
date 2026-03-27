# Build Prep And Compile Check

## What I did

- Installed the missing kernel-build prerequisites:
  - `flex`
  - `bison`
  - `dwarves` / `pahole`
  - `libssl-dev`
  - `libelf-dev`
- Updated the copied Ubuntu kernel config to avoid local certificate-file build failures:
  - `CONFIG_SYSTEM_TRUSTED_KEYS=""`
  - `CONFIG_SYSTEM_REVOCATION_KEYS=""`
- Ran `make olddefconfig` successfully in:
  - `external/linux/ubuntu-6.8.0-101.101/`
- Started a broad `make -j$(nproc) bzImage` compile-check and let it run past the syscall table generation and the patched kernel objects.
- Captured the resulting generated-header and object-file evidence.

## Why this works

- Installing the missing build tools removes the trivial environment blockers that would otherwise make any compile result meaningless.
- Clearing the Canonical cert references is the normal local-build fix when building from a distro config outside the distro packaging environment.
- `make olddefconfig` refreshes the copied host config against the fetched source tree so generated headers and Kconfig state are coherent.
- The broad `bzImage` pass is enough to prove the syscall-table generation and patched translation units compile cleanly, even without waiting for every unrelated built-in driver to finish.

## Concrete compile evidence

- Generated x86_64 syscall header:
  - `arch/x86/include/generated/uapi/asm/unistd_64.h`
  - contains:
    - `#define __NR_split_thp 462`
    - `#define __NR_syscalls 463`
- Generated syscall implementation header:
  - `arch/x86/include/generated/asm/syscalls_64.h`
  - was produced successfully during the build phase
- Patched objects compiled successfully:
  - `arch/x86/entry/syscall_64.o`
  - `mm/huge_memory.o`

That means the new syscall number, prototype, and implementation all made it through the compiler and generated-header pipeline.

## Important limitation

- I intentionally stopped the full `bzImage` build after the meaningful patch-specific compile gates had passed.
- The remaining build work at that point was largely unrelated driver churn and not adding much signal about the syscall patch itself.
- So this checkpoint is:
  - a successful patch-focused compile check
  - not a completed full-kernel image build

## Non-patch warnings observed

The broad compile produced warnings in unrelated files, including:

- `security/apparmor/file.c`
- `security/security.c`
- `drivers/char/random.c`
- `drivers/cpufreq/cpufreq.c`

These were not caused by the new syscall patch.

## Alternatives considered

- Finish the entire `bzImage` build before stopping.
  - Rejected for this checkpoint because the syscall patch had already cleared the meaningful compile gates, and the remaining time was mostly being spent on unrelated built-in drivers.
- Skip the broad `bzImage` pass and only compile one object directly.
  - Rejected because we wanted generated syscall headers and actual integration into the normal x86 build pipeline, not just a narrow object compile.

## Pros

- Confirms the syscall number was generated into x86 headers correctly.
- Confirms both patched implementation objects compile.
- Removes build-environment blockers for later work.
- Keeps the checkpoint focused on the syscall patch rather than unrelated kernel packaging steps.

## Cons

- Not a full successful image build yet.
- Build warnings elsewhere remain in the tree, even though they are unrelated to this patch.
- We still need user-space verifier work before runtime validation is possible.
