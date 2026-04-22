# Replay Experiment Runbook

This runbook matches the current replay workflow in this repository.

The default assumption is that you are working inside your newest existing
Docker container at `/KernMLOps`. Host commands are mostly just helpers for
entering that container or launching a long container-side run from `tmux`.

The replay flow is:

1. capture one Redis snapshot and one MONITOR log
2. generate one breakpoint matrix from that captured log
3. replay the same snapshot/log pair many times
4. compare rows inside the same replay environment
5. generate analysis files and dashboards from the result parquet files

The most important fairness rule is:

- capture once
- preserve that `snapshot.rdb` and `monitor_run.log`
- reuse those same two files for every row and every rerun you want to compare

## What Replay Means In This Repo

Replay restores a captured Redis snapshot, replays a captured MONITOR log, and
repeats that process for many breakpoint rows.

When replay stages the preserved snapshot back into Redis's active `dump.rdb`
path, it uses a hard link and fails fast if that link cannot be created. Replay
does not silently switch to a different restore path.

The supported replay entrypoints are:

- `python/kernmlops/replay/capture_redis_trace.sh`
- `python/kernmlops/replay/generate_breakpoints.py`
- `python/kernmlops/replay/replay_trace.py`
- `python/kernmlops/replay/generate_gups_breakpoints.py`
- `python/kernmlops/replay/replay_gups.py`

The replay entrypoints read Redis bind and port from `config/redis.conf`.
`replay_trace.py` no longer takes separate `--host` or `--port` flags.

The GUPS split harness is host-native rather than container-first. It uses the
repo-managed benchmark binary installed by `scripts/setup-benchmarks/setup-gups.sh`.

The captured inputs are:

- `data/redis_traces/snapshot.rdb`
- `data/redis_traces/monitor_run.log`

The replay output is a parquet file where:

- each row is one breakpoint configuration
- `row_index` stores the replay row position
- `thp_mode` stores the restore-time THP mode used for that row
- each `break_dispatch_N` column stores how run `N` dispatched replay-time splits (`inline` or `threaded`)
- each `runtime_s_N` column is one timed replay of that same row
- each `commands_replayed_N` column stores the successful command count for that run
- each `split_events_N` column stores how many replay-time split triggers fired
- each `split_successes_N` column stores how many split triggers succeeded
- each `split_failures_N` column stores how many split triggers failed, including
  zero-attempt failures where `VAPTR` no longer resolved a direct pointer for
  that key by the time replay tried to break it
- each `split_syscall_attempts_N` column stores total split syscall attempts for that run
- each `split_max_attempts_N` column stores the largest attempt count used by any one split event
- each `split_total_wall_ms_N` column stores total replay-time split wall time for run `N`
- each `split_max_wall_ms_N` column stores the slowest one-event split wall time for run `N`
- each `split_queue_lag_ms_mean_N` column stores the mean threaded queue lag before split work started
- each `split_queue_lag_ms_max_N` column stores the worst threaded queue lag before split work started
- hardware runs can also append `dtlb_loads_N` and `dtlb_misses_N`

`break_dispatch_N` is especially important for the new inline-versus-threaded
comparison work:

- `inline` means replay breaks the target THP on the hot path before the
  triggering Redis command executes
- `threaded` means replay enqueues the split request to a dedicated worker
  thread and continues executing the Redis command immediately
- `mixed` is a CLI convenience mode that requires an even `--runs`; replay maps
  the first half of runs to `inline` and the second half to `threaded`

## Row Semantics

### `base_pages` (first row)

The first breakpoint row is the sentinel baseline.

Today the replay generator emits that first row as an all-zero row, but replay
itself keys off row position, not row contents.

Replay does not interpret it as "split every tracked key."

Instead replay:

- sets THP `enabled` to `never`
- restores the snapshot under that THP mode
- replays the trace with an empty effective breakpoint map

That means Redis allocates base pages from startup for this row.

### `no_break` (all-max row)

The all-max row keeps every key at `access_count + 1`, which means replay never
fires a split for any key.

Replay:

- sets THP `enabled` to `always`
- restores the snapshot
- replays with no splits triggered

This is the intact-THP baseline.

### `split_only:<key>`

These rows split one chosen key before its first access and leave every other
tracked key unsplit.

Replay:

- sets THP `enabled` to `always`
- restores the snapshot
- uses the per-key breakpoint map normally

### Random single-key rows

Random rows pick a few non-hot keys and turn each one into a split-only row.

Each random row:

- chooses one random remaining key
- sets that key to `0`
- keeps every other key at `access_count + 1`

Use them when you want a few non-handpicked single-key rows beside the hot-key
rows.

## Capture Script

The capture script is:

- `python/kernmlops/replay/capture_redis_trace.sh`

It reads the Redis bind and port from `config/redis.conf` before building the
YCSB Redis client parameters.

Current default environment values in that script are:

- `RECORD_COUNT=4096`
- `OPERATION_COUNT=4096`
- `READ_PROPORTION=0.05`
- `UPDATE_PROPORTION=0.00`
- `SCAN_PROPORTION=0.00`
- `INSERT_PROPORTION=0.00`
- `READMODIFYWRITE_PROPORTION=0.00`
- `DELETE_PROPORTION=0.95`
- `fieldlength=2097152`
- `requestdistribution=zipfian`
- `threadcount=16`

Current supported overrides are:

- `RECORD_COUNT=...`
- `OPERATION_COUNT=...`
- `READ_PROPORTION=...`
- `UPDATE_PROPORTION=...`
- `SCAN_PROPORTION=...`
- `INSERT_PROPORTION=...`
- `READMODIFYWRITE_PROPORTION=...`
- `DELETE_PROPORTION=...`
- `-d/--ycsb-dir`

The six operation proportions must sum to exactly `1.0`. The script now fails
fast before starting YCSB if the configured mix is invalid.

The capture script now moves the finished `dump.rdb` into
`data/redis_traces/snapshot.rdb` instead of copying it, so longer captures do
not keep two full snapshot copies on disk at once.

## Canonical Replay Entrypoints

The canonical replay entrypoints are:

- `python/kernmlops/replay/capture_redis_trace.sh`
- `python/kernmlops/replay/generate_breakpoints.py`
- `python/kernmlops/replay/replay_trace.py`
- `python/kernmlops/replay/generate_gups_breakpoints.py`
- `python/kernmlops/replay/replay_gups.py`

Use those paths directly.

## Deterministic GUPS Split Harness

The deterministic GUPS path does not replay an external trace in v1.

Instead it:

1. runs the seeded GUPS benchmark once in calibration mode
2. writes one page-summary CSV for the 2 MiB table regions touched during the
   timed update pass
3. turns that CSV into a breakpoint matrix
4. reruns the benchmark under `base_pages`, `no_break`, and split-only rows
5. renders one dashboard from the result parquet plus preserved run artifacts

The supported GUPS entrypoints are:

- `python/kernmlops/replay/generate_gups_breakpoints.py`
- `python/kernmlops/replay/replay_gups.py`
- `temp_data_analysis/render_gups_split_harness.py`

### GUPS Row Semantics

`generate_gups_breakpoints.py` emits the same conceptual row kinds as the Redis
generator, but the split trigger is page-local rather than key-local.

`base_pages`:

- first row
- runner sets THP `enabled` to `never`
- runner does not write a split schedule

`no_break`:

- second row
- runner sets THP `enabled` to `always`
- runner does not write a split schedule

`split_only` rows:

- runner sets THP `enabled` to `always`
- runner writes one split-schedule CSV row per repeat
- the current generator uses `break_after_page_accesses=1` so the page has been
  faulted once before the in-benchmark `split_thp(pid, vaddr)` call

### GUPS Calibration

Install the benchmark on the host first:

```bash
cd ~/HugePageResearch
scripts/setup-benchmarks/setup-gups.sh
```

Then run a small seeded calibration with THP forced on and quiet background
memory management:

```bash
cd ~/HugePageResearch
sudo -E HOME=$HOME PATH="$PATH" PYTHONPATH=python/kernmlops \
  .venv/bin/python - <<'PY'
from pathlib import Path
import subprocess
from replay.system_tuning import setup_system, teardown_system

base = Path("temp_data_analysis/gups_split_harness_example/raw")
base.mkdir(parents=True, exist_ok=True)
config = setup_system()
try:
    subprocess.check_call([
        str(Path.home() / "kernmlops-benchmark" / "gups" / "gups"),
        "--results", str(base / "calibration_results.jsonl"),
        "--table-size-mib", "64",
        "--repeats", "1",
        "--updates-multiplier", "4",
        "--threads", "1",
        "--stream-seed", "7",
        "--page-summary-out", str(base / "calibration_page_summary.csv"),
    ])
finally:
    teardown_system(config)
PY
```

### Generate A GUPS Breakpoint Matrix

```bash
cd ~/HugePageResearch
PYTHONPATH=python/kernmlops .venv/bin/python \
  python/kernmlops/replay/generate_gups_breakpoints.py \
  temp_data_analysis/gups_split_harness_example/raw/calibration_page_summary.csv \
  --output temp_data_analysis/gups_split_harness_example/raw/breakpoints.parquet \
  --hot-pages 10 \
  --random-rows 3 \
  --random-seed 0
```

### Run The GUPS Matrix In `tmux`

```bash
cd ~/HugePageResearch
tmux new-session -d -s gups-split-example \
  'cd ~/HugePageResearch && sudo -E HOME=$HOME PATH="$PATH" PYTHONPATH=python/kernmlops \
  .venv/bin/python python/kernmlops/replay/replay_gups.py \
  --breakpoints temp_data_analysis/gups_split_harness_example/raw/breakpoints.parquet \
  --output temp_data_analysis/gups_split_harness_example/raw/gups_split_results.parquet \
  --artifacts-dir temp_data_analysis/gups_split_harness_example/raw/gups_split_artifacts \
  --table-size-mib 64 \
  --repeats 4 \
  --updates-multiplier 4 \
  --stream-seed 7 \
  --runs 3 \
  --collector-config config/replay_collectors_dtlb.yaml \
  -v |& tee temp_data_analysis/gups_split_harness_example/raw/gups_split_runtime.log'
```

This writes:

- one result parquet
- one metadata JSON beside that parquet
- one `run_commands.log`
- one per-row/per-run artifact tree containing `gups_results.jsonl`, stdout
  logs, split schedules, and split-event CSVs

### Render The GUPS Dashboard

```bash
cd ~/HugePageResearch
PYTHONPATH=python/kernmlops .venv/bin/python \
  temp_data_analysis/render_gups_split_harness.py \
  --results temp_data_analysis/gups_split_harness_example/raw/gups_split_results.parquet \
  --metadata temp_data_analysis/gups_split_harness_example/raw/gups_split_results.metadata.json \
  --output-dir temp_data_analysis/gups_split_harness_example \
  --label example
```

The renderer writes:

- `runtime_mean_std.png`
- `gups_mean_std.png`
- `dtlb_loads_mean_std.png`
- `dtlb_misses_mean_std.png`
- `config.md`
- `gups_split_harness_<label>.html`

The HTML and `config.md` both preserve the exact input paths, command-log path,
and any split-only rows that were omitted from the main charts because they
never successfully split.

## Replay CLI Notes

The replay CLI now supports:

- `--break-dispatch inline`
- `--break-dispatch threaded`
- `--break-dispatch mixed`

Example:

```bash
cd /KernMLOps
python python/kernmlops/replay/replay_trace.py \
  data/redis_traces/my_snapshot.rdb \
  data/redis_traces/my_monitor_run.log \
  --breakpoints data/my_breakpoints.parquet \
  --output data/my_results.parquet \
  --runs 8 \
  --break-dispatch mixed \
  -v
```

When `--break-dispatch mixed` is used, `--runs` must be even. Replay fails
fast if you ask for `mixed` dispatch with an odd run count.

## Docker Workflow

Replay should use your newest existing Docker container instead of creating a
new one unless it is gone.

Find the live containers from the host:

```bash
sudo docker ps --format '{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.CreatedAt}}'
```

Enter the newest existing container and stay there for the normal replay flow:

```bash
sudo docker exec -it clever_hofstadter bash
cd /KernMLOps
```

From this point on, prefer the container shell for capture, breakpoint
generation, and runtime-only replay.

If Redis is not already running, start it in the container:

```bash
redis-server ./config/redis.conf --loadmodule ./redis-module/vaptr.so --daemonize yes
redis-cli ping
```

Wait for `PONG` before capturing or replaying.

## Capture One Trace

Capture from inside the container:

```bash
cd /KernMLOps
./python/kernmlops/replay/capture_redis_trace.sh
```

For a longer run shape:

```bash
cd /KernMLOps
RECORD_COUNT=12288 OPERATION_COUNT=245760 ./python/kernmlops/replay/capture_redis_trace.sh
```

For a read-heavy capture:

```bash
cd /KernMLOps
RECORD_COUNT=15000 OPERATION_COUNT=60000 \
READ_PROPORTION=1.0 UPDATE_PROPORTION=0.0 SCAN_PROPORTION=0.0 \
INSERT_PROPORTION=0.0 READMODIFYWRITE_PROPORTION=0.0 DELETE_PROPORTION=0.0 \
./python/kernmlops/replay/capture_redis_trace.sh
```

