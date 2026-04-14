#!/usr/bin/env bash
set -euo pipefail

# Run YCSB load, snapshot the RDB, then capture a redis-cli monitor log for the run phase.
# Parameters from config/redis_never.yaml.
# Redis server must already be running. Run from the repository root.
# Usage: ./python/kernmlops/replay/capture_redis_trace.sh [-h] [-d ycsb_dir]

# --- Constants ----------------------------------------------------------------

readonly EXPLICIT_PURGE=true
readonly SERVER_SLEEP=10
readonly RECORD_COUNT="${RECORD_COUNT:-4096}"
readonly OPERATION_COUNT="${OPERATION_COUNT:-4096}"
readonly READ_PROPORTION="${READ_PROPORTION:-0.05}"
readonly UPDATE_PROPORTION="${UPDATE_PROPORTION:-0.00}"
readonly SCAN_PROPORTION="${SCAN_PROPORTION:-0.00}"
readonly INSERT_PROPORTION="${INSERT_PROPORTION:-0.00}"
readonly READMODIFYWRITE_PROPORTION="${READMODIFYWRITE_PROPORTION:-0.00}"
readonly DELETE_PROPORTION="${DELETE_PROPORTION:-0.95}"

readonly BENCHMARK_DIR_NAME="kernmlops-benchmark"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly TRACE_BASE_DIR="${REPO_ROOT}/data/redis_traces"
readonly PYTHON_KERNMLOPS_DIR="${REPO_ROOT}/python/kernmlops"

# --- Globals ------------------------------------------------------------------

BENCHMARK_DIR="${BENCHMARK_DIR:-${HOME}/${BENCHMARK_DIR_NAME}}"
YCSB_DIR="${BENCHMARK_DIR}/ycsb/YCSB"
MONITOR_PID=""

# --- Helpers ------------------------------------------------------------------

die() {
    echo "ERROR: $*" >&2
    exit 1
}

load_redis_endpoint() {
    PYTHONPATH="${PYTHON_KERNMLOPS_DIR}${PYTHONPATH:+:${PYTHONPATH}}" python - <<'PY'
from redis_runtime import load_repo_redis_endpoint

endpoint = load_repo_redis_endpoint()
print(endpoint.host)
print(endpoint.port)
PY
}

mapfile -t REDIS_ENDPOINT_LINES < <(load_redis_endpoint)
[[ "${#REDIS_ENDPOINT_LINES[@]}" -eq 2 ]] || die "Failed to read Redis endpoint from config/redis.conf"
readonly REDIS_HOST="${REDIS_ENDPOINT_LINES[0]}"
readonly REDIS_PORT="${REDIS_ENDPOINT_LINES[1]}"
unset REDIS_ENDPOINT_LINES

YCSB_PARAMS=(
    -p "redis.host=${REDIS_HOST}"
    -p "redis.port=${REDIS_PORT}"
    -p "fieldcount=1"
    -p "fieldlength=2097152"
    -p "minfieldlength=4096"
    -p "insertorder=hashed"
    -p "zeropadding=1"
    -p "fieldlengthdistribution=uniform"
    -p "recordcount=${RECORD_COUNT}"
    -p "insertstart=0"
    -p "operationcount=${OPERATION_COUNT}"
    -p "workload=site.ycsb.workloads.CoreWorkload"
    -p "readproportion=${READ_PROPORTION}"
    -p "updateproportion=${UPDATE_PROPORTION}"
    -p "scanproportion=${SCAN_PROPORTION}"
    -p "insertproportion=${INSERT_PROPORTION}"
    -p "readmodifywriteproportion=${READMODIFYWRITE_PROPORTION}"
    -p "deleteproportion=${DELETE_PROPORTION}"
    -p "requestdistribution=zipfian"
    -p "threadcount=16"
    -p "target=10000"
)
readonly YCSB_PARAMS

run_ycsb() {
    (cd "${YCSB_DIR}" && python bin/ycsb "$@")
}

