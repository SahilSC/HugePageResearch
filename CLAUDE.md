# CLAUDE.md

Seek to verify any claims with 100% accuracy via factual evidence. Search online if you have to. If you present a fact or statement or fix or suggestion, you should implement and verify it works before telling me.

## Project Overview

KernMLOps is a kernel performance data collection and analysis framework. It uses eBPF (via BCC) to instrument the Linux kernel, collects hardware/software performance counters and kernel events, writes data as Parquet files, and provides tooling for analysis and visualization. Current research focus is on Transparent Huge Pages (THP).

## Quick Reference

```shell
# Setup (Ubuntu 24.04)
source ./scripts/setup_prep_env.sh   # installs docker + uv, builds docker image
make hooks                            # install pre-commit hooks

# Build & run
make docker                           # enter privileged Docker container
make collect-raw                      # collect with faux benchmark (Ctrl+C to stop)
make collect                          # collect using overrides.yaml config

# Lint (this is the test suite - there are no unit tests)
make lint                             # ruff check + pyright
make pre-commit                       # all pre-commit hooks

# Other useful targets
make defaults                         # regenerate defaults.yaml
make dump                             # print collected data
make benchmark-redis                  # run Redis benchmark collection
```

## Project Structure

```
python/kernmlops/              Main Python package (entry point: __main__.py)
  cli/                         Click CLI commands (collect, dump, graph, perf-list)
  data_collection/             Collector configs and BPF instrumentation
    bpf_instrumentation/       BPF hooks, perf counters, harnesses
      bpf/                     Raw BPF C programs (*.bpf.c)
      perf/                    Hardware perf counter hooks (libpfm4-based)
  data_schema/                 Polars-based table/graph schemas for collected data
    perf/                      Perf counter-specific schemas
  kernmlops_benchmark/         Benchmark implementations (redis, mongodb, gap, etc.)
  kernmlops_config/            Config dataclasses
  data_import/                 Parquet file reading utilities
  analysis/                    Post-hoc analysis scripts
config/                        YAML config overrides for different scenarios
data/curated/                  Output directory for collected Parquet data
scripts/                       Setup and benchmark installation scripts
module/                        Kernel module for in-kernel inference
docs/                          Additional documentation
```

## Architecture & Patterns

**Protocol-based extensibility**: All extension points use Python `Protocol` (structural subtyping), not inheritance:
- `BPFProgram` — hooks that collect kernel data
- `CollectionTable` — schema for a collected data table
- `CollectionGraph` — visualization of collected data
- `Benchmark` — workload to run during collection

**Config system**: Frozen `@dataclass` classes inheriting `ConfigBase` with deep YAML `merge()`. Composed via `make_dataclass()`. Unknown config keys error immediately.

**Data flow**: BPF hooks -> Python lists -> `pop_data()` -> `pl.DataFrame` -> `CollectionTable` -> Parquet files. All timestamps use `ts_uptime_us` (microseconds since boot).

**Threading model in `run_collect()`**: polling thread (drains BPF buffers), periodic output thread (flushes Parquet), stdin watcher thread (`END` signal), main thread (runs benchmark).

## Code Conventions

- Python 3.12 required
- Files: `snake_case.py`; Classes: `PascalCase`; Tables end in `Table`, Graphs in `Graph`, Hooks in `Hook`
- BPF C files: `<name>.bpf.c` formatted with clang-format
- Use Polars (not pandas) for all DataFrame operations
- Data stored as Parquet files in `data/curated/<benchmark>/<collection_id>/`
- Perf counters use libpfm4 for portable hardware event resolution

## Key Dependencies

- **BCC** (`python3-bpfcc`): system package, NOT pip-installable. Must use `--system-site-packages` in venv
- **uv**: primary Python package manager (`uv venv && uv sync`)
- **Polars** (`polars-lts-cpu`): DataFrame library
- **Click**: CLI framework
- **libpfm4**: hardware perf event name resolution
- **Docker**: collection runs in privileged container with `--pid=host`

## Adding New Components

- **New perf counter**: Follow `docs/Add-Perf-Counter.md`. Add to `perf/` directory, register in `perf/__init__.py`
- **New BPF hook**: Implement `BPFProgram` protocol, register in `bpf_instrumentation/__init__.py` `all_hooks`
- **New table schema**: Implement `CollectionTable` protocol, register in `data_schema/__init__.py` `table_types`
- **New benchmark**: Implement `Benchmark` protocol, register in `kernmlops_benchmark/__init__.py`

## Configuration

- `defaults.yaml` — auto-generated, all defaults (do not edit manually)
- `overrides.yaml` — user-created, Makefile picks it up automatically
- `config/*.yaml` — named scenarios (e.g., `redis_always_compat.yaml`)
- Top-level config keys: `benchmark_config`, `collector_config`, `hugepage_harness`

## CI

GitHub Actions (`.github/workflows/lint.yaml`): runs `make lint` and `make pre-commit` on push/PR to `main`.