For an `80%` read / `20%` delete capture:

```bash
cd /KernMLOps
RECORD_COUNT=15000 OPERATION_COUNT=60000 \
READ_PROPORTION=0.8 UPDATE_PROPORTION=0.0 SCAN_PROPORTION=0.0 \
INSERT_PROPORTION=0.0 READMODIFYWRITE_PROPORTION=0.0 DELETE_PROPORTION=0.2 \
./python/kernmlops/replay/capture_redis_trace.sh
```

That writes:

- `data/redis_traces/snapshot.rdb`
- `data/redis_traces/monitor_run.log`

The next capture overwrites `snapshot.rdb` and `monitor_run.log`, so rename
them right away to keep them safe:

```bash
mv data/redis_traces/snapshot.rdb data/redis_traces/my_experiment_snapshot.rdb
mv data/redis_traces/monitor_run.log data/redis_traces/my_experiment_monitor_run.log
```

Use `mv` instead of `cp` — snapshots can be multiple GB and copying wastes disk.

## Generate Breakpoint Matrices

The generator is:

- `python/kernmlops/replay/generate_breakpoints.py`

### Small 5-row verification matrix

This is the fast verification matrix:

```bash
cd /KernMLOps
.venv/bin/python python/kernmlops/replay/generate_breakpoints.py \
  data/redis_traces/monitor_run.log \
  --output data/replay_verification_breakpoints_5rows.parquet \
  --hot-keys 2 \
  --random-rows 1
```

This produces:

- row 1: `base_pages`
- row 2: `no_break`
- row 3-4: split-only hottest keys
- row 5: one random split-only key row

## Runtime-Only Replay

The overnight runtime matrix should stay runtime-only. Do not pass
`--collector-config` when you only want time.

Run the quick verification from inside the container shell:

```bash
cd /KernMLOps
.venv/bin/python python/kernmlops/replay/replay_trace.py \
  data/redis_traces/snapshot.rdb \
  data/redis_traces/monitor_run.log \
  --breakpoints data/replay_verification_breakpoints_5rows.parquet \
  --output data/replay_verification_results_5rows.parquet \
  --runs 1 \
  -v
```

Expected properties:

- the first row logs the THP-disabled `base_pages` restore
- the parquet contains `runtime_s_1`
- the parquet contains `commands_replayed_1`
- the parquet contains `split_events_1`, `split_successes_1`, `split_failures_1`
- the parquet contains `split_syscall_attempts_1` and `split_max_attempts_1`
- the parquet contains no `dtlb_*` columns

If you want to launch the same container-side run from the host without opening
an interactive shell:

```bash
sudo docker exec clever_hofstadter bash -lc 'cd /KernMLOps && .venv/bin/python python/kernmlops/replay/replay_trace.py data/redis_traces/snapshot.rdb data/redis_traces/monitor_run.log --breakpoints data/replay_verification_breakpoints_5rows.parquet --output data/replay_verification_results_5rows.parquet --runs 1 -v'
```

Launch a longer runtime-only replay from the host inside `tmux`:

```bash
tmux new-session -d -s replay_runtime
tmux send-keys -t replay_runtime "sudo docker exec clever_hofstadter bash -lc 'cd /KernMLOps && .venv/bin/python python/kernmlops/replay/replay_trace.py data/redis_traces/replay_3min_22rows_snapshot.rdb data/redis_traces/replay_3min_22rows_monitor_run.log --breakpoints data/replay_3min_breakpoints_22rows.parquet --output data/replay_3min_results_22rows.parquet --runs 3 -v'" C-m
```

For the April 9 overnight runtime-only matrix, the narrow runner is:

```bash
tmux new-session -d -s replay_overnight_20260409
tmux send-keys -t replay_overnight_20260409 "sudo docker exec clever_hofstadter bash -lc 'cd /KernMLOps && .venv/bin/python python/kernmlops/replay/run_overnight_runtime_matrix.py 2>&1 | tee data/replay_overnight_runtime_matrix.log'" C-m
```

That runner performs:

- preflight checks against Redis and `VAPTR`
- one quick verification replay per workload
- one full-size calibration replay per workload
- one full `2-14-2`, `3`-run runtime-only replay per workload
- manifest, HTML dashboard, JSON, and markdown outputs after each full replay

## Runtime + dTLB Replay

Replay counter collection is enabled with `--collector-config`. That path uses
direct `perf_event_open` counters and currently requires `sudo/root`.

