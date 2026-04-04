# Agent Guide

## Read First

- Read `RESEARCH.md` first. It is the rolling local research-context file for this repo and should carry the detailed record of user goals, preferences, decisions, verified findings, progress, and next steps. `RESEARCH.md` is intentionally gitignored, so keep it current as work progresses.
- If `THREAD_PROMPT.md` exists, read it after `RESEARCH.md` and reconcile any drift immediately. Prefer the newer verified state from `RESEARCH.md` and the current working tree if the two disagree.
- Read `PLANS.md` before creating or updating any ExecPlan-style specification, and follow it strictly.
- When writing a PR, avoid too technical lingo and focus on providing understanding from someone reading the docs. You should provide a simple 1-2 sentence overview of the PR (e.g. "Allow virtual address query of redis keys using VAPTR [key])" and then examples of what changed. Avoid statements that require a lot of context or use jargon like "keep `python/kernmlops/data_collection/bpf_instrumentation/bpf_hook.py` aligned with the existing collector import surface" - what is collector import surface? This is too complex, something like "Maintained bpf hook interface" would be fine and simpler/easier to read.

## Keep Updated

- Maintain `RESEARCH.md` as the detailed rolling handoff log.
- Maintain `MEMORY_BLOAT_README.md` as the persistent human resume doc for machine/repo state. Whenever you install dependencies, fetch source trees, change build prerequisites, or otherwise change resume-critical local state, update it as a short numbered list with exact commands. Do not use `sed` dump commands there, and do not turn it into an agent-oriented handoff file.
- If local source trees such as `external/linux/ubuntu-6.8.0-101.101/` may be missing on another machine or checkout, put the exact recreate steps in `MEMORY_BLOAT_README.md` rather than relying on `.gitignore` behavior.
- Keep this file focused on durable orientation: concise user/project context, repo structure, and where future agents should look first.
- After a major task, write a focused markdown report describing what was done, why it works, approaches considered, and pros/cons. Prefer `reports/kernel/`, `reports/collector/`, `docs/`, or another existing task-appropriate folder over creating new root-level scratch files.

## Current Research Snapshot

- Main research goal: measure the utility of individual 2 MiB Transparent Huge Pages in Redis, especially the effect of splitting the single THP containing a target object.
- Current kernel mechanism: a custom x86_64 syscall with the simple ABI `pid + vaddr`, intended to split the one THP containing `vaddr` in the target process.
- Current verified state: the matching Ubuntu `6.8.0-101.101` source tree exists under `external/linux/`, the userspace verifier suite lives under `tests/syscall_verification/`, and prior work has already reached patched-kernel build/install/boot plus successful live verifier runs.
- Important research caveat: across fresh Redis runs, object placement is not stable enough to claim the same key lands in the same THP extent by default. Future experiment writeups must distinguish per-run THP utility from stable page-identity claims unless stronger controls are added.

## Repo Structure

- `README.md`, `Makefile`, `pyproject.toml`, `requirements.txt`, `uv.lock`, and `Pipfile`: top-level setup, developer workflows, and Python packaging entry points.
- `python/kernmlops/`: main Python package for collection, orchestration, schema definitions, and analysis.
- `python/kernmlops/cli/`: CLI entry points used by `python python/kernmlops ...` and `make collect*`.
- `python/kernmlops/kernmlops_benchmark/`: benchmark launchers for Redis, memcached, MongoDB, GAP, Linux build, and related workloads.
- `python/kernmlops/data_collection/bpf_instrumentation/`: active hook implementations. For Redis/THP work, start here with `thp_intervention_hook.py`, `vaptr_hook.py`, and `thp_trace.py`.
- `python/kernmlops/analysis/`: post-processing and graph/report helpers such as THP harness analysis and VAPTR access analysis.
- `python/kernmlops/data_schema/`: parquet/table schema definitions consumed by analysis code.
- `python/kernmlops/kernmlops_config/`: config models, including hugepage harness config wiring.
- `config/`: runnable YAML configs. The Redis and THP-research-specific files live here, including `redis_thp_harness_v1.yaml`, `redis_vaptr_e2e.yaml`, and `redis_vaptr_access_bit_e2e.yaml`.
- `redis-module/`: Redis module source for `VAPTR` (`vaptr.c`, headers, `Makefile`) plus local build outputs such as `vaptr.so`.
- `tests/syscall_verification/`: standalone C verifier suite and helper CLIs for the custom THP-splitting syscall. Edit the `.c`/`.h`/`Makefile` sources, not the compiled binaries or `.o` files.
- `testing/`: older Python/C tests for collector hooks and page-access behavior. This is distinct from `tests/syscall_verification/`.
- `external/linux/ubuntu-6.8.0-101.101/`: exact Ubuntu kernel source tree used for syscall development and compile/install work. If it is missing on another checkout, recreate it using the commands documented in `MEMORY_BLOAT_README.md`.
- `reports/kernel/`: milestone reports for syscall design, kernel source setup, patching, build, install, and runtime verification.
- `reports/collector/`: collector and VAPTR debugging/validation reports.
- `reports/archive/`: older historical notes that may still be useful for context.
- `docs/`: older research notes and archived objectives/tasks.
- `benchmark/`: provisioning and remote benchmark setup helpers, including Ansible-based machine setup and zswap tooling.
- `module/`: older out-of-tree kernel module and eBPF experimentation. Useful background, but usually not the first stop for the current THP syscall path.
- `scripts/`: environment bootstrap and benchmark setup helpers such as `setup_prep_env.sh` and `setup-benchmarks/`.
- `data/curated/`: collected experiment outputs.
- `figures/`: generated plots and rendered visuals.
- `YCSB/`: benchmark dependency tree used by some workloads.
- `cleaningup.md`: notes for the separate cleanup/readability pass.
- `.venv/`, `__pycache__/`, build outputs in `tests/syscall_verification/`, and similar binary artifacts are local/generated state rather than primary source files.

## Practical Navigation

- For Redis THP experiment work, start with `config/redis_thp_harness_v1.yaml`, `config/redis_vaptr_e2e.yaml`, `redis-module/vaptr.c`, and `python/kernmlops/data_collection/bpf_instrumentation/thp_intervention_hook.py`.
- For kernel syscall work, start with `external/linux/ubuntu-6.8.0-101.101/`, `tests/syscall_verification/README.md`, and the checkpoint reports in `reports/kernel/`.
- For collector/VAPTR debugging history, check `reports/collector/` before creating new scratch notes.
- Keep durable reports in `reports/` and keep detailed rolling state in `RESEARCH.md`; avoid adding new root-level scratch markdown unless the user explicitly wants it there.

# How to Start