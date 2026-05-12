# SETUP

This is the canonical machine setup and experiment runbook for this repo.

Follow the sections in this order:

1. Complete Section 1 once on a new machine.
2. Use Section 2 for generic memory-bloat or trace-data collection.
3. Use Section 3 for Redis trace capture and replay.
4. Use Section 4 only when you need the custom `split_thp(pid, vaddr)` kernel.

## 1. Generic Machine Setup

The commands below assume the repo lives at `~/HugePageResearch`. If your clone
is elsewhere, replace that path consistently.

### 1.1 Clone And Bootstrap

```bash
git clone https://github.com/SahilSC/HugePageResearch.git
cd HugePageResearch
source scripts/setup_prep_env.sh
```

This installs Docker and `uv`, creates `.venv`, and builds the Docker image.

### 1.2 Install Benchmarks And Redis Module

If you already have a working repo container, reuse it. Otherwise start one:

```bash
make docker
```

Inside the container:

```bash
make install-ycsb
make setup-redis
make -C redis-module
```

On the host, install the repo-managed GUPS benchmark:

```bash
cd ~/HugePageResearch
scripts/setup-benchmarks/setup-gups.sh
```

### 1.3 Install Host Packages

Run this on the host:

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
  libelf-dev \
  linux-headers-$(uname -r)
```

### 1.4 Let The Repo Venv See System BCC

```bash
perl -0pi -e 's/include-system-site-packages = false/include-system-site-packages = true/' .venv/pyvenv.cfg
.venv/bin/python -c "import bcc, polars, osquery; print('python deps ok')"
```

### 1.4.1 Bootstrap `.venv` In This Checkout If It Is Missing

1. `cd ~/HugePageResearch-gups-harness`
2. `uv run python -c "import polars, matplotlib, redis"`

### 1.5 Fix The Benchmark Path On Non-Standard Home Directories

The benchmark installers and some scripts still expect
`/home/$USER/kernmlops-benchmark`.

```bash
if [ "$HOME" != "/home/$USER" ]; then
  sudo mkdir -p "/home/$USER"
  sudo ln -sfn "$HOME/kernmlops-benchmark" "/home/$USER/kernmlops-benchmark"
fi
```

### 1.6 Optional GitHub CLI Auth

```bash
gh auth login
gh auth status
```

### 1.7 Optional LaTeX Editing In VS Code

1. Update package indexes on the host:

   ```bash
   sudo apt-get update
   ```

2. Install the LaTeX build tools and common TeX packages:

   ```bash
   sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
     latexmk \
     texlive-latex-extra \
     texlive-fonts-recommended \
     texlive-bibtex-extra \
     texlive-xetex \
     biber \
     chktex
   ```

3. Install the VS Code extension:

   ```bash
   code --install-extension James-Yu.latex-workshop
   ```

## 2. Memory Bloat And Trace-Data Collection

Use this section when you want RSS or THP-bloat data without replay-time THP
splitting. The safest starting point is the `*_compat.yaml` configs because
they avoid the 6.x-only unmap hooks and still capture `mm_rss_stat`.

### 2.1 Preferred Container Flow

Enter the repo container if you are not already inside it:

```bash
make docker
```

Run the baseline with THP disabled:

```bash
cd /KernMLOps
.venv/bin/python python/kernmlops collect -v \
  -c config/redis_never_compat.yaml \
  --benchmark redis
```

Run the comparison with THP forced on:

```bash
cd /KernMLOps
.venv/bin/python python/kernmlops collect -v \
  -c config/redis_always_compat.yaml \
  --benchmark redis
```

Optional `madvise` run:

```bash
cd /KernMLOps
.venv/bin/python python/kernmlops collect -v \
  -c config/redis_madvise_compat.yaml \
  --benchmark redis
```

Each run prints a collection id and writes parquet files under
`data/curated/redis/<collection-id>/`.

### 2.2 Host-Native Flow

If you are not using the container, run the same commands from the repo root
with the host environment preserved through `sudo`:

```bash
cd ~/HugePageResearch
sudo -E HOME=$HOME UNAME=$USER GID=$(id -g) PATH="$PATH" \
  .venv/bin/python python/kernmlops collect -v \
  -c config/redis_never_compat.yaml \
  --benchmark redis
```

Repeat that command with `config/redis_always_compat.yaml` and then optionally
`config/redis_madvise_compat.yaml`.

### 2.3 Compare The Runs

From the repo root, replace the two example collection ids and run:

```bash
.venv/bin/python - <<'PY'
import os
import sys

import polars as pl

sys.path.insert(0, os.path.abspath("python/kernmlops"))
from analysis.bloat import clean_rss_pid

run_never = "REPLACE_WITH_NEVER_COLLECTION_ID"
run_always = "REPLACE_WITH_ALWAYS_COLLECTION_ID"

runs = {"never": run_never, "always": run_always}
results = {}

print("--- BLOAT ANALYSIS ---")
for label, cid in runs.items():
    base_dir = f"data/curated/redis/{cid}"
    rss_df = pl.read_parquet(f"{base_dir}/mm_rss_stat.end.parquet")

    tgid_counts = (
        rss_df.filter(pl.col("tgid") > 0)
        .group_by("tgid")
        .len()
        .sort("len", descending=True)
    )
    main_pid = tgid_counts[0, "tgid"]

    timeline = clean_rss_pid(rss_df, main_pid)
    avg_rss_mb = (timeline["count"] * 4 / 1024).mean()
    print(f"{label} ({cid[:8]}...): Avg RSS = {avg_rss_mb:.2f} MB")
    results[label] = avg_rss_mb