Prefer to run a small dTLB smoke from inside the container if that container has
the needed perf permissions:

```bash
cd /KernMLOps
sudo .venv/bin/python python/kernmlops/replay/replay_trace.py \
  data/redis_traces/snapshot.rdb \
  data/redis_traces/monitor_run.log \
  --breakpoints data/replay_smoke_breakpoints_5rows.parquet \
  --output data/replay_smoke_dtlb_results_5rows.parquet \
  --runs 1 \
  --collector-config config/replay_collectors_dtlb.yaml \
  -v
```

Expected properties:

- the first row still logs the THP-disabled `base_pages` restore
- the parquet contains `runtime_s_1`
- the parquet also contains `dtlb_loads_1` and `dtlb_misses_1`

If your current container has the needed perf permissions, you can launch the
same style of run through `sudo docker exec`. If it does not, use the host-root
form below only as a fallback for dTLB collection.

Host-root fallback:

```bash
cd /users/SahilSC/HugePageResearch
sudo .venv/bin/python python/kernmlops/replay/replay_trace.py \
  data/redis_traces/snapshot.rdb \
  data/redis_traces/monitor_run.log \
  --breakpoints data/replay_smoke_breakpoints_5rows.parquet \
  --output data/replay_smoke_dtlb_results_5rows.parquet \
  --runs 1 \
  --collector-config config/replay_collectors_dtlb.yaml \
  -v
```

Example longer launch from `tmux`:

```bash
tmux new-session -d -s replay_dtlb
tmux send-keys -t replay_dtlb "cd /users/SahilSC/HugePageResearch && sudo .venv/bin/python python/kernmlops/replay/replay_trace.py data/redis_traces/replay_3min_22rows_snapshot.rdb data/redis_traces/replay_3min_22rows_monitor_run.log --breakpoints data/replay_3min_breakpoints_22rows.parquet --output data/replay_3min_dtlb_results_22rows.parquet --runs 3 --collector-config config/replay_collectors_dtlb.yaml -v" C-m
```

## What To Validate

For a relocated replay verification, these are the fastest checks:

```bash
cd /KernMLOps
bash -n python/kernmlops/replay/capture_redis_trace.sh
.venv/bin/python python/kernmlops/replay/generate_breakpoints.py --help
.venv/bin/python python/kernmlops/replay/replay_trace.py --help
PYTHONPYCACHEPREFIX=/tmp/pycache .venv/bin/python -m py_compile \
  python/kernmlops/replay/generate_breakpoints.py \
  python/kernmlops/replay/replay_trace.py \
  python/kernmlops/replay/hardware_collectors.py
PYTHONPATH=python/kernmlops .venv/bin/python -m unittest \
  testing.test_generate_breakpoints \
  testing.test_replay_trace \
  testing.test_replay_hardware_collectors
```

For a full end-to-end verification, confirm you produced:

- `data/redis_traces/snapshot.rdb`
- `data/redis_traces/monitor_run.log`
- one breakpoint parquet
- one replay result parquet

For runtime-only replay, the result parquet should contain `runtime_s_*` columns
plus the runtime-only replay metadata columns:

- `commands_replayed_*`
- `split_events_*`
- `split_successes_*`
- `split_failures_*`
- `split_syscall_attempts_*`
- `split_max_attempts_*`

It should contain no `dtlb_*` columns.

For dTLB replay, the result parquet should contain both the `runtime_s_*` and
`dtlb_*` columns.

## Runtime-Only Dashboard

For the overnight runtime-only matrix, generate the HTML dashboard right after
each replay finishes. The generic dashboard entrypoint is:

```bash
cd /KernMLOps
.venv/bin/python temp_data_analysis/replay_runtime_experiment_dashboard.py \
  --results data/<experiment>_results.parquet \
  --trace data/redis_traces/<experiment>_monitor_run.log \
  --manifest data/experiment_manifests/<experiment>.json \
  --output-html temp_data_analysis/<experiment>_dashboard.html \
  --output-metrics temp_data_analysis/<experiment>_metrics.json \
  --output-rows temp_data_analysis/<experiment>_rows.json \
  --output-summary temp_data_analysis/<experiment>_summary.md
```

The runtime-only dashboard is expected to show:

- mean runtime bars with runtime std error bars
- split-success labels like `hot#1 (2/3)`
- the exact capture and replay config from the manifest
- footer metadata with UTC start/end timestamps and elapsed time
- zero-success split rows omitted from the main runtime chart but still present
  in the summary data and markdown summary
