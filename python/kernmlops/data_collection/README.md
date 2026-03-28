# Data Collection

This directory contains the Python-side collector stack. When someone runs
`make collect`, or the equivalent `python python/kernmlops collect data ...`
command, this package is what turns configuration into active instrumentation
and output parquet files.

## What Lives Here

[`__init__.py`](./__init__.py) defines `GenericCollectorConfig`, the config
object that controls poll rate, output location, and which hooks should run for
a collection. The same file imports `data_collection.bpf_instrumentation` as the
registry of available hooks and exposes `machine_info()` so each run captures a
system-information table alongside the collected hook data.

[`system_info.py`](./system_info.py) gathers machine metadata that is written to
every collection. [`track_idle_accesses.py`](./track_idle_accesses.py) is a
standalone helper for Linux idle-page tracking; it is adjacent to the collector
package because it works with the same output-oriented workflow, but it is not
part of the default `make collect` path.

[`page_access.py`](./page_access.py) is a standalone helper for resolving a
process virtual address to its backing page frame number and sampling Linux's
idle-page bitmap for that page. It is collector-adjacent infrastructure rather
than a default hook on `main`, which is why it lives here but is documented
separately from the BPF-backed hook registry.

## How A Collection Run Works

The CLI entrypoint lives in
[`python/kernmlops/cli/__init__.py`](../cli/__init__.py). The `collect data`
command reads a YAML config, constructs a `GenericCollectorConfig`, constructs a
benchmark object, and then calls
[`run_collect`](../cli/collect.py) in
[`python/kernmlops/cli/collect.py`](../cli/collect.py).

Inside `run_collect`, the collector:

1. asks `GenericCollectorConfig.get_hooks()` for instantiated hook objects
2. loads each hook with a collection id
3. starts the benchmark
4. polls each hook until the benchmark finishes
5. drains each hook's buffered tables and writes them to parquet files

The important thing to remember is that the collector is configuration-driven.
The hook names come from `collector_config.generic.hooks` in the active YAML
file. By default those names are populated from
[`defaults.yaml`](../../../defaults.yaml).

## How Hooks Are Chosen

The built-in hook registry lives in
[`bpf_instrumentation/__init__.py`](./bpf_instrumentation/__init__.py). That
module exposes two important names:

- `all_hooks`, a mapping from a hook name like `perf` or `memory_usage` to the
  Python class that implements that hook
- `hook_names()`, which returns the default hook order used by the collector

`GenericCollectorConfig.get_hooks()` filters `all_hooks` by the configured hook
names and instantiates one object for each enabled hook. The resulting objects
all implement the `BPFProgram` protocol described in
[`bpf_instrumentation/bpf_hook.py`](./bpf_instrumentation/bpf_hook.py).

## Current Scope On `main`

This README describes the collector stack that exists on `main` today. It
covers the generic collector, the existing BPF-backed hooks, and the current
configuration-driven polling/output path.

The new standalone page-access helper now also lives in this package, but the
Redis-address tracking and VAPTR-specific collection path are still being
merged separately in later batches. Those systems should get their own
directory readmes when the supporting code lands so the documentation stays
aligned with the working tree instead of describing future files that do not yet
exist on `main`.

## Useful Commands

From the repository root:

- `make defaults`
  regenerates [`defaults.yaml`](../../../defaults.yaml) from the dataclass-based
  config model.
- `make collect`
  runs the default collection flow using the current config override file.
- `python python/kernmlops collect perf-list`
  prints the perf event names this machine exposes, which is useful when adding
  or debugging perf-backed hooks.

## Where To Read Next

After this file, the next place to read is
[`python/kernmlops/data_collection/bpf_instrumentation/README.md`](./bpf_instrumentation/README.md).
That file explains how individual hooks are structured and where the BPF-backed
logic actually lives.
