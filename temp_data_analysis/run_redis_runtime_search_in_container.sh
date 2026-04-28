#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMP_DIR="${REPO_ROOT}/temp_data_analysis"
RUNNER_LOG="${TEMP_DIR}/redis_runtime_search_runner.log"
CONTAINER_FILE="${TEMP_DIR}/redis_runtime_search_container.txt"
COMMANDS_FILE="${TEMP_DIR}/redis_runtime_search_commands.txt"
STATUS_FILE="${TEMP_DIR}/redis_runtime_search_runner.status"

TOTAL_BUDGET_HOURS="${TOTAL_BUDGET_HOURS:-8.0}"
STAGE1_HOURS="${STAGE1_HOURS:-4.5}"
MAX_MODE_RUNTIME_MINUTES="${MAX_MODE_RUNTIME_MINUTES:-30.0}"
BRANCH_NAME="${BRANCH_NAME:-redis_runtimes}"
RESUME_MANIFEST="${RESUME_MANIFEST:-}"

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "${RUNNER_LOG}"
}

fail() {
  log "ERROR: $*"
  printf 'failed\n' > "${STATUS_FILE}"
  exit 1
}

find_newest_running_container() {
  sudo docker ps --format '{{.Names}}' | head -n 1
}

container_name="${1:-}"
if [[ -z "${container_name}" ]]; then
  container_name="$(find_newest_running_container)"
fi
[[ -n "${container_name}" ]] || fail "No running Docker container found"

mkdir -p "${TEMP_DIR}"
: > "${RUNNER_LOG}"
printf '%s\n' "${container_name}" > "${CONTAINER_FILE}"
printf 'running\n' > "${STATUS_FILE}"

runner_cmd=".venv/bin/python python/kernmlops/experiments/redis_thp_replication/runtime_search.py --container-name ${container_name} --branch-name ${BRANCH_NAME} --total-budget-hours ${TOTAL_BUDGET_HOURS} --stage1-hours ${STAGE1_HOURS} --max-mode-runtime-minutes ${MAX_MODE_RUNTIME_MINUTES}"
if [[ -n "${RESUME_MANIFEST}" ]]; then
  runner_cmd="${runner_cmd} --resume-manifest ${RESUME_MANIFEST}"
fi
printf '%s\n' "${runner_cmd}" > "${COMMANDS_FILE}"

log "Starting Redis runtime search in container ${container_name}"
log "Command: ${runner_cmd}"

if ! sudo docker exec "${container_name}" bash -lc "set -euo pipefail; cd /KernMLOps; ${runner_cmd}" 2>&1 | tee -a "${RUNNER_LOG}"; then
  fail "Runtime search command failed in container ${container_name}"
fi

printf 'completed\n' > "${STATUS_FILE}"
log "Redis runtime search completed successfully"
