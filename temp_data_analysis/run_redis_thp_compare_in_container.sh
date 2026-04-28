#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMP_DIR="${REPO_ROOT}/temp_data_analysis"
RUNNER_LOG="${TEMP_DIR}/redis_thp_compare_runner.log"
CONTAINER_FILE="${TEMP_DIR}/redis_thp_compare_container.txt"
COMMANDS_FILE="${TEMP_DIR}/redis_thp_compare_commands.txt"
STATUS_FILE="${TEMP_DIR}/redis_thp_compare_runner.status"
SESSION_NAME="${SESSION_NAME:-redis-thp-compare}"

RUN_SPECS=(
  "outer10|always|config/redis_always_outer10.yaml"
  "outer10|madvise|config/redis_madvise_outer10.yaml"
  "outer10|never|config/redis_never_outer10.yaml"
  "outer10_update50_delete50|always|config/redis_always_outer10_update50_delete50.yaml"
  "outer10_update50_delete50|madvise|config/redis_madvise_outer10_update50_delete50.yaml"
  "outer10_update50_delete50|never|config/redis_never_outer10_update50_delete50.yaml"
)

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "${RUNNER_LOG}"
}

fail() {
  log "ERROR: $*"
  printf 'failed\n' > "${STATUS_FILE}"
  exit 1
}

extract_collection_id() {
  local stdout_log="$1"
  grep -E '^Collection_id: ' "${stdout_log}" | tail -n 1 | awk '{print $2}'
}

verify_run_output() {
  local stdout_log="$1"
  local collection_id
  collection_id="$(extract_collection_id "${stdout_log}")"
  [[ -n "${collection_id}" ]] || fail "Missing Collection_id in ${stdout_log}"
  local parquet_path="${REPO_ROOT}/data/curated/redis/${collection_id}/system_info.end.parquet"
  [[ -f "${parquet_path}" ]] || fail "Missing curated parquet for ${collection_id}: ${parquet_path}"
  log "Verified collection ${collection_id} at ${parquet_path}"
}

container_name="${1:-}"
if [[ -z "${container_name}" ]]; then
  container_name="$(sudo docker ps --format '{{.Names}}' | head -n 1)"
fi
[[ -n "${container_name}" ]] || fail "No running Docker container found"

mkdir -p "${TEMP_DIR}"
: > "${RUNNER_LOG}"
: > "${COMMANDS_FILE}"
printf '%s\n' "${container_name}" > "${CONTAINER_FILE}"
printf 'running\n' > "${STATUS_FILE}"

log "Starting Redis THP compare runner for container ${container_name}"

for spec in "${RUN_SPECS[@]}"; do
  IFS='|' read -r suite_slug mode_name config_path <<< "${spec}"
  run_slug="${suite_slug}_${mode_name}"
  stdout_log_rel="temp_data_analysis/${run_slug}_collect.stdout.log"
  stdout_log_abs="${REPO_ROOT}/${stdout_log_rel}"
  benchmark_log_abs="${REPO_ROOT}/temp_data_analysis/${run_slug}_redis_benchmark.log"
  command_text="python python/kernmlops collect -v -c ${config_path} --benchmark redis"
  printf '%s\n' "${command_text}" >> "${COMMANDS_FILE}"
  log "Launching ${run_slug}: ${command_text}"
  sudo docker exec "${container_name}" bash -lc \
    "set -euo pipefail; cd /KernMLOps; ${command_text} 2>&1 | tee ${stdout_log_rel}"
  if [[ ! -f "${REPO_ROOT}/redis_benchmark.log" ]]; then
    fail "redis_benchmark.log missing after ${run_slug}"
  fi
  cp "${REPO_ROOT}/redis_benchmark.log" "${benchmark_log_abs}"
  verify_run_output "${stdout_log_abs}"
  log "Preserved benchmark log at ${benchmark_log_abs}"
done

printf 'completed\n' > "${STATUS_FILE}"
log "All Redis THP compare runs completed successfully"
