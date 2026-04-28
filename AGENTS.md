# Agent Guide

## Read First

- Fail fast in replay code. Do not add fallback paths, fallback options, backwards compatibility, or overengineer
- 2-Y-Z Amin-B means run a test with first 2 breakpoint (base pages, all thp), Y hottest breakpoints, Z random breakpoints, each key for A min for B runs
- Avoid calling things "smoke" tests, prefer verification/tests/quick examples
  silent backup behavior unless the user explicitly asks for them.
- After running long experiments, 
    a) document your changes, results, architectural decisions if you must change something. You must also remember to create a html graph (like basepages_10keys_dashboard.html)
    b) When running long running experiments prefer tmux
- I am currently working on replay_trace.py script. Please read it. 
    c) When you cannot break a page at all, you should somehow include it in your data (e.g. edit parquet to count number of failed splits and also max number of attempts to attemp to split. when you draw graphs, you must include in the bar char of time how many times the key was successfuly split (e.g. (2) hot key #1, (3) hot key #2, and skip over keys that are not split at all. write max attempts in your html graph, and also all configurations needed to run the experiment (so it is easy to remember). draw std for each bar on your graphs. 
    d) If running out of space on disk, please delete any unused files (e.g. snapshots from previous runs)
    e) NEVER assume a long-running experiment is doing fine - you NEED to check up on it every 20 mins to make sure the script is up. You don't have to do too much when you poll, just check that it is running
    f) Always write the exact directories you got your data for your graphs from (e.g. 20260413T021419067720/redis_benchmark.log)
- Avoid spinning up your own docker container most of the time (when running experiments). You should ask me if the docker container is not avaliable, otherwise use my most recently created docker container. If describing a workflow (for example in replay.md), do not assume a docker container exists when describing something (but feel free to say use the most previously created docker container)
- Read `PLANS.md` before creating or updating any ExecPlan-style specification, and follow it strictly.
- Every edit should be part of a PR. These PRs ought to be very human readable. Avoid complex language.
- When writing a PR, avoid too technical lingo and focus on providing understanding from someone reading the docs. You should provide a simple 1-2 sentence overview of the PR (e.g. "Allow virtual address query of redis keys using VAPTR [key])" and then examples of what changed. Avoid statements that require a lot of context or use jargon like "keep `python/kernmlops/data_collection/bpf_instrumentation/bpf_hook.py` aligned with the existing collector import surface" - what is collector import surface? This is too complex, something like "Maintained bpf hook interface" would be fine and simpler/easier to read.

## Keep Updated

- Whenever you change `generate_breakpoints.py`, `replay_trace.py`, or `capture_redis_trace.sh`, update `replay.md` to match. The runbook must stay in sync with the actual CLI flags and behavior.
- Maintain `SETUP.md` as the persistent human resume doc for machine/repo state. Whenever you install dependencies, fetch source trees, change build prerequisites, or otherwise change resume-critical local state, update it as a short numbered list with exact commands. Do not use `sed` dump commands there, and do not turn it into an agent-oriented handoff file.
- If local source trees such as `external/linux/ubuntu-6.8.0-101.101/` may be missing on another machine or checkout, put the exact recreate steps in `MEMORY_BLOAT_README.md` rather than relying on `.gitignore` behavior.
- Keep this file focused on durable orientation: concise user/project context, repo structure, and where future agents should look first.
- After a major task, write a focused markdown report describing what was done, why it works, approaches considered, and pros/cons. Prefer `reports/kernel/`, `reports/collector/`, `docs/`, or another existing task-appropriate folder over creating new root-level scratch files.

## Commenting Style

- Prefer multi-line docstrings for non-trivial functions and helper classes.
- Match this structure:

  ```python
  def example(arg: str) -> int:
      """One-line summary.

      Short plain-English explanation of what the function does and any
      important repo-specific assumption.

      Example output (only if its not obvious by the returns):
          {"process_id": 608669}
`
      Args:
          arg: Meaning of the argument in this repo.

      Returns:
          Meaning of the return value.

      Raises:
          RuntimeError: Why this function fails fast.
      """
  ```

- For dataclasses, prefer a short summary plus an `Attributes:` block like:

  ```python
  @dataclass(frozen=True)
  class RedisCommand:
      """A single parsed command from a redis-cli MONITOR log line.

      Attributes:
          command: The Redis command verb (e.g. ``HSET``, ``GET``).
          args: All arguments following the command verb.
          key: The key being operated on (``args[0]``, or empty string).
      """
  ```

- When a helper consumes or produces a structured Redis, kernel, or command
  result, include one short example of the expected shape in the docstring.
- Prefer fail-fast docstrings that explain the expected happy-path shape rather
  than long lists of speculative fallback cases.

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

# How to Start
