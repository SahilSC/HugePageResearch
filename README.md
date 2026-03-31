# KernMLOps

This repository serves as the mono-repo for the KernMLOps research project.

Currently, it only contains scripts for data collection of kernel performance.

See CONTRIBUTING.md for an overview of how to expand this tool.

WARNING: Do not clone submodules, this will clone the linux kernel.
Cloning the kernel is prohibitively expensive.
That submodule is only necessary if you plan to use in-kernel inference.

## CloudLab / Production Setup

If you are starting from a fresh CloudLab or Ubuntu machine and want the
shortest path to a working Redis benchmark environment, use this flow first.

### Step 1: Clone and Run the Setup Script

```bash
git clone https://github.com/SahilSC/HugePageResearch.git
cd HugePageResearch
source scripts/setup_prep_env.sh
```

This installs Docker and `uv`, creates `.venv`, and builds the Docker image.
Expected time: about 10 minutes on a fresh Ubuntu machine.

### Step 2: Enter the Docker Container

```bash
make docker
```

### Step 3: Install YCSB Inside the Container

```bash
make install-ycsb
```

### Step 4: Setup Redis Inside the Container

```bash
make setup-redis
```

### Step 5: Disable ASLR on the Host

```bash
sudo sysctl -w kernel.randomize_va_space=0
echo "kernel.randomize_va_space = 0" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

## Redis Memory-Bloat Example

If you are running collection on the host, activate the repo environment first:

```bash
source .venv/bin/activate
```

Baseline run with THP disabled:

```bash
sudo -E HOME=$HOME UNAME=$USER GID=$(id -g) PATH="$PATH" \
  .venv/bin/python python/kernmlops collect -v \
  -c config/redis_never_compat.yaml \
  --benchmark redis
```

Comparison run with THP forced on:

```bash
sudo -E HOME=$HOME UNAME=$USER GID=$(id -g) PATH="$PATH" \
  .venv/bin/python python/kernmlops collect -v \
  -c config/redis_always_compat.yaml \
  --benchmark redis
```

Optional VAPTR / access-bit run:

```bash
sudo -E HOME=$HOME UNAME=$USER GID=$(id -g) PATH="$PATH" \
  .venv/bin/python python/kernmlops collect -v \
  -c config/redis_vaptr_access_bit_e2e.yaml \
  --benchmark redis
```

## Custom Kernel Setup for `split_thp`

Use the host machine for kernel work, not the Docker container. The syscall
research in this repo targets the Ubuntu `6.8.0-101.101` source tree, and the
custom installed kernel release string is `6.8.12-splitthp`.

Install the kernel build prerequisites:

```bash
sudo apt-get update
sudo apt-get install -y build-essential bc cpio flex bison dwarves libssl-dev libelf-dev
```

Recreate the matching Ubuntu source tree if it is missing:

```bash
cd ~/HugePageResearch
mkdir -p external/linux
cd external/linux
apt download linux-source-6.8.0=6.8.0-101.101
dpkg-deb -x linux-source-6.8.0_6.8.0-101.101_all.deb pkg
tar -xf pkg/usr/src/linux-source-6.8.0.tar.bz2
mv linux-source-6.8.0 ubuntu-6.8.0-101.101
```

Prepare the kernel config:

```bash
cd ~/HugePageResearch/external/linux/ubuntu-6.8.0-101.101
cp /boot/config-6.8.0-101-generic .config
scripts/config --set-str LOCALVERSION "-splitthp"
scripts/config --set-str SYSTEM_TRUSTED_KEYS ""
scripts/config --set-str SYSTEM_REVOCATION_KEYS ""
make olddefconfig
grep CONFIG_LOCALVERSION .config
```

Build and install the custom kernel:

```bash
cd ~/HugePageResearch/external/linux/ubuntu-6.8.0-101.101
make -j"$(nproc)" bzImage modules
sudo make modules_install install
```

Reboot into the custom kernel explicitly:

```bash
sudo grub-reboot "Advanced options for Ubuntu>Ubuntu, with Linux 6.8.12-splitthp"
sudo reboot
```

Verify after reboot:

```bash
uname -r
ls -l /boot/vmlinuz /boot/initrd.img /lib/modules/6.8.12-splitthp
```

Boot the stock kernel again if you need a control run:

```bash
sudo grub-reboot "Advanced options for Ubuntu>Ubuntu, with Linux 6.8.0-101-generic"
sudo reboot
```

## Jupyter Setup -- Recommended

You will need docker and uv,
we have a script that is for ubuntu 24.04 and 22.04 `source scripts/setup_prep_env.sh`.

### Ubuntu 24.04 or 22.04

```shell
# Run the full installation -- Expected Time: ~10 mins
source ./scripts/setup_prep_env.sh

