# GUPS Split Harness Verification

## Summary

This change adds a deterministic GUPS selective-split harness that keeps the
existing multithreaded always-vs-never sanity path intact, then layers a new
single-thread seeded path on top for exact page targeting.

The new flow works end to end on this host:

- calibration wrote a deterministic page-summary CSV
- direct benchmark validation wrote a split-events CSV and recorded a
  successful `split_thp(pid, vaddr)` call inside GUPS
- the full breakpoint matrix ran in `tmux`
- the renderer wrote the HTML dashboard, PNGs, and `config.md`

## What Changed

### Benchmark and collector support

- added a repo-managed `benchmark/gups/` source tree and setup script
- added `python/kernmlops/kernmlops_benchmark/gups.py`
- registered the benchmark in `python/kernmlops/kernmlops_benchmark/__init__.py`
- added `smaps_rollup_hook` so the always-vs-never sanity benchmark still has a
  cheap process-local THP measurement path

### Deterministic split harness

- extended `benchmark/gups/gups.c` with:
  - `--stream-seed`
  - `--page-summary-out`
  - `--split-schedule`
  - `--split-events-out`
  - in-benchmark `split_thp(getpid(), table_base + page_index * 2 MiB)` calls
  - split summary fields in `gups_results.jsonl`
- added `python/kernmlops/replay/system_tuning.py` and reused it from
  `replay_trace.py` and the new GUPS runner
- added:
  - `python/kernmlops/replay/generate_gups_breakpoints.py`
  - `python/kernmlops/replay/replay_gups.py`
  - `temp_data_analysis/render_gups_split_harness.py`

### Why this works better than the Redis path for GUPS

Redis needs `VAPTR` because the target object address lives inside Redis
internals. GUPS owns the table allocation directly, so the benchmark can map a
stable table-local page index to `table_base + page_index * 2 MiB` on its own.

That lets the harness:

- stay deterministic with a seeded update stream
- avoid a Redis-style address-resolution module
- record split attempts and outcomes directly at the point where the split is
  triggered

## Validation

### Install step

Benchmark install command:

```bash
cd /users/SahilSC/HugePageResearch-gups-harness
scripts/setup-benchmarks/setup-gups.sh
```

Installed binary:

- `/users/SahilSC/kernmlops-benchmark/gups/gups`

### Direct calibration

Inputs and outputs:

- results: `temp_data_analysis/gups_split_harness_20260421/raw/direct_calibration_results.jsonl`
- page summary: `temp_data_analysis/gups_split_harness_20260421/raw/direct_calibration_page_summary.csv`

Command shape:

```bash
cd /users/SahilSC/HugePageResearch-gups-harness
sudo -E HOME=$HOME PATH="$PATH" PYTHONPATH=python/kernmlops \
  /users/SahilSC/HugePageResearch/.venv/bin/python - <<'PY'
from pathlib import Path
import subprocess
from replay.system_tuning import setup_system, teardown_system

base = Path("temp_data_analysis/gups_split_harness_20260421/raw")
config = setup_system()
try:
    subprocess.check_call([
        "/users/SahilSC/kernmlops-benchmark/gups/gups",
        "--results", str(base / "direct_calibration_results.jsonl"),
        "--table-size-mib", "64",
        "--repeats", "1",
        "--updates-multiplier", "4",
        "--threads", "1",
        "--stream-seed", "7",
        "--page-summary-out", str(base / "direct_calibration_page_summary.csv"),
    ])
finally:
    teardown_system(config)
PY
```

Observed direct calibration result:

- `repeat=0 runtime_s=0.754129`
- `gups=0.044494`
- `verification_passed=true`

### Breakpoint generation

Input:

- `temp_data_analysis/gups_split_harness_20260421/raw/direct_calibration_page_summary.csv`

Output:

- `temp_data_analysis/gups_split_harness_20260421/raw/direct_breakpoints.parquet`

Command:

```bash
cd /users/SahilSC/HugePageResearch-gups-harness
PYTHONPATH=python/kernmlops /users/SahilSC/HugePageResearch/.venv/bin/python \
  python/kernmlops/replay/generate_gups_breakpoints.py \
  temp_data_analysis/gups_split_harness_20260421/raw/direct_calibration_page_summary.csv \
  --output temp_data_analysis/gups_split_harness_20260421/raw/direct_breakpoints.parquet \
  --hot-pages 2 \
  --random-rows 1 \
  --random-seed 0
```

Generated rows:

- `base_pages`
- `no_break`
- `hot page #1`
- `hot page #2`
- `random page #1`

### Direct split validation

This was the fail-fast benchmark-only check outside `collect` and outside the
matrix runner.

Artifacts:

- schedule: `temp_data_analysis/gups_split_harness_20260421/raw/direct_split_schedule.csv`
- results: `temp_data_analysis/gups_split_harness_20260421/raw/direct_split_results.jsonl`
- split events: `temp_data_analysis/gups_split_harness_20260421/raw/direct_split_events.csv`

