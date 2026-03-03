# Perf Buffers / Missing Graphs Report

## Summary

Two different issues happened:

1. `collect graph` on the cleaned `redis_always_compat` run printed `system_info` but created no PNGs.
2. A later collection command failed in `PerfBPFHook.load()` with:
   - `perf_event_open: Too many open files`
   - `Exception: Could not open perf buffer`

These are separate problems.

## 1) Why no graph PNG was created

### What happened

You ran `collect graph` for:

- `cleanedredis_always_compat-20260224T103102658135`

Then `find data/graphs/redis/...` showed no directory.

### Why it happens

Your compat config disables `perf`:

- `config/redis_always_compat.yaml:27` has `# - perf`

That cleaned run only contains:

- `system_info.end.parquet`
- `mm_rss_stat.end.parquet`
- `process_trace.end.parquet`

`collect graph` only graphs tables that are loaded into `data_schema.table_types`:

- `python/kernmlops/data_schema/__init__.py:25`

`mm_rss_stat` and `process_trace` are not in that list, so only `system_info` loads. `system_info` has no graphs, so:

- nothing is plotted
- nothing is saved
- no `data/graphs/redis/<collection_id>/` directory is created

### Important path note

Graph output goes under:

- `data/graphs/...`

not:

- `data/curated/graphs/...`

## 2) Why `Could not open perf buffer` happens

### What the traceback shows

The failure occurs during perf hook setup here:

- `python/kernmlops/data_collection/bpf_instrumentation/perf/perf_hook.py:126`

That line opens BCC perf buffers:

- `self.bpf[event_name].open_perf_buffer(...)`

Before that, the hook also attaches perf events on **all CPUs**:

- `cpu=-1` in `perf_hook.py:108`

### Real root cause

This is a file descriptor exhaustion problem (FD limit), not mainly a kernel version problem.

BCC prints a generic hint:

- `(check your kernel for PERF_COUNT_SW_BPF_OUTPUT support, 4.4 or newer)`

But your actual error is:

- `Too many open files`

That means the process hit `RLIMIT_NOFILE` (open file limit) while creating per-CPU perf event/buffer resources.

## Why perf buffers can consume so many FDs

The perf hook creates multiple streams and each stream fans out across CPUs.

In this repo, there are up to 11 perf tables defined:

- `dtlb_*`, `itlb_*`, `instructions_retired`, `page_faults`, `minor_faults`, `major_faults`, `tlb_flushes`, etc.

On machines without custom raw counter support, fewer may load (often ~8).

The perf hook then:

1. attaches a perf event per enabled stream per CPU
2. opens a perf buffer per enabled stream (also backed per CPU by BCC)

So FD usage scales roughly with:

- `(# enabled perf streams) x (# CPUs)`

and then increases further with:

- other BPF hooks that also use perf buffers (`mm_rss_stat`, `process_trace`, etc.)
- BPF maps/program fds
- normal process fds

On a many-core machine (your environment shows 40 CPUs in this workspace), a low container/session `ulimit -n` like `1024` can be exhausted quickly.

## Why this is confusing with `redis_always_compat.yaml`

Your posted traceback goes through `perf_hook.py`, but `redis_always_compat.yaml` has `perf` commented out.

That means one of these is likely true:

- the actual command that ran was not using the compat config you expected
- a shell alias/wrapper (like `cdata`) used a different config
- the terminal command was malformed (your pasted history shows garbled command text)
- the file on disk at runtime differed from what was open in the editor

I verified the merge behavior in this repo: a `hooks:` list in YAML replaces the default hook list, so compat config should resolve to only:

- `mm_rss_stat`
- `process_trace`

If `perf_hook.py` appears in the traceback, the effective runtime config likely included `perf`.

## How to verify the effective hooks before running

Run this to confirm what hooks the config actually resolves to:

```bash
PYTHONPATH=.venv/lib/python3.12/site-packages:python/kernmlops python3 - <<'PY'
import yaml
from cli.config import KernmlopsConfig
cfg = KernmlopsConfig().merge(yaml.safe_load(open('config/redis_always_compat.yaml').read()))
print(cfg.collector_config.generic.hooks)
PY
```

Expected output for compat:

```text
['mm_rss_stat', 'process_trace']
```

## How to fix / avoid each issue

### If you want graph PNGs from compat runs

Current compat runs will not generate graphs with existing graph loaders because they only contain non-graphable tables (`mm_rss_stat`, `process_trace`, `system_info`).

Options:

1. Use a config with `perf` enabled (for perf graphs), e.g. `redis_always.yaml`.
2. Add graph support for `mm_rss_stat` and register it in `data_schema.table_types` (code change).
3. Use a custom analysis script for `mm_rss_stat` instead of `collect graph`.

### If you want to avoid perf buffer failures

1. Increase `ulimit -n` in the same shell/container running collection.
2. Disable `perf` when not needed (use compat config or comment out `perf`).
3. Reduce enabled hooks to lower total perf-buffer streams.
4. Run on fewer CPUs only if the code is changed to attach perf events to a CPU subset (current perf hook uses `cpu=-1`, all CPUs).

## Commands to check FD limits (run in the same container/session)

```bash
ulimit -n
nproc
python3 - <<'PY'
import os, resource
print("RLIMIT_NOFILE:", resource.getrlimit(resource.RLIMIT_NOFILE))
print("CPUs:", os.cpu_count())
PY
```

## Quick diagnosis checklist

1. Confirm graph target path is `data/graphs/...`, not `data/curated/graphs/...`
2. Check parquet files in the collection:
   - if only `system_info`, `process_trace`, `mm_rss_stat` are present, `collect graph` will not create PNGs
3. Confirm effective hooks for the config (see snippet above)
4. If traceback includes `perf_hook.py`, treat it as a perf-enabled run
5. Check `ulimit -n` in that exact shell/container

## Bottom line

- The missing PNGs are expected for the compat cleaned run because it contains no graphable tables under the current graph pipeline.
- The perf buffer failure is caused by FD exhaustion during perf hook setup (per-stream x per-CPU fan-out), not primarily kernel feature support.
- The traceback entering `perf_hook.py` while using a compat config indicates the actual runtime config/command likely differed from what you intended.
