# SETUP

This is the canonical machine-setup and syscall bring-up guide for this repo.
If you are starting on a fresh Ubuntu or CloudLab machine and want to reproduce
the custom `split_thp(pid, vaddr)` kernel workflow from scratch, follow this
document top to bottom.

## 1. Clone And Bootstrap The Repo

```bash
git clone https://github.com/SahilSC/HugePageResearch.git
cd HugePageResearch
source scripts/setup_prep_env.sh
```

This installs Docker and `uv`, creates `.venv`, and builds the Docker image.

## 2. Enter The Docker Container And Install Benchmarks

```bash
make docker
make install-ycsb
make setup-redis
```

## 3. Disable ASLR On The Host

Run this on the host machine, not inside the container:

```bash
sudo sysctl -w kernel.randomize_va_space=0
echo "kernel.randomize_va_space = 0" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

## 4. Install Host Dependencies For The Syscall Build

```bash
sudo apt-get update
sudo apt-get install -y \
  gh \
  python3-bpfcc \
  bpfcc-tools \
  python3.12-venv \
  build-essential \
  bc \
  cpio \
  flex \
  bison \
  dwarves \
  libssl-dev \
  libelf-dev
```

Make sure the repo virtual environment can see the system-installed BCC
bindings:

```bash
perl -0pi -e 's/include-system-site-packages = false/include-system-site-packages = true/' .venv/pyvenv.cfg
.venv/bin/python -c "import bcc, polars, osquery; print('python deps ok')"
```

If you want to manage GitHub PRs from this machine:

```bash
gh auth login
gh auth status
```

## 5. Recreate The Matching Ubuntu Kernel Source Tree

The syscall patch targets Ubuntu `6.8.0-101.101`.

```bash
cd ~/HugePageResearch
mkdir -p external/linux
cd external/linux
apt download linux-source-6.8.0=6.8.0-101.101
dpkg-deb -x linux-source-6.8.0_6.8.0-101.101_all.deb pkg
tar -xf pkg/usr/src/linux-source-6.8.0.tar.bz2
mv linux-source-6.8.0 ubuntu-6.8.0-101.101
```

## 6. Apply The Tracked Syscall Patch

```bash
cd ~/HugePageResearch/external/linux/ubuntu-6.8.0-101.101
patch -p1 < ~/HugePageResearch/kernel-patches/ubuntu-6.8.0-101.101-split_thp.patch
```

This patch file is the repo-visible kernel delta for the syscall work. After it
applies cleanly, the actual kernel implementation lives in:

- `arch/x86/entry/syscalls/syscall_64.tbl`
- `include/linux/syscalls.h`
- `mm/huge_memory.c`

## 7. Prepare The Kernel Config

```bash
cd ~/HugePageResearch/external/linux/ubuntu-6.8.0-101.101
cp /boot/config-6.8.0-101-generic .config
scripts/config --set-str LOCALVERSION "-splitthp"
scripts/config --set-str SYSTEM_TRUSTED_KEYS ""
scripts/config --set-str SYSTEM_REVOCATION_KEYS ""
make olddefconfig
grep CONFIG_LOCALVERSION .config
```

The last command should print:

```text
CONFIG_LOCALVERSION="-splitthp"
```

## 8. Build And Install The Custom Kernel

```bash
cd ~/HugePageResearch/external/linux/ubuntu-6.8.0-101.101
make -j"$(nproc)" bzImage modules
sudo make modules_install install
```

This should install:

- `/boot/vmlinuz-6.8.12-splitthp`
- `/boot/initrd.img-6.8.12-splitthp`
- `/lib/modules/6.8.12-splitthp/`

## 9. Reboot Into The Custom Kernel

Use a one-shot GRUB reboot so you explicitly select the custom kernel:

```bash
sudo grub-reboot "Advanced options for Ubuntu>Ubuntu, with Linux 6.8.12-splitthp"
sudo reboot
```

After the machine comes back:

```bash
uname -r
ls -l /boot/vmlinuz /boot/initrd.img /lib/modules/6.8.12-splitthp
```

`uname -r` should report:

```text
6.8.12-splitthp
```

## 10. Build And Run The Syscall Verifiers

```bash
cd ~/HugePageResearch/tests/syscall_verification
make
./self_split_verify
./cross_process_split_verify
```

Expected results on the patched kernel:

- `self_split_verify: PASS`
- `cross_process_split_verify: PASS`

Both verifiers should show:

- `anon_huge_pages_kb=2048` before the syscall
- `anon_huge_pages_kb=0` after the split

## 11. Optional: Boot Back Into The Stock Kernel

If you want to compare behavior against the stock Ubuntu kernel:

```bash
sudo grub-reboot "Advanced options for Ubuntu>Ubuntu, with Linux 6.8.0-101-generic"
sudo reboot
```

## 12. Redis / VAPTR Smoke Path

Once the custom kernel is running, the repo-visible syscall bundle is:

- `kernel-patches/ubuntu-6.8.0-101.101-split_thp.patch`
- `tests/syscall_verification/`
- `reports/kernel/`

For a Redis-targeted smoke test after bring-up, use:

- `tests/syscall_verification/README.md`
- `config/redis_vaptr_e2e.yaml`
- `config/redis_vaptr_access_bit_e2e.yaml`
