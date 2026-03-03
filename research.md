# HugePageResearch Deep Dive and Bug Audit

## Scope and Method

This report is based on a deep audit of the repository (current working tree state, including local uncommitted changes). I traced the end-to-end collection path, hook registration, schema conversion, output layout, and THP harness analysis flow, with emphasis on intermittent missing-data failure modes.

I did not run a fresh privileged BPF collection from this shell, but I did validate key findings with commands against existing collected runs in `data/curated`, plus direct execution of the real config/registry/loader code paths using host-side import stubs for `bcc`/`osquery` (because Docker socket access is unavailable from this shell). A `compileall` pass was attempted but blocked by `.pyc` write permission errors.

## Executive Summary

The repository is a kernel-performance data collection framework (`kernmlops`) with:

- benchmark runners (Redis, memcached, MongoDB, GAP, Linux build, faux)
- BPF-based hooks plus procfs/system samplers
- parquet output
- schema + graphing utilities
- newer THP/hugepage harness instrumentation and post-processing

The most likely cause of "some data is not always being collected" is a real race in the collection runtime:

- periodic output flushes call `pop_data()` concurrently with the polling thread appending samples
- the output thread is not stopped correctly for naturally-terminating benchmarks
- the final flush can race with a still-running periodic flush thread

This can produce intermittent missing samples (especially under load), and the code currently has several silent-drop behaviors that hide the problem.

Separately, I confirmed there are also "data appears missing" issues caused by loader/layout mismatches and partial registry integration (especially around THP trace and THP harness tables).

## Repository Architecture (How It Works)

### Operator entrypoints

- `Makefile` wraps most workflows (`collect`, `collect-data`, benchmark-specific targets, Dockerized collection).
- `make collect` ultimately runs `python python/kernmlops collect ...` inside the container (`Makefile` lines around `collect` / `collect-data`).
- Default config is `defaults.yaml`, optionally overridden by `overrides.yaml` (`Makefile`, `KERNMLOPS_CONFIG_FILE` logic).

### Python CLI and config composition

- `python/kernmlops/__main__.py` dispatches to `cli.main()`.
- `python/kernmlops/cli/__init__.py` defines `collect data`, `collect dump`, `collect graph`, and `collect perf-list`.
- `collect data` loads YAML, merges into a dataclass config (`KernmlopsConfig`), selects a benchmark, and calls `cli.collect.run_collect()`.

Config structure:

- top-level `benchmark_config`
- top-level `collector_config`
- top-level `hugepage_harness` (`python/kernmlops/cli/config.py:23-27`)

### Benchmarks

Benchmarks implement a common protocol (`python/kernmlops/kernmlops_benchmark/benchmark.py`) with:

- `setup()`
- `run()`
- `poll()`
- `kill()`

`poll()` is used by the collector thread to detect completion while probes keep draining.

### Hook registry and instantiation

Hooks live in `python/kernmlops/data_collection/bpf_instrumentation/`.

- Registry: `python/kernmlops/data_collection/bpf_instrumentation/__init__.py`
- Hook names are configured via `collector_config.generic.hooks`
- `GenericCollectorConfig.get_hooks()` instantiates hooks and passes `hugepage_harness` only to `smaps_harness` and `vmstat_harness` (`python/kernmlops/data_collection/__init__.py:21`)

There are two hook styles:

- BPF perf-buffer hooks (`perf`, `file_data`, `block_io`, `process_trace`, etc.)
- polling/procfs hooks (`memory_usage`, `smaps_harness`, `vmstat_harness`)

### Collection runtime (critical flow)

`python/kernmlops/cli/collect.py` orchestrates collection:

1. Build hooks and load them (`run_collect`, lines `153-175`)
2. Start helper threads:
   - stdin `END` watcher (`wait_for_END`)
   - polling thread (`poll_instrumentation`)
   - periodic output thread (`output_data_thread`)