validate_operation_mix() {
    python - \
        "${READ_PROPORTION}" \
        "${UPDATE_PROPORTION}" \
        "${SCAN_PROPORTION}" \
        "${INSERT_PROPORTION}" \
        "${READMODIFYWRITE_PROPORTION}" \
        "${DELETE_PROPORTION}" <<'PY'
import math
import sys

names = [
    "READ_PROPORTION",
    "UPDATE_PROPORTION",
    "SCAN_PROPORTION",
    "INSERT_PROPORTION",
    "READMODIFYWRITE_PROPORTION",
    "DELETE_PROPORTION",
]
values = []
for name, raw in zip(names, sys.argv[1:], strict=True):
    try:
        value = float(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be numeric, got {raw!r}") from exc
    if value < 0.0 or value > 1.0:
        raise SystemExit(f"{name} must be in [0.0, 1.0], got {value}")
    values.append(value)

total = sum(values)
if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
    raise SystemExit(
        "YCSB operation proportions must sum to 1.0; "
        f"got {total:.12f} from "
        + ", ".join(f"{name}={value}" for name, value in zip(names, values, strict=True))
    )
PY
}

start_monitor() {
    local log_file="$1"
    redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" monitor >"${log_file}" 2>&1 &
    MONITOR_PID=$!
}

stop_monitor() {
    kill "${MONITOR_PID}" 2>/dev/null || true
    wait "${MONITOR_PID}" 2>/dev/null || true
    MONITOR_PID=""
}

cleanup() { [[ -n "${MONITOR_PID}" ]] && stop_monitor || true; }
trap cleanup EXIT ERR INT TERM

# --- Arg parsing --------------------------------------------------------------

usage() {
    echo "Usage: $(basename "$0") [-h] [-d ycsb_dir]"
    echo "  -d, --ycsb-dir   Path to YCSB directory (default: ${YCSB_DIR})"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
    -h | --help)
        usage
        exit 0
        ;;
    -d | --ycsb-dir)
        YCSB_DIR="${2:?'--ycsb-dir requires a value'}"
        shift 2
        ;;
    *) die "Unknown argument: $1" ;;
    esac
done

# --- Preflight ----------------------------------------------------------------

[[ -x "${YCSB_DIR}/bin/ycsb" ]] || die "YCSB not found at ${YCSB_DIR}/bin/ycsb"

redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" ping &>/dev/null || die "Cannot reach Redis at ${REDIS_HOST}:${REDIS_PORT}"
validate_operation_mix

# --- Setup --------------------------------------------------------------------

output_dir="${TRACE_BASE_DIR}"
mkdir -p "${output_dir}"

redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" FLUSHALL >/dev/null
sleep "${SERVER_SLEEP}"

# --- Load phase ---------------------------------------------------------------

echo "Running YCSB load ..." >&2
run_ycsb load redis -s -P workloads/workloada "${YCSB_PARAMS[@]}" >/dev/null 2>&1
echo "Load complete." >&2

# --- Snapshot -----------------------------------------------------------------
# Save an RDB snapshot after the load so replay_trace.py can restore the
# exact key-value layout (including value sizes) between benchmark iterations.
# Triggers BGSAVE, waits for completion, then moves the finished RDB into the
# preserved snapshot path. This avoids keeping two large copies of the same
# snapshot on disk at once for the longer replay captures.

snapshot_rdb="${output_dir}/snapshot.rdb"

rdb_dir=$(redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" config get dir | tail -1)
rdb_file=$(redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" config get dbfilename | tail -1)
[[ -n "${rdb_file}" ]] || die "Redis dbfilename is not configured"

echo "Saving RDB snapshot ..." >&2
last_save_before=$(redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" lastsave)
redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" bgsave >/dev/null
while true; do
    last_save_now=$(redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" lastsave)
    [[ "${last_save_now}" -gt "${last_save_before}" ]] && break
    sleep 1
done
sudo mv "${rdb_dir}/${rdb_file}" "${snapshot_rdb}"
sudo chown "$(id -u):$(id -g)" "${snapshot_rdb}"
echo "Snapshot saved to ${snapshot_rdb}" >&2

[[ "${EXPLICIT_PURGE}" == "true" ]] && redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" MEMORY PURGE >/dev/null

# --- Run phase ----------------------------------------------------------------

echo "Running YCSB run phase ..." >&2
start_monitor "${output_dir}/monitor_run.log"
run_ycsb run redis -s -P workloads/workloada "${YCSB_PARAMS[@]}" >/dev/null 2>&1
stop_monitor
echo "Run complete." >&2

[[ "${EXPLICIT_PURGE}" == "true" ]] && redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" MEMORY PURGE >/dev/null

echo "${output_dir}"
