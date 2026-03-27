# Task B: Last 6 Commits on `main` and Comparison to `messy-dir`

## Commits reviewed

1. `2449a0ea1730e33df60a7a4cbcb96a082eaab39e` `Add figures`
2. `1314040c60809c638c011bd46532d4065adebf1d` `Update numpy version`
3. `a8970ac3212ce8a610c3f8a3544c1598b356423a` `Add idle page outputs to ignore`
4. `6e545c1d6af495deff22deee2c6338ebe79bc2e5` `Add idle page plot script`
5. `45a030eaf6efaf97e509c79499408a1d5a2b44b0` `Add pypy idle page tracking script`
6. `d7dad1f5681bfe52b8869a0f1e6a87092670c1e7` `Add outer repeat tracking`

## What each commit does

### 1. `d7dad1f` `Add outer repeat tracking`

File changed:

- `python/kernmlops/analysis/thp_runtimes.py`

Effect:

- changes the runtime-analysis script so it combines load/run pairs into total runtime samples
- prints sample count and summary stats
- adds an explicit “plotted boxplot.png” message

This commit is unrelated to idle-page tracking logic itself.

### 2. `45a030e` `Add pypy idle page tracking script`

File added:

- `python/kernmlops/data_collection/track_idle_accesses.py`

Effect:

- adds a standalone PID-wide idle-page tracker
- scans `/proc/<pid>/maps`
- walks `/proc/<pid>/pagemap`
- marks PFNs idle in `/sys/kernel/mm/page_idle/bitmap`
- polls access state repeatedly
- accumulates per-PFN access counts
- writes final output as parquet or csv.gz

This is the actual core idle-page tracking implementation on `main`.

### 3. `6e545c1` `Add idle page plot script`

File added:

- `python/kernmlops/analysis/idle_accesses.py`

Effect:

- adds plotting for the standalone tracker’s output
- supports histogram and rank-frequency line plots
- consumes `accesses.parquet` or `accesses.csv.gz`

### 4. `a8970ac` `Add idle page outputs to ignore`

File changed:

- `.gitignore`

Effect:

- ignores:
  - `accesses.parquet`
  - `accesses.csv.gz`
  - `perf.data`
  - `.pypy-venv/`

This is cleanup for the standalone workflow.

### 5. `1314040` `Update numpy version`

File changed:

- `pyproject.toml`

Effect:

- adds explicit `numpy>=1.26.0`

This supports the plotting script on `main`.

### 6. `2449a0e` `Add figures`

Files added or moved:

- `figures/10_idle_accesses.png`
- `figures/100_idle_accesses.png`
- `figures/1_000_idle_accesses.png`
- `figures/10_000_idle_accesses.png`
- `figures/runtime_variability.png`
- moved `redis_bloat_comparison.png` into `figures/`

Effect:

- adds the generated paper/report figures for the runtime and idle-page analysis workflow

## What these six commits achieve as a whole

As a set, these commits add a **standalone idle-page analysis workflow** on `main`:

1. collect per-PFN idle/access counts for a Redis PID
2. save the final aggregated counts
3. plot the distribution / rank-frequency of those final counts
4. ignore generated outputs
5. include the resulting figures in the repo

This is not integrated into the main collector pipeline. It is a separate script-driven workflow.

## Is this the same approach as `messy-dir`?

### Core kernel mechanism

Yes, the core kernel mechanism is the same:

- `/proc/<pid>/pagemap`
- `/sys/kernel/mm/page_idle/bitmap`
- optionally `/proc/kpageflags`

So at the kernel-interface level, both are doing the same kind of idle-page tracking.

### Integration and scope

No, the two branches are not the same design.

`main`:

- standalone script
- tracks many pages for a PID at once
- operates over heap/anonymous VMAs
- stores final aggregate access counts per PFN
- good for whole-process page ranking
- does **not** save a per-page time series

`messy-dir`:

- integrated into the collector as the `vaptr` hook
- tracks only the sampled Redis object pages returned by `VAPTR`
- stores timestamps and per-sample rows in `vaptr.end.parquet`
- good for a small sampled cohort over time
- not a whole-process tracker

## Did you do the “BPF hook method” or Gaurav’s method?

The accurate answer is:

- you wrapped the sampling inside a collector hook, so from a repo-architecture point of view it is a hook-based integration
- but the actual access measurement is **not** an eBPF page-access tracker
- it is still the same pagemap + page_idle userspace technique Gaurav used

So the best description is:

**your branch is Gaurav’s idle-page method embedded inside your `vaptr` collector hook path**

## Which is better for your stated goal?

Your stated goal:

- collect data about a process’s pages
- rank pages by accesses
- ideally see page-rank behavior over time

### Better for whole-process page coverage

`main` is closer.

Why:

- it tracks PFNs across the target process’s heap/anonymous VMAs
- it is not limited to 10 sampled Redis keys

### Better for per-sample time series on a small cohort

`messy-dir` is closer.

Why:

- it stores one row per key per poll with timestamps in `vaptr.end.parquet`

### Less buggy right now

`main` is less buggy in one important sense:

- it does not call `VAPTR` immediately before reading the idle state
- so it avoids the extra self-interference bug present in `messy-dir`

But both approaches still depend on the same kernel idle-bit mechanism, and on this host I observed a more basic problem:

- even a frozen anonymous page came back “accessed” immediately after being marked idle

So the host-level reliability issue affects both branches.

## Bottom line

If you want:

- **whole-process page ranking**, start from `main`
- **sampled Redis object pages over time**, start from `messy-dir`

If you want:

- **whole-process page ranking over time**

neither branch fully gives you that today:

- `main` has breadth but only final aggregates
- `messy-dir` has time series but only for sampled Redis object pages

That is the clearest difference between the two designs.