bloat_mb = results["always"] - results["never"]
bloat_pct = (bloat_mb / results["never"]) * 100
print(f"Estimated Bloat: {bloat_mb:+.2f} MB ({bloat_pct:+.1f}%)")
PY
```

The helper functions for this calculation live in
`python/kernmlops/analysis/bloat.py`.

## 3. Redis Trace Capture And Replay

Use this section when you want one preserved `snapshot.rdb`, one preserved
`monitor_run.log`, and replay-time breakpoint experiments.

The canonical replay entrypoints are:

- `python/kernmlops/replay/capture_redis_trace.sh`
- `python/kernmlops/replay/generate_breakpoints.py`
- `python/kernmlops/replay/replay_trace.py`

`replay.md` is the runbook for the current CLI flags and replay semantics.

### 3.1 Host Prep

Build the Redis module on the repo checkout you will use:

```bash
cd ~/HugePageResearch
make -C redis-module
```

If you need stable pointer layouts for manual VAPTR verification on the host,
disable ASLR on the host:

```bash
sudo sysctl -w kernel.randomize_va_space=0
echo "kernel.randomize_va_space = 0" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

### 3.2 Capture Inside The Repo Container

If you already have a working repo container, reuse it. Otherwise:

```bash
make docker
```

Inside the container:

```bash
cd /KernMLOps
redis-server ./config/redis.conf --loadmodule ./redis-module/vaptr.so --daemonize yes
redis-cli ping
./python/kernmlops/replay/capture_redis_trace.sh
```

Expected artifacts:

- `data/redis_traces/snapshot.rdb`
- `data/redis_traces/monitor_run.log`

### 3.3 Generate Breakpoints

```bash
cd /KernMLOps
.venv/bin/python python/kernmlops/replay/generate_breakpoints.py \
  data/redis_traces/monitor_run.log \
  --output data/breakpoints.parquet
```

### 3.4 Replay

```bash
cd /KernMLOps
.venv/bin/python python/kernmlops/replay/replay_trace.py \
  data/redis_traces/snapshot.rdb \
  data/redis_traces/monitor_run.log \
  --breakpoints data/breakpoints.parquet \
  --output data/results.parquet \
  --runs 1 \
  -v
```

### 3.5 Manual Redis And VAPTR Verification

If the distro Redis cannot load `redis-module/vaptr.so`, use the validated
Redis `7.4.2` build directly:

```bash
cd ~/HugePageResearch
REDIS_BIN="/tmp/redis-7.4.2/src/redis-server"
"$REDIS_BIN" ./config/redis.conf \
  --loadmodule "$(pwd)/redis-module/vaptr.so"
```

Then verify the module:

```bash
redis-cli MODULE LIST
```

The output should include `vaptr`.

## 4. Custom `split_thp(pid, vaddr)` Kernel Workflow

Use this section only when you need the patched kernel syscall path. The target
kernel source tree is Ubuntu `6.8.0-101.101`.

### 4.1 Recreate The Kernel Source Tree

```bash
cd ~/HugePageResearch
mkdir -p external/linux
cd external/linux
apt download linux-source-6.8.0=6.8.0-101.101
dpkg-deb -x linux-source-6.8.0_6.8.0-101.101_all.deb pkg
tar -xf pkg/usr/src/linux-source-6.8.0.tar.bz2
mv linux-source-6.8.0 ubuntu-6.8.0-101.101
```

### 4.2 Apply The Tracked Patch

```bash
cd ~/HugePageResearch/external/linux/ubuntu-6.8.0-101.101
patch -p1 < ~/HugePageResearch/kernel-patches/ubuntu-6.8.0-101.101-split_thp.patch
```

### 4.3 Prepare The Kernel Config

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

### 4.4 Build And Install The Kernel

```bash
cd ~/HugePageResearch/external/linux/ubuntu-6.8.0-101.101
make -j"$(nproc)" bzImage modules
sudo make modules_install install
```

### 4.5 Reboot Into The Custom Kernel

```bash
sudo grub-reboot "Advanced options for Ubuntu>Ubuntu, with Linux 6.8.12-splitthp"
sudo reboot
```

After reboot:

```bash
uname -r
ls -l /boot/vmlinuz /boot/initrd.img /lib/modules/6.8.12-splitthp
```

`uname -r` should report:

```text
6.8.12-splitthp
```

### 4.6 Build And Run The Syscall Verifiers

```bash
cd ~/HugePageResearch/tests/syscall_verification
make
./self_split_verify
./cross_process_split_verify
```

Expected results:

- `self_split_verify: PASS`
- `cross_process_split_verify: PASS`

Both verifiers should show:

- `anon_huge_pages_kb=2048` before the syscall
- `anon_huge_pages_kb=0` after the split

### 4.7 Optional: Boot Back Into The Stock Kernel

```bash
sudo grub-reboot "Advanced options for Ubuntu>Ubuntu, with Linux 6.8.0-101-generic"
sudo reboot
```
