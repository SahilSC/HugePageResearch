# Measuring Memory Bloat with KernMLOps

## Table of Contents

- [Background \& Motivation](#background--motivation)
- [What Is Memory Bloat?](#what-is-memory-bloat)
- [Architecture Overview](#architecture-overview)
- [Prerequisites](#prerequisites)
- [Full Setup — Native (No Docker)](#full-setup--native-no-docker)
- [Full Setup — Docker (Recommended for Production)](#full-setup--docker-recommended-for-production)
- [Running a Memory Bloat Measurement (Redis Example)](#running-a-memory-bloat-measurement-redis-example)
- [Analyzing the Data (Calculating Bloat)](#analyzing-the-data-calculating-bloat)
- [Understanding the Configuration Files](#understanding-the-configuration-files)
- [Understanding the BPF Hooks](#understanding-the-bpf-hooks)
- [Key Files and Commands Reference](#key-files-and-commands-reference)
- [Troubleshooting](#troubleshooting)

---

## Background & Motivation

Modern memory allocators (jemalloc, tcmalloc, glibc malloc) and the Linux kernel's **Transparent Huge Pages (THP)** subsystem introduce a trade-off:

- **Performance**: Huge pages (2 MB) reduce TLB misses and page table overhead, dramatically improving performance for memory-intensive workloads.
- **Bloat**: A single huge page fault allocates the entire 2 MB regardless of how much is actually used. If a program only touches a few kilobytes within that 2 MB range, the rest is *bloat* — physical memory that is allocated but unused.

> **The core research question**: When should the kernel give a process a huge page, and when should it take one back? How much bloat does a given THP policy (`always`, `madvise`, `never`) create for real workloads like Redis?

Calculating bloat is inherently difficult:
- Zeroed memory could mean "allocated but unused" or "application called `madvise(MADV_DONTNEED)`"
- RSS (Resident Set Size) as reported by `/proc` includes all physically-resident pages, even if they contain only zeros
- **Bloat = True RSS − Useful RSS**, but "Useful RSS" requires kernel-level instrumentation to track

This repository provides the tools to instrument the kernel with eBPF probes, run benchmarks against programs like Redis, and collect fine-grained memory usage data to compute and analyze memory bloat.

---

## What Is Memory Bloat?

```
Bloat = RSS (physical memory allocated) − Actual Useful Memory
```

In practice, we estimate bloat by comparing the RSS usage of a workload under different THP policies:

1.  **Baseline (`never`)**: Linux uses only 4KB pages. This represents the "tightest" possible packing of memory (lowest internal fragmentation), but highest page table overhead.
2.  **Comparison (`always` or `madvise`)**: Linux uses 2MB huge pages.

```
Bloat ≈ RSS(always) - RSS(never)
```

If `RSS(always)` is significantly higher than `RSS(never)`, the difference is "bloat" — memory consumed by huge pages that wasn't strictly necessary for the data.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Your Host Machine                        │
│   (or Docker Container if using the Docker workflow)         │
│                                                              │
│  ┌──────────────┐   ┌─────────────────────────────┐         │
│  │ Redis Server │   │ KernMLOps Data Collection   │         │
│  │ + YCSB Load  │   │                             │         │
│  │  Generator   │   │  • BPF Hooks (eBPF probes)  │         │
│  │              │   │  • Perf Counters            │         │
│  │              │   │  • /proc/meminfo polling    │         │
│  └──────────────┘   └──────────┬──────────────────┘         │
│                               │                              │
│                               ▼                              │
│                    data/curated/<benchmark>/                  │
│                      <collection-id>/                        │
│                        *.parquet files                       │
│                               │                              │
│                               ▼                              │
│           Python Analysis (polars + plotnine/matplotlib)      │
│              python/kernmlops/analysis/bloat.py               │
└──────────────────────────────────────────────────────────────┘
```

The system works by:
1. **Inserting eBPF probes** into the running kernel to trace memory events (RSS changes, madvise calls, huge page collapses, page unmaps)
2. **Running a benchmark** (e.g., Redis + YCSB) that exercises the program under test
3. **Collecting data** as Parquet files for offline analysis
4. **Analyzing** the data with Python (polars DataFrames) to compute bloat metrics

---

## Prerequisites

| Requirement | Purpose |
|---|---|
| **Ubuntu 22.04 or 24.04** | Tested OS versions |
| **Python ≥ 3.12** | Required by the project (`requires-python = ">=3.12"`) |
| **Root / sudo access** | Required for BPF probe insertion and THP control |
| **Linux kernel headers** | Needed for BPF compilation |
| **`python3-bpfcc`** (apt) | BCC Python bindings for eBPF |
| **Java 11+** | Required for YCSB benchmark runner |
| **Maven** | Required for building YCSB from source |
| **Docker** *(optional)* | Container environment (recommended for clean setup) |

> **Kernel version note**: The `madvise`, `collapse_huge_pages`, and `unmap_range` BPF hooks require
> **kernel 6.x+** (they attach to `do_vmi_align_munmap` which doesn't exist on 5.15). On older kernels,
> use only the `mm_rss_stat`, `process_trace`, and `perf` hooks.

---

## Full Setup — Native (No Docker)

This was tested end-to-end on **Ubuntu 22.04 with kernel 5.15**. If you have Docker, skip to the Docker section below.

### Step 1: Clone the Repository

```bash
git clone https://github.com/<org>/KernMLOps.git
cd KernMLOps
```

> ⚠️ **Do NOT clone submodules** — that would clone the entire Linux kernel source.

### Step 2: Install System Dependencies

```bash
# Python 3.12 (Ubuntu 22.04 needs the deadsnakes PPA)
sudo apt-get install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update -y
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev

# BCC (eBPF tooling), Java, Maven, Redis
sudo apt-get install -y python3-bpfcc openjdk-11-jre-headless maven redis-server

# Kernel headers (for BPF compilation)
sudo apt-get install -y linux-headers-$(uname -r)
```

> **Ubuntu 24.04**: Python 3.12 is the default, so skip the deadsnakes PPA steps.

### Step 3: Create a Python 3.12 Virtual Environment

The `--system-site-packages` flag is **required** so the venv can access the system-installed `bcc` module (BCC cannot be installed via pip):

```bash
python3.12 -m venv .venv --system-site-packages
source .venv/bin/activate
```

### Step 4: Install Python Dependencies

```bash
# Install setuptools first (needed by thrift/osquery build)
pip install --force-reinstall setuptools

# Install thrift with PEP517 build (the default setup.py build may fail)
pip install --use-pep517 thrift

# Install all project dependencies
pip install \
  polars-lts-cpu click click-default-group matplotlib plotext \
  psutil typing_extensions pytimeparse pexpect plotnine pyarrow \
  scipy pymongo osquery

# Fix potential PIL/kiwisolver conflicts (system Python 3.10 packages may conflict)
pip install --force-reinstall --no-cache-dir Pillow kiwisolver
```

### Step 5: Verify the Python Environment

```bash
source .venv/bin/activate
python -c "
from bcc import BPF; print('✓ BCC')
import polars; print('✓ Polars')
from matplotlib import pyplot; print('✓ Matplotlib')
import click; print('✓ Click')
import osquery; print('✓ OSQuery')
"
```

All five should print ✓.

### Step 6: Install YCSB (Benchmark Load Generator)

```bash
# Copy Maven settings
sudo cp scripts/settings.xml /etc/maven/settings.xml

# Run the YCSB install script
bash scripts/setup-benchmarks/install-ycsb.sh
```

**⚠️ Known issue**: The install script may fail to copy `ycsb_runner.py` to the YCSB bin directory. Fix manually:

```bash
# Verify YCSB was cloned and built
ls ~/kernmlops-benchmark/ycsb/YCSB/bin/

# If the YCSB was cloned to the wrong directory (e.g., ./YCSB/ instead of ~/kernmlops-benchmark/ycsb/YCSB/):
mkdir -p ~/kernmlops-benchmark/ycsb
cp -r ./YCSB ~/kernmlops-benchmark/ycsb/YCSB  # Only if needed

# Install the custom YCSB runner script
cp scripts/setup-benchmarks/ycsb_runner.py ~/kernmlops-benchmark/ycsb/YCSB/bin/ycsb
chmod +x ~/kernmlops-benchmark/ycsb/YCSB/bin/ycsb
```

### Step 7: Fix Home Directory Path (if non-standard)

The code uses `UNAME` env var to compute benchmark paths as `/home/$UNAME/kernmlops-benchmark`. If your home directory isn't under `/home/` (e.g., `/users/myuser`), create a symlink:

```bash
# Only needed if your $HOME is NOT /home/$USER
sudo mkdir -p /home/$USER
sudo ln -sf ~/kernmlops-benchmark /home/$USER/kernmlops-benchmark
```

---

## Full Setup — Docker (Recommended for Production)

### Step 1: Clone and Run the Setup Script

```bash
git clone https://github.com/<org>/KernMLOps.git
cd KernMLOps
source scripts/setup_prep_env.sh
```

This installs Docker, uv, creates a venv, and builds the Docker image. **Expected time: ~10 minutes.**

### Step 2: Enter the Docker Container

```bash
make docker
```

This launches a **privileged** Docker container with kernel headers, modules, and `/sys/kernel/` mounted.

### Step 3: Install YCSB (Inside the Container)

```bash
make install-ycsb
```

### Step 4: Setup Redis (Inside the Container)

```bash
make setup-redis
```

### Step 5: Disable ASLR

sudo sysctl -w kernel.randomize_va_space=0
echo "kernel.randomize_va_space = 0" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
---

## Running a Memory Bloat Measurement (Redis Example)

To accurately measure bloat, we need to run the same benchmark under different THP policies and compare the results.

### 1. Run Baseline (THP=never)

This forces the kernel to use 4KB pages.

```bash
# Activate the venv
source .venv/bin/activate

# Use a config that sets transparent_hugepages: never
sudo -E HOME=$HOME UNAME=$USER GID=$(id -g) PATH="$PATH" \
  .venv/bin/python python/kernmlops collect -v \
  -c config/redis_never_compat.yaml \
  --benchmark redis
```

> Record the **Collection ID** from the output (e.g., `4e982d62...`).

### 2. Run Comparison (THP=always)

This forces the kernel to try using huge pages (2MB) whenever possible.

```bash
# Use a config that sets transparent_hugepages: always
python python/kernmlops collect -v \
  -c config/redis_always_compat.yaml \
  --benchmark redis
```

> Record the **Collection ID** (e.g., `90b45e53...`).

### (Optional) 3. Run Advisory (THP=madvise)

This uses huge pages only for memory regions explicitly marked with `madvise(MADV_HUGEPAGE)`.

```bash
sudo -E HOME=$HOME UNAME=$USER GID=$(id -g) PATH="$PATH" \
  .venv/bin/python python/kernmlops collect -v \
  -c config/redis_madvise_compat.yaml \
  --benchmark redis
```

---

## Analyzing the Data (Calculating Bloat)

Once you have the collection IDs, you can calculate the bloat.

### Create Analysis Script (`measure_bloat.py`)

Save the following as `measure_bloat.py`:

```python
import polars as pl
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath("python/kernmlops"))
from analysis.bloat import clean_rss_pid

# REPLACE WITH YOUR COLLECTION IDs
run_never  = "4e982d62-1b59-4559-9926-5fe610b503c1"  # Baseline
run_always = "90b45e53-eafa-44f6-a976-c9c74f9c7877"  # Comparison

runs = {"never": run_never, "always": run_always}
results = {}

print("--- BLOAT ANALYSIS ---")
for label, cid in runs.items():
    base_dir = f"data/curated/redis/{cid}"
    rss_df = pl.read_parquet(f"{base_dir}/mm_rss_stat.end.parquet")
    
    # Identify redis-server PID (tgid with most RSS events > 0)
    tgid_counts = rss_df.filter(pl.col("tgid") > 0).group_by("tgid").len().sort("len", descending=True)
    main_pid = tgid_counts[0, "tgid"]
    
    # Get RSS timeline
    timeline = clean_rss_pid(rss_df, main_pid)
    avg_rss = (timeline["count"] * 4 / 1024).mean() # Convert 4KB pages to MB
    
    print(f"{label} (ID: {cid[:8]}...): Avg RSS = {avg_rss:.2f} MB")
    results[label] = avg_rss

bloat_mb = results["always"] - results["never"]
bloat_pct = (bloat_mb / results["never"]) * 100
print(f"\nEstimated Bloat: {bloat_mb:+.2f} MB ({bloat_pct:+.1f}%)")
```

### Run Analysis

```bash
source .venv/bin/activate
python measure_bloat.py
```

**Example Output:**
```
--- BLOAT ANALYSIS ---
never (ID: 4e982d62...): Avg RSS = 339.98 MB
always (ID: 90b45e53...): Avg RSS = 326.81 MB

Estimated Bloat: -13.17 MB (-3.9%)
```

> **Interpretation**: A negative bloat number means THP actually *saved* memory (likely due to reduced page table overhead). Use a workload with sparse access patterns (random writes to large arrays) to induce positive bloat.

Can also do 
python python/kernmlops/analysis/compare_maps.py \
  data/curated/redis/20260303T185825368648 \
  data/curated/redis/20260303T185703009111 \
  --percentages 50 100

---

## Understanding the Configuration Files

### `defaults.yaml` — All Default Options

Generated via `make defaults`. Shows every configurable option.

### `config/redis_never.yaml` — Example (Annotated)

```yaml
benchmark_config:
  generic:
    benchmark: redis                # Which benchmark to run (redis|faux|gap|mongodb|memcached|linux_build)
    cpus: 0                         # 0 = use all CPUs
    skip_clear_page_cache: false    # Clear page cache before benchmark
    transparent_hugepages: never    # THP policy: always | madvise | never | no_change
    overcommit_memory: never_check  # VM overcommit setting
  redis:
    request_distribution: "zipfian" # Zipfian = some keys are much hotter than others

collector_config:
  generic:
    poll_rate: 0.1                  # Poll BPF buffers every 100ms
    output_dir: data                # Output directory for Parquet files
    hooks:                          # BPF hooks to enable
      - mm_rss_stat                 # Track RSS changes (REQUIRED for bloat measurement)
```

## Understanding the BPF Hooks

### `mm_rss_stat` — RSS Change Tracking ⭐ (Core for Bloat Measurement)

**Files:** `bpf_instrumentation/mm_rss_stat.py`, `bpf/mm_trace_rss_stat.bpf.c`

Attaches to the kernel's `rss_stat` tracepoint. Every time the kernel updates a process's RSS counter, this hook records:

| Field | Description |
|---|---|
| `pid` | Thread ID |
| `tgid` | Process ID (thread group leader) |
| `ts_ns` | Timestamp in nanoseconds (boot time) |
| `member` | RSS category: `MM_FILEPAGES`, `MM_ANONPAGES`, `MM_SWAPENTS`, `MM_SHMEMPAGES` |
| `count` | New page count (in 4 KB pages) |

**This is the primary data source for bloat measurement.** It captures every RSS change for every process on the system.

### `process_trace` — Process Lifecycle Tracking

**Files:** `bpf_instrumentation/fork_and_exit.py`, `bpf/fork_and_exit.bpf.c`

Tracks `fork`, `exec`, and `exit` events. Used to:
- Map PIDs to process names (e.g., which PID is `redis-server`)
- Time-bound the analysis to the benchmark window

---

## Key Files and Commands Reference

### Commands

| Command | Context | Description |
|---|---|---|
| `source scripts/setup_prep_env.sh` | Host | Full Docker installation (Docker, uv, venv, Docker image) |
| `make docker` | Host | Enter the privileged Docker container |
| `make install-ycsb` | Container | Install YCSB benchmark suite |
| `make setup-redis` | Container | Setup Redis data directory and config |
| `make collect` | Host | Run data collection using `overrides.yaml` |
| `sudo ... collect -v -c <config>` | Host (native) | Direct invocation for native runs |

### Key Files

| File | Description |
|---|---|
| `Makefile` | Top-level build/run orchestration |
| `config/redis_never.yaml` | Redis + THP=never config |
| `python/kernmlops/cli/collect.py` | Core collection logic |
| `python/kernmlops/analysis/bloat.py` | Core bloat analysis (RSS cleaning, graphing) |
| `measure_bloat.py` | Script to calculate bloat from multiple runs |

---

## Troubleshooting

### `BenchmarkNotConfiguredError: benchmark redis is not configured`

YCSB is not found at the expected location. The code looks for it at `~/kernmlops-benchmark/ycsb/`. Fix:

```bash
mkdir -p ~/kernmlops-benchmark/ycsb
cp -r ./YCSB ~/kernmlops-benchmark/ycsb/YCSB
```

### `Failed to attach BPF program to kprobe do_vmi_align_munmap`

Your kernel (likely 5.15) doesn't have `do_vmi_align_munmap` (introduced in kernel 6.x). Remove the `madvise` hook from your config.

### `FATAL CONFIG FILE ERROR: 'set-proc-title yes'`

The `config/redis.conf` uses Redis 7.x directives. On Redis 6.x, comment out `set-proc-title`.

### `ImportError: cannot import name '_imaging' from 'PIL'`

PIL/Pillow conflict between system Python and venv. Fix:
`pip install --force-reinstall --no-cache-dir Pillow kiwisolver`
