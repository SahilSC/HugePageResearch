#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SESSION_NAME="${1:-redis-thp-compare}"
LOG_PATH="${REPO_ROOT}/temp_data_analysis/redis_thp_compare_monitor.log"
INTERVAL_SEC="${INTERVAL_SEC:-1200}"

mkdir -p "${REPO_ROOT}/temp_data_analysis"
: > "${LOG_PATH}"

log_status() {
  local timestamp="$1"
  printf '[%s] session=%s status=%s\n' "${timestamp}" "${SESSION_NAME}" "$2" >> "${LOG_PATH}"
}

while tmux has-session -t "${SESSION_NAME}" 2>/dev/null; do
  timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log_status "${timestamp}" "alive"
  tmux capture-pane -pt "${SESSION_NAME}" -S -40 >> "${LOG_PATH}" || true
  printf '\n' >> "${LOG_PATH}"
  sleep "${INTERVAL_SEC}"
done

timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
log_status "${timestamp}" "finished"