# Install Hooks for linting -- Expected Time: ~2 mins
make hooks

# Run jupyter notebook -- Expected Time: instant
jupyter notebook
```

### Other Machines

You can install docker from [docker's website](https://docs.docker.com/engine/install/).
The tools should work with other container engines but have not been tested yet.
My suggestion is to make sure to add yourself to the docker group after.

We rely on the uv tool for python package management.
Here is a [helpful link](https://docs.astral.sh/uv/getting-started/installation/)
for installation.

From then on launch jupyter notebook like so:

```shell
# Create and run your management container -- Expected Time: ~5 mins
make docker-image


# Create your virtual environment -- Expected Time: ~5 mins
uv venv
uv sync
source .venv/bin/activate

# Install Hooks for linting -- Expected Time: ~2 mins
make hooks

# Run jupyter notebook -- Expected Time: instant
jupyter notebook
```

From here open up the Jupyter not

## Quick Setup

You will need docker.
You can install from [docker's website](https://docs.docker.com/engine/install/).
The tools should work with other container engines but have not been tested yet.

```shell

# Create and run your management container -- Expected Time: ~5 mins
make docker-image

# Install YCSB benchmark (Run inside Container) -- Expected Time: ~3 mins
make docker
make install-ycsb

# Copy a simple starting redis script (Run Outside Container) -- Expected Time: ~1 mins
cp config/start_overrides.yaml overrides.yaml

# Capture the simple overrides (Run Outside the Container) -- Expected Time: ~1 mins
make collect

# If you'd like to label the results of your runs, set a custom prefix with
# COLLECTION_PREFIX=<my label> make collect

```

## Capturing Data -> Processing in Python

For this example you need to open two terminals.

In Terminal 1 navigate to your cloned version of `KernMLOps`.

```shell
make docker
make collect-raw
```

You are looking for output that looks like this:

```shell
Hit Ctrl+C to terminate...
Started benchmark faux
```

This tells you that the probes have been inserted and data collection has begun.

In Terminal 2, start the application.
You will eventually need the pid,
the terminal can get you that as shown below.

```shell
./app arg1 arg2 arg3 &
echo $!
wait
```

The result of the command should be a pid.
The pid can be used later to filter results.
When the wait call finishes the program `app` has exited.
Then in Terminal 1 press `CTRL+C`.
Now the data should be collected in ...
Now in Terminal 1 you can exit the docker container,
enter python and import the last data collection.

The data is now under `data/curated` in the `KernMLOps` directory.

You can import that to python by doing the following:
Note that you need at least Python 3.12 (as is provided in the container)
Change to the `python/kernmlops/` directory

```python3
cd python/kernmlops/
python3
>>> import data_import as di
>>> r = di.read_parquet_dir("<path-to-data-curated>")
>>> r.keys()
```

In this case `r` is a dictionary containing a dataframe per key.
If we want to explore for example the `dtlb_misses` for our program
we can do the following:

```python3
>>> dtlb_data = r['dtlb_misses']
>>> import polars as pl
>>> dtlb_data.filter(pl.col("tgid") == <pid for program>)
```

## Tools

### Python-3.12

This is here to make the minimum python version blatant.

### [pre-commit](https://pre-commit.com)

A left shifting tool to consistently run a set of checks on the code repo.
Our checks enforce syntax validations and formatting.
We encourage contributors to use pre-commit hooks.

```shell
# install all pre-commit hooks
make hooks

