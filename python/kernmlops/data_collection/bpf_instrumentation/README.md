# BPF Instrumentation

This directory contains the hook implementations used by the collector. In this
repository, a "hook" is a Python class that knows how to load some
instrumentation, poll it while a benchmark runs, and return the collected data
as typed tables.

## The Core Interface

[`bpf_hook.py`](./bpf_hook.py) defines the `BPFProgram` protocol. A hook class
must provide:

- `name()`, the string used in configuration
- `load(collection_id)`, which prepares the instrumentation for one run
- `poll()`, which drains any in-kernel or userspace buffers during the run
- `data()` / `pop_data()`, which expose the collected tables
- `clear()` and `close()`, which manage hook lifecycle and cleanup

The collector does not need to know the internals of a specific hook. It only
needs a class that follows this protocol.

## The Hook Registry

[`__init__.py`](./__init__.py) is the registry for the built-in hooks on
`main`. It imports each hook class, builds the `all_hooks` mapping, and exposes
`hook_names()` so the collector can populate the default hook list.

The built-in registry now includes hooks such as:

- file and memory usage tracking
- process metadata and process trace events
- quanta runtime and block I/O tracing
- perf counters
- huge-page-related hooks such as `thp_trace`, `vmstat_harness`,
  `thp_intervention`, `smaps_harness`, `proc_maps`, and `smaps_hook`
- zswap runtime tracing

If you want to know which names are enabled by default, compare this registry
with `collector_config.generic.hooks` in [`defaults.yaml`](../../../../defaults.yaml).

## How The Files Are Organized

- Files such as [`memory_usage_hook.py`](./memory_usage_hook.py) and
  [`process_metadata_hook.py`](./process_metadata_hook.py) define the Python hook
  classes.
- The [`bpf/`](./bpf) subdirectory contains the C source snippets that are
  compiled and loaded through BCC for hooks that use eBPF.
- The [`perf/`](./perf) subdirectory contains perf-specific configuration and
  helper code that support the `perf` hook.

The pattern in this repository is usually "one Python wrapper per hook, plus
one or more helper sources under `bpf/` when kernel-side instrumentation is
required."

For the THP harness path, there are two kinds of hooks:

- kernel-facing hooks such as [`thp_trace.py`](./thp_trace.py), which use BCC
  and the `bpf/` sources to watch huge-page behavior in the kernel
- userspace samplers such as
  [`thp_intervention_hook.py`](./thp_intervention_hook.py),
  [`proc_maps_hook.py`](./proc_maps_hook.py), and
  [`proc_smaps_hook.py`](./proc_smaps_hook.py), which read `/proc` and debugfs
  to track Redis mappings and issue split commands

## How A Hook Gets Used

When the collector starts a run, it reads the configured hook names from the
active YAML config. `GenericCollectorConfig.get_hooks()` in the parent
`data_collection` package looks those names up in `all_hooks`, instantiates the
matching classes, and hands the resulting objects to
[`python/kernmlops/cli/collect.py`](../../cli/collect.py).

That means there are three places to check when you are trying to understand or
debug a hook:

1. the YAML hook name in [`defaults.yaml`](../../../../defaults.yaml) or an
   override file in [`config/`](../../../../config)
2. the registry entry in [`__init__.py`](./__init__.py)
3. the hook implementation file itself

## Perf Counters

The perf hook deserves special mention because it depends on machine-specific
hardware event names. If a machine exposes different counters from the ones
already supported in the repo, read
[`docs/Add-Perf-Counter.md`](../../../../docs/Add-Perf-Counter.md) for the
step-by-step process to extend the mapping.

## Current Scope On `main`

This README documents the hook system that is present on `main` now, including
the Redis/VAPTR hooks and the THP harness helpers for map sampling and
intervention.