3. Run benchmark (`benchmark.run()`)
4. Wait for poll thread to publish completion via queue (`queue.get()`)
5. Close hooks
6. Final flush to parquet (`output_collections_to_file(..., "end", ...)`)
7. Optionally graph from in-memory tables

### Output format (current implementation)

Data is written to:

- `data/curated/<benchmark>/<collection_id>/<table_name>.<chunk>.parquet`

This is explicit in `output_collections_to_file()` (`python/kernmlops/cli/collect.py:86-91`).

Chunks include:

- periodic flushes: `0`, `1`, `2`, ...
- final flush: `end`

### THP / Hugepage harness specifics

The newer hugepage harness is a hybrid collector + labeling system:

- `smaps_harness.py` samples `/proc/<pid>/smaps_rollup` and `/proc/<pid>/smaps`
- builds candidate regions (`thp_candidates`)
- optionally writes split requests to debugfs (`split_huge_pages`)
- records interventions (`thp_interventions`)
- `vmstat_harness.py` samples `/proc/vmstat`
- `thp_trace.py` BPF hook captures THP trace events (scan/collapse/migrate/compaction/syscalls/TLB flush)
- `analysis/thp_harness.py` joins these with perf counters to produce decision datasets and intervention matching

This is a substantial new pipeline and currently has integration gaps (detailed below).

## High-Confidence Bugs (Prioritized)

### 1. Intermittent sample loss due to race between polling and periodic flush (primary data-loss bug)

Files:

- `python/kernmlops/cli/collect.py:42`
- `python/kernmlops/cli/collect.py:80`
- `python/kernmlops/cli/collect.py:113`

What happens:

- poll thread calls `bpf_program.poll()` and handlers append to in-memory lists
- periodic output thread concurrently calls `bpf_program.pop_data()` which does `data()` then `clear()`
- there is a lock, but only the output thread/final flush uses it; poll thread does not

Impact:

- samples appended during `pop_data()` can be cleared without ever being written
- missing data is intermittent and workload-dependent (exactly the kind of symptom you described)

Why this is real:

- `output_lock` is never used in `poll_instrumentation()`
- all hook buffers are mutable Python lists with no synchronization

### 2. Output thread is not stopped for naturally-terminating benchmarks (can race final flush and clear data)

Files:

- `python/kernmlops/cli/collect.py:40-63`
- `python/kernmlops/cli/collect.py:98-133`
- `python/kernmlops/cli/collect.py:201-223`
- `python/kernmlops/cli/collect.py:261-273`

What happens:

- `run_event` is only cleared by signal/`END`, not when benchmark naturally exits
- `poll_instrumentation()` exits loop with `return_code` but does not clear `run_event`
- periodic output thread loops on `while run_event.is_set()`

Additional bug making this worse:

- `ended` is passed as a plain bool to `output_data_thread()` (`False` at thread start)
- later `ended = True` in `run_collect()` does not affect the thread (not shared state)

Impact:

- output thread can continue running after benchmark completion
- it may flush/clear hook buffers before or during final flush
- final `end` parquet may miss data
- thread may keep flushing closed hooks and print exceptions

This is a severe lifecycle bug and a likely source of intermittent missing final samples.

### 3. Poll thread can die on hook exception and main thread blocks forever on `queue.get()`

Files:

- `python/kernmlops/cli/collect.py:41-49`
- `python/kernmlops/cli/collect.py:236`

What happens:

- `poll_instrumentation()` only catches `BenchmarkNotRunningError`
- any hook exception from `bpf_program.poll()` escapes the thread
- `queue.put(return_code)` is never reached
- main thread blocks indefinitely on `queue.get()`

Impact:

- collection appears hung
- final flush may never happen
- partial data can be left on disk without clear error propagation

This is not just a usability issue; it can masquerade as missing data / incomplete runs.

### 4. `thp_trace` hook exists but is not registered, and unknown hooks are silently ignored

Files:

- `python/kernmlops/data_collection/bpf_instrumentation/thp_trace.py:165` (`THPTraceBPFHook.name() -> "thp_trace"`)
- `python/kernmlops/data_collection/bpf_instrumentation/__init__.py:31-47` (missing `THPTraceBPFHook`)
- `python/kernmlops/data_collection/__init__.py:24-26` (unknown hook names are just `continue`)
- `config/redis_thp_harness_v1.yaml:35` (requests `thp_trace`)

Impact:

- users can configure `thp_trace` and believe it is active
- collector silently skips it
- all THP trace event tables are absent

This is a direct "configured but not collected" bug.

Empirical evidence from existing run data:

- In `data/curated/redis/b929691a-c663-4718-9cdc-77cfc70863fa`, `system_info.end.parquet` shows hooks `["mm_rss_stat", "process_trace", "perf", "smaps_harness", "vmstat_harness"]` and does not include `thp_trace`.
- The same run does include `smaps_*`, `vmstat_samples`, and `thp_candidates`/`thp_interventions`, which confirms the THP harness was active while `thp_trace` specifically was not.

### 5. `redis_thp_harness_v1.yaml` nests `hugepage_harness` under `collector_config`, but code expects top-level `hugepage_harness`

Files:

- `config/redis_thp_harness_v1.yaml:25-54` (`collector_config.hugepage_harness`)
- `python/kernmlops/cli/config.py:23-27` (`KernmlopsConfig` top-level `hugepage_harness`)
- `python/kernmlops/kernmlops_config/__init__.py:10-30` (`ConfigBase.merge` + `dataclasses.replace`)

Why this is a bug:

- `CollectorConfig` only has a `generic` field
- adding `hugepage_harness` under `collector_config` introduces an unknown dataclass field during merge
- `dataclasses.replace(self, **merged_config)` should fail with an unexpected keyword argument

Impact:

- config load fails before collection starts when this file is used as-is

Empirical validation:

- Executing the real merge path (`KernmlopsConfig().merge(...)`) raises:
- `TypeError: CollectorConfig.__init__() got an unexpected keyword argument 'hugepage_harness'`

### 6. Reader/writer layout mismatch makes standard loaders/graphing miss or fail to load collected data

Files:

- Writer path: `python/kernmlops/cli/collect.py:86-91`
- Legacy/alternate reader: `python/kernmlops/data_import/__init__.py:12-22`
- Graph loader: `python/kernmlops/data_schema/schema.py:248-264`
- Run-dir-aware utility (proves current layout): `python/kernmlops/analysis/collector.py:29-40`

Problems:

- writer uses `data/curated/<benchmark>/<collection_id>/<table>.<chunk>.parquet`
- `read_parquet_dir()` expects `data/curated/<table>/*.parquet`
- `CollectionData.from_data()` expects table-named directories and filenames starting with `collection_id`
- current filenames are `<table>.<chunk>.parquet`, so `x.name.startswith(collection_id)` is false
- `CollectionData.from_data()` also asserts `len(dfs) <= 1`, but chunked output can create many parquet files per table

Impact:

- `collect dump` / `collect graph` can appear to show missing data or no data
- actual collected data may exist on disk but be invisible to those commands

Empirical validation:

- `data_import.read_parquet_dir(Path("data/curated"))` raises `ValueError: cannot concat empty list`
- `CollectionData.from_data(Path("data/curated"), <existing_collection_id>, data_schema.table_types)` raises `AssertionError` (no `system_info` loaded due layout/lookup mismatch)

### 7. `data_schema.table_types` omits many tables that hooks emit (THP/trace/harness data becomes invisible to graph/load path)

Files:

- Registry used by graph loader: `python/kernmlops/data_schema/__init__.py:25-36`
- THP tables exist: `python/kernmlops/data_schema/thp_harness.py:44`, `:197`, `:229`, `:270`, `:297`

Omitted examples:

- `thp_scan_events`, `thp_collapse_events`, etc. (THP trace)
- `vmstat_samples`
- `smaps_rollup_samples`, `smaps_vma_samples`
- `thp_candidates`, `thp_interventions`
- `process_trace`, `mm_rss_stat`, `zswap_runtime`, `madvise`, `unmap_range`, `cbmm_*` (and others)

Impact:

- even if data files exist, `CollectionData.from_data(..., table_types=data_schema.table_types)` will not load these tables
- graphs and downstream `CollectionData` consumers will appear incomplete

### 8. Silent event drops: handlers swallow exceptions and no lost-event callbacks are registered

Files:

- perf silent drop: `python/kernmlops/data_collection/bpf_instrumentation/perf/perf_hook.py:167-173`
- file_data silent drop: `python/kernmlops/data_collection/bpf_instrumentation/file_data_hook.py:82-96`
- perf buffer open without lost callback: `python/kernmlops/data_collection/bpf_instrumentation/perf/perf_hook.py:125-128`

Observations:

- multiple hooks use perf buffers but do not install lost-event callbacks
- overflows can happen under load (especially perf at 1000 Hz per event per CPU)
- handler exceptions are swallowed with `pass`, so data loss is invisible

Impact:

- intermittent missing samples with no warning
- debugging becomes difficult because the collector reports success

This is a high-probability contributor when workloads are noisy/high-rate.

### 9. `output_graphs` graphs only the last in-memory chunk after periodic flushing, not the full run

Files:

- periodic flushing clears hook buffers: `python/kernmlops/cli/collect.py:119-128`, `:162-165` in hooks generally
- graphing uses only final `collection_tables`: `python/kernmlops/cli/collect.py:246-281`

What happens:

- periodic output thread flushes and clears hook buffers throughout run
- final `collection_tables` only contains system info + data since last periodic flush
- `collection_data.graph()` is called on that subset

Impact:

- generated graphs can look incomplete / truncated
- users may interpret this as missing collection

Disk files may be complete (modulo race bugs), but in-process graphing is not.

### 10. THP harness intervention is linked to the wrong `decision_id` when stratified target differs from sampled target

Files:

- sampled candidate emission: `python/kernmlops/data_collection/bpf_instrumentation/smaps_harness.py:432-443`
- stratified intervention target selection: `python/kernmlops/data_collection/bpf_instrumentation/smaps_harness.py:452-457`
- intervention row stores passed `decision_id`: `python/kernmlops/data_collection/bpf_instrumentation/smaps_harness.py:373-387`

What happens:

- code emits one `sampled` candidate and stores its `decision_id`
- intervention may target a different region selected by `_select_stratified_region()`
- intervention row still stores the sampled candidate’s `decision_id`

Impact:

- mislabeled intervention-to-candidate linkage
- downstream matching/training data can be corrupted
- analysis may report inconsistent intervention outcomes

This is a data correctness bug (not raw sample loss), but important for THP harness research results.

## Additional Bugs / Data Quality Issues (Lower Priority but Real)

### 11. `memory_usage` hook scales hugepage counts by 1024 (unit bug)

File:

- `python/kernmlops/data_collection/bpf_instrumentation/memory_usage_hook.py:57-61`

`HugePages_Total`, `HugePages_Free`, and `HugePages_Rsvd` are counts, not kB. The code multiplies them by `1024`, which makes these fields numerically wrong.

Impact:

- incorrect `memory_usage` table values for hugepage counts
- misleading graphs/analysis

### 12. `read_parquet_dir()` crashes on empty filtered directories

File:

- `python/kernmlops/data_import/__init__.py:14-22`

If `benchmark_name` filtering leaves `dfs = []`, `pl.concat([])` raises. This is secondary to the layout mismatch, but still a bug.

## Why Missing Data Feels Intermittent (Root-Cause Synthesis)

The intermittent nature is explained by a combination of:

- unsynchronized `poll()` vs `pop_data()` list access (timing-sensitive)
- periodic output thread not stopping on natural benchmark completion
- no lost-event accounting for perf buffers
- silent exception swallowing in event handlers