# run pre-commit on repo once
make pre-commit
```

### [perf](https://man7.org/linux/man-pages/man2/perf_event_open.2.html)

Perf counters are used for low-level insights into performance.

When using a new machine it is likely the counters used will be different
from those already explicitly supported.  Developers can run
`python python/kernmlops collect perf-list` to get the names, descriptions,
and umasks of the various hardware events on the new machine. From there
developers can add the correct `name, umask` pair to an existing counter
config that already exists.

It is simplest to run the above command inside a container.

## Dependencies

### Python

Python is required, at least version `3.12` is required for its generic typing support.
This is the default version on Ubuntu 24.04.

Python package dependencies are listed in `requirements.txt` and can be
installed via:

```shell
# On some systems like Ubuntu 24.04 without a virtual environment
# `--break-system-packages` may be necessary
pip install [--break-system-packages] -r requirements.txt
```

## Contributing

Developers should verify their code passes basic standards by running:

```shell
make lint
```

Developers can automatically fix many common styling issues with:

```shell
make format
```

## Usage

Users can run data collection with:

```shell
make collect-raw
```

## Configuration

All default configuration options are shown in `defaults.yaml`, this can be generated
via `make defaults`.

To configure collection, users can create and modify a `overrides.yaml` file with
just the overrides they wish to set, i.e.:

```yaml
---
benchmark_config:
  generic:
    benchmark: gap
  gap:
    trials: 7
```

Then `make collect` or `make collect-data` will use the overrides set.

If an unknown configuration parameter is set (i.e. `benchmark_cfg`) and
error will be thrown before collection begins.

## Where To Read Next

If you want to understand the collector internals instead of only running the
top-level commands, start with these subsystem guides:

- [`python/kernmlops/data_collection/README.md`](python/kernmlops/data_collection/README.md)
  explains how the collector package turns configured hook names into running
  instrumentation.
- [`python/kernmlops/data_collection/bpf_instrumentation/README.md`](python/kernmlops/data_collection/bpf_instrumentation/README.md)
  explains what a hook is in this repository and where the built-in BPF-backed
  hooks live.
- [`docs/Add-Perf-Counter.md`](docs/Add-Perf-Counter.md) walks through adding a
  new perf counter when a machine exposes different event names.

## Troubleshooting: Or How I Learned to Shoot My Foot

### eBPF Programs

eBPF Programs are statically verified when the python scripts attempt
to load them to into the kernel and that is where errors will manifest.
When a program fails to compile the error
will be the usual sort of C-compiler error. i.e.

```shell
/virtual/main.c:53:3: error: call to undeclared function '__bpf_builtin_memset';
    ISO C99 and later do not support implicit function declarations
    [-Wimplicit-function-declaration]
   53 |   __bpf_builtin_memset(&data, 0, sizeof(data));
      |   ^
1 error generated.
```

For verification errors the entire compiled bytecode will be printed,
look for something along the lines of:

```shell
invalid indirect read from stack R4 off -32+20 size 24
processed 59 insns (limit 1000000) max_states_per_insn 0
    total_states 3 peak_states 3 mark_read 3
```

#### eBPF Padding

The error:

```shell
invalid indirect read from stack R4 off -32+20 size 24
processed 59 insns (limit 1000000) max_states_per_insn 0
    total_states 3 peak_states 3 mark_read 3
```

Indicates that a read in the program is reading uninitialized memory.

That error came from:

```c
struct quanta_runtime_perf_event data;
data.pid = pid;
data.tgid = tgid;
data.quanta_end_uptime_us = ts / 1000;
data.quanta_run_length_us = delta / 1000;
quanta_runtimes.perf_submit(ctx, &data, sizeof(data));
```

The invalid read was `perf_submit` since there was extra padding in the `data` struct
that was not formally initialized.  To be as robust as possible this should be handled
with an explicit `__builtin_memset` as in:

```c
struct quanta_runtime_perf_event data;
__builtin_memset(&data, 0, sizeof(data));
data.pid = pid;
data.tgid = tgid;
data.quanta_end_uptime_us = ts / 1000;
data.quanta_run_length_us = delta / 1000;
quanta_runtimes.perf_submit(ctx, &data, sizeof(data));
```

This gives the most robust handling for multiple systems,
see [here](https://github.com/iovisor/bcc/issues/2623#issuecomment-560214481).

### redis.clients.jedis.exceptions.JedisDataException
<!-- TODO: move this section to a benchmark-specific doc --!>

If you see the above error, you probably have stray `rdb` files that are causing
`redis` to start slowly, and maybe influencing your runs in other unexpected
ways. Ensure that `load_from_rdb` is not set to true in the `redis` section of
your benchmark config or always ensure that `dump.rdb` is not present before
starting a collection!
