# Kernel Install And Boot Checkpoint

## Summary

This task took the already-built patched Ubuntu kernel and completed the safe
install path. The custom kernel is now installed as `6.8.12-splitthp` with its
own module tree, initramfs, and GRUB entries. I stopped before rebooting so the
user can choose when to cross the live-kernel boundary.

## What Changed

- Installed the custom kernel and modules with:
  - `sudo make modules_install install`
- Populated:
  - `/lib/modules/6.8.12-splitthp/`
- Installed boot artifacts:
  - `/boot/vmlinuz-6.8.12-splitthp`
  - `/boot/initrd.img-6.8.12-splitthp`
- Updated symlinks:
  - `/boot/vmlinuz -> vmlinuz-6.8.12-splitthp`
  - `/boot/initrd.img -> initrd.img-6.8.12-splitthp`
- Triggered post-install hooks:
  - `depmod`
  - `DKMS`
  - `update-initramfs`
  - `update-grub`

## Why This Works

The install path is safe because it keeps the custom kernel distinct from the
stock Ubuntu kernel instead of trying to reuse the stock module tree.

Concrete evidence:

- the installed release string is clean:
  - `6.8.12-splitthp`
- the matching module tree exists:
  - `/lib/modules/6.8.12-splitthp/`
- `DKMS` rebuilt the out-of-tree `emulab-ipod-dkms` module successfully for
  that exact kernel release
- `update-initramfs` generated a matching initrd:
  - `/boot/initrd.img-6.8.12-splitthp`
- `update-grub` registered both:
  - the custom `6.8.12-splitthp` kernel
  - the stock `6.8.0-101-generic` kernel

GRUB now has a top-level `Ubuntu` entry that explicitly loads:

- `/boot/vmlinuz-6.8.12-splitthp`
- `/boot/initrd.img-6.8.12-splitthp`

Because `/etc/default/grub` has `GRUB_DEFAULT=0`, a normal reboot should boot
the custom kernel unless the user overrides it at the boot menu.

## Current Boundary

- The machine is still running:
  - `6.8.0-101-generic`
- The custom kernel is installed but not yet exercised.
- The next meaningful verification step is:
  - reboot into `6.8.12-splitthp`
  - run `tests/syscall_verification/self_split_verify`
  - run `tests/syscall_verification/cross_process_split_verify`

## Alternatives Considered

### Reuse the stock Ubuntu module tree

Rejected for the main path.

Pros:

- faster than building and installing a fully matching custom module tree

Cons:

- the stock modules advertise vermagic for `6.8.0-101-generic`
- our custom build identifies as `6.8.12-splitthp`
- that mismatch creates avoidable runtime uncertainty during the first boot

### Reboot immediately after install

Rejected for this checkpoint.

Pros:

- faster end-to-end validation

Cons:

- violates the user's explicit request to stop before reboot and refer back
- removes the user's chance to save or push current work first

## Pros And Cons

Pros:

- low-risk install path with a matching module tree
- stock kernel remains available in GRUB as a fallback
- custom kernel is already first in the GRUB ordering, so the next reboot path
  is simple

Cons:

- slower than the risky reuse-stock-modules approach
- runtime syscall validation still depends on a reboot
- `/boot/initrd.img` and `/boot/vmlinuz` symlinks now point to the custom
  kernel, so future boot assumptions should be made carefully
