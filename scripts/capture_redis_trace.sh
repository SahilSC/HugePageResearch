#!/usr/bin/env bash
set -euo pipefail

# Run YCSB load, snapshot the RDB, then capture a redis-cli monitor log for the run phase.
# Parameters from config/redis_never.yaml.
# Redis server must already be running. Run from the repository root.
# Usage: ./scripts/capture_redis_trace.sh [-h] [-d ycsb_dir]

# --- Constants ----------------------------------------------------------------

readonly EXPLICIT_PURGE=true
readonly SERVER_SLEEP=10

readonly REDIS_HOST="127.0.0.1"
readonly REDIS_PORT=6379
readonly BENCHMARK_DIR_NAME="kernmlops-benchmark"

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly TRACE_BASE_DIR="${SCRIPT_DIR}/../data/redis_traces"

YCSB_PARAMS=(
    -p "redis.host=${REDIS_HOST}"
    -p "redis.port=${REDIS_PORT}"
    -p "fieldcount=1"
    -p "fieldlength=2097152"
    -p "minfieldlength=4096"
    -p "insertorder=hashed"
    -p "zeropadding=1"
    -p "fieldlengthdistribution=uniform"
    -p "recordcount=4096"
    -p "insertstart=0"
    -p "operationcount=4096"
    -p "workload=site.ycsb.workloads.CoreWorkload"
    -p "readproportion=0.05"
    -p "updateproportion=0.00"
    -p "scanproportion=0.00"
    -p "insertproportion=0.00"
    -p "readmodifywriteproportion=0.00"
    -p "deleteproportion=0.95"
    -p "requestdistribution=zipfian"
    -p "threadcount=16"
    -p "target=10000"
)
readonly YCSB_PARAMS

# --- Globals ------------------------------------------------------------------

BENCHMARK_DIR="${BENCHMARK_DIR:-${HOME}/${BENCHMARK_DIR_NAME}}"
YCSB_DIR="${BENCHMARK_DIR}/ycsb/YCSB"
MONITOR_PID=""

# --- Helpers ------------------------------------------------------------------

die() {
    echo "ERROR: $*" >&2
    exit 1
}

run_ycsb() {
    (cd "${YCSB_DIR}" && python bin/ycsb "$@")
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
# Triggers BGSAVE, waits for completion, then copies the RDB with sudo.

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
sudo cp "${rdb_dir}/${rdb_file}" "${snapshot_rdb}"
sudo chown "$(id -u):$(id -g)" "${snapshot_rdb}"
echo "Snapshot saved to ${snapshot_rdb}" >&2

keys_file="${output_dir}/keys.txt"
redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" --no-auth-warning \
    KEYS 'user*' | sort >"${keys_file}"
echo "Keys dumped to ${keys_file}" >&2

[[ "${EXPLICIT_PURGE}" == "true" ]] && redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" MEMORY PURGE >/dev/null

# --- Run phase ----------------------------------------------------------------

echo "Running YCSB run phase ..." >&2
start_monitor "${output_dir}/monitor_run.log"
run_ycsb run redis -s -P workloads/workloada "${YCSB_PARAMS[@]}" >/dev/null 2>&1
stop_monitor
echo "Run complete." >&2

[[ "${EXPLICIT_PURGE}" == "true" ]] && redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" MEMORY PURGE >/dev/null

echo "${output_dir}"