Observed split-event row:

- `page_index=0`
- `page_access_count=1`
- `success=1`
- `attempts=1`
- `anon_hugepages_kb_before=65536`
- `anon_hugepages_kb_after=63488`
- `split_wall_us=505`

Observed benchmark result:

- `split_events=1`
- `split_successes=1`
- `split_failures=0`

### Full matrix run

`tmux` session:

- `gups-split-20260421`

Matrix command:

```bash
cd /users/SahilSC/HugePageResearch-gups-harness
tmux new-session -d -s gups-split-20260421 \
  'cd /users/SahilSC/HugePageResearch-gups-harness && sudo -E HOME=$HOME PATH="$PATH" PYTHONPATH=python/kernmlops \
  /users/SahilSC/HugePageResearch/.venv/bin/python python/kernmlops/replay/replay_gups.py \
  --breakpoints temp_data_analysis/gups_split_harness_20260421/raw/direct_breakpoints.parquet \
  --output temp_data_analysis/gups_split_harness_20260421/raw/gups_split_results.parquet \
  --artifacts-dir temp_data_analysis/gups_split_harness_20260421/raw/gups_split_artifacts \
  --table-size-mib 64 \
  --repeats 4 \
  --updates-multiplier 4 \
  --stream-seed 7 \
  --runs 3 \
  --collector-config config/replay_collectors_dtlb.yaml \
  -v |& tee temp_data_analysis/gups_split_harness_20260421/raw/gups_split_runtime.log'
```

Polling:

- initial poll: runner started in `base_pages`
- mid-run poll: runner had reached `hot page #1`
- later poll: runner had reached `random page #1`
- final check: `tmux` session exited normally and the result parquet existed

Run outputs:

- runtime log: `temp_data_analysis/gups_split_harness_20260421/raw/gups_split_runtime.log`
- results parquet: `temp_data_analysis/gups_split_harness_20260421/raw/gups_split_results.parquet`
- metadata json: `temp_data_analysis/gups_split_harness_20260421/raw/gups_split_results.metadata.json`
- per-run artifacts dir: `temp_data_analysis/gups_split_harness_20260421/raw/gups_split_artifacts`
- per-run command log: `temp_data_analysis/gups_split_harness_20260421/raw/gups_split_artifacts/run_commands.log`

## Dashboard

Rendered outputs:

- folder: `temp_data_analysis/gups_split_harness_20260421/`
- config: `temp_data_analysis/gups_split_harness_20260421/config.md`
- html: `temp_data_analysis/gups_split_harness_20260421/gups_split_harness_20260421.html`
- runtime graph: `temp_data_analysis/gups_split_harness_20260421/runtime_mean_std.png`
- gups graph: `temp_data_analysis/gups_split_harness_20260421/gups_mean_std.png`
- dTLB loads graph: `temp_data_analysis/gups_split_harness_20260421/dtlb_loads_mean_std.png`
- dTLB misses graph: `temp_data_analysis/gups_split_harness_20260421/dtlb_misses_mean_std.png`

## Results

Matrix configuration:

- table size: `64 MiB`
- repeats per benchmark invocation: `4`
- runs per breakpoint row: `3`
- stream seed: `7`
- hottest split rows: `2`
- random split rows: `1`

Observed row summary:

- `base_pages`
  - runtime mean `0.745417 s`
  - GUP/s mean `0.045080`
  - dTLB loads mean `587431887.333`
  - dTLB misses mean `239242923.667`
- `no_break`
  - runtime mean `0.378502 s`
  - GUP/s mean `0.088701`
  - dTLB loads mean `560480273.333`
  - dTLB misses mean `12082.333`
- `(3) hot page #1`
  - runtime mean `1.137102 s`
  - GUP/s mean `0.029538`
  - split successes `3`
  - split max attempts `2`
- `(3) hot page #2`
  - runtime mean `1.135138 s`
  - GUP/s mean `0.029593`
  - split successes `3`
  - split max attempts `2`
- `(3) random page #1`
  - runtime mean `1.131903 s`
  - GUP/s mean `0.029681`
  - split successes `3`
  - split max attempts `2`

Key takeaways:

- intact THP (`no_break`) beat `base_pages` by about `1.97x` in GUP/s on this
  small deterministic run
- all three split-only rows successfully split in all three reruns
- once the hottest/random page was split, throughput dropped well below the
  intact-THP row

## Tradeoffs

Pros:

- exact page targeting without Redis-specific address resolution
- deterministic seeded access stream
- preserved per-run artifacts for every matrix row
- dashboard explicitly reports split-success counts and max retry counts

Cons:

- v1 is intentionally `threads=1` only for the selective-split path
- the direct replay counters in `replay_gups.py` cover the whole benchmark
  process lifetime, not just the timed update loop
- the current generator emits split-only single-page rows only