This combination means:

- some runs look fine
- others lose samples near flush boundaries or under load
- collector rarely surfaces a clear error

## Recommended Fix Order (Pragmatic)

1. Fix collector thread lifecycle and synchronization in `cli/collect.py`
2. Add explicit lost-event callbacks and logging for perf buffers
3. Register `THPTraceBPFHook` and error on unknown hook names
4. Unify writer/reader layout (or provide a run-dir-aware `CollectionData.from_run_dir`)
5. Expand `data_schema.table_types` to include active hook outputs (especially THP harness/trace)
6. Fix THP harness config YAML nesting (`hugepage_harness` top-level)
7. Fix THP harness intervention `decision_id` linkage
8. Fix unit bug in `memory_usage`

## Notes on Repo Specificities

- The repo currently contains both legacy collection/graphing assumptions and newer run-dir / THP harness flows. Some commands and utilities target different on-disk layouts.
- There is a substantial THP-focused experimentation branch in the current tree (new config/schema/harness files), and several integration bugs come from partial wiring between those additions and the older core framework.
- The `module/` subtree and provisioning scripts are extensive but largely orthogonal to the intermittent user-space collection loss issue traced here.

## Reproducible Instrumentation Demo (Use Existing `memory_usage` Hook)

No new instrumentation is required for this. The repo already has:

- collector hook: `python/kernmlops/data_collection/bpf_instrumentation/memory_usage_hook.py`
- schema + graph definition: `python/kernmlops/data_schema/memory_usage.py`

The correct path is to use the normal `collect data` / `collect graph` flow with only the `memory_usage` hook enabled.

### Minimal Config (memory usage only)

Create a temporary config (for example `memory_usage_only.yaml`) with a minimal collector setup.

```yaml
---
benchmark_config:
  generic:
    benchmark: faux
    skip_clear_page_cache: true
    transparent_hugepages: no_change
    overcommit_memory: no_change
  faux: {}
collector_config:
  generic:
    poll_rate: 0.25
    output_dir: data
    output_dfs: false
    output_graphs: false
    hooks:
      - memory_usage
```

Notes:

- `faux` is appropriate here because it lets you sample the running system until you stop it.
- The generic benchmark settings above avoid privileged kernel writes during `benchmark.setup()`.

### Exact Native CLI Steps

From repo root:

```bash
source .venv/bin/activate
python python/kernmlops collect data -c memory_usage_only.yaml -p memory-usage-demo
```

What happens:

- collection starts with only the existing `memory_usage` hook
- it prints `Hit Ctrl+C to terminate...` (because `faux` never self-terminates)
- let it run for ~10-30 seconds
- press `Ctrl+C`
- the command prints the `collection_id`

Then graph that collection:

```bash
python python/kernmlops collect graph \
  -d data/curated \
  -c <collection_id> \
  -o data/graphs \
  --matplot
```

Expected graph content from `MemoryUsageTable` (`python/kernmlops/data_schema/memory_usage.py`):

- `cached_bytes`
- `anon_pages_total_bytes`
- `anon_hugepages_total_bytes`
- `mapped_total_bytes`
- `shmem_total_bytes`

### Environment Requirement (Important)

In this environment, the normal `kernmlops` import path currently fails before collection because `bcc` is not installed and the package eagerly imports BPF modules. Once `bcc` is installed, the native CLI flow above is the right way to collect and graph `memory_usage` data.

## `collect graph` Failure on Fresh Collections (Problem Statement + Fix Plan)

### Problem Summary

`collect data` can successfully write a new collection, but `collect graph` fails with:

- `AssertionError` in `CollectionData(...)`

This happens even when the collection contains valid parquet files such as:

- `system_info.end.parquet`
- `memory_usage.end.parquet`

### Root Cause (What Is Broken)

There is a data layout mismatch between the current writer and the graph loader:

- The collector writes data in a per-run directory layout (`data/curated/<benchmark>/<collection_id>/...`)
- The graph loader reads from a legacy per-table directory layout (`data/curated/<table_name>/<collection_id>*.parquet`)

Because of that mismatch, `collect graph` does not find `system_info`, and `CollectionData` asserts when it expects that table to exist.

### User Impact

- Users can collect data but cannot graph it with the documented CLI flow
- The error message (`AssertionError`) does not clearly explain the actual problem
- This makes the `memory_usage` demo and other hook workflows look broken even when collection succeeded

### Fix Plan (High-Level, No Implementation Details)

1. Make the graph loading path support the current run-directory layout used by `collect data`
2. Keep backward compatibility with the legacy table-directory layout if older datasets still exist [NO NEED for backwards compability]
3. Improve error handling so missing `system_info` reports a clear, actionable message instead of a bare assertion [Make it readable]
4. Add a small regression test covering:
   - successful load/graph from current run layout
   - graceful failure with a clear message when required tables are missing
   [Just do the test yourself]
5. Update docs/examples so `collect data` and `collect graph` instructions match the actual on-disk layout

### Acceptance Criteria

- A collection produced by `collect data` can be graphed immediately by `collect graph` without manual file copying
- The command works for `memory_usage` demo collections
- Failures report explicit reasons (for example, missing `system_info`) instead of `AssertionError`

## `output_graphs` Crash: `system_info` In-Memory Table Is Missing `collection_id`

### Symptom

When `collector_config.generic.output_graphs: true`, `collect data` can finish collection but then crash during in-process graph generation with:

- `polars.exceptions.ColumnNotFoundError: "collection_id" not found`

The stack trace typically points to:

- `python/kernmlops/data_schema/memory_usage.py:94` (`graph.name()` uses `self.collection_data.id`)
- `python/kernmlops/data_schema/schema.py:158` (`CollectionData.id`)
- `python/kernmlops/data_schema/schema.py:108` (`SystemInfoTable.id` reads `collection_id`)

### Root Cause

`run_collect()` builds an in-memory `SystemInfoTable` row and immediately calls `CollectionData.from_tables(...)` for optional graphing.

However, the in-memory `system_info` DataFrame created in `run_collect()` does **not** include a `collection_id` column.

Relevant code path:

- `python/kernmlops/cli/collect.py:245-257` builds `SystemInfoTable.from_df(system_info.with_columns([...]))`
- the added columns include:
  - `collection_time_sec`
  - `collection_pid`
  - `benchmark_name`
  - `hooks`
- but **not** `collection_id`

Later, graphing code assumes `system_info` always has `collection_id`:

- `python/kernmlops/data_schema/schema.py:107-109` (`SystemInfoTable.id`)

This mismatch causes graph generation to fail even though collection itself succeeded.

### Why This Happens Even After the Run-Layout Loader Fix

The run-layout graph loader fix addresses `collect graph` reading data back from disk.

This bug is different:

- it happens in the same `collect data` process
- it uses `CollectionData.from_tables(collection_tables)` directly
- it never goes through `CollectionData.from_data(...)`

So the loader-side `collection_id` injection does not help this path.

### Impact

- `collect data` crashes at the end when `output_graphs` is enabled
- users may incorrectly think the instrumentation or graph schema is broken
- collected parquet files may already be written successfully, but the command exits with an error

### Fix (What To Change)

Add `collection_id` to the in-memory `system_info` table in `run_collect()` when constructing the `SystemInfoTable`.

Concretely, in `python/kernmlops/cli/collect.py` where `system_info.with_columns([...])` is built, include:

- `pl.lit(collection_id).alias("collection_id")`

This keeps the in-memory path consistent with what `SystemInfoTable.id` and all graph code expect.

### Expected Result After Fix

- `collect data` with `output_graphs: true` completes successfully
- in-process graph generation works without `ColumnNotFoundError`
- `memory_usage` and other graph types that use `collection_data.id` can render normally
