#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMP_DIR="${REPO_ROOT}/temp_data_analysis"
SESSION_NAME="${1:-redis-runtime-search}"
LOG_PATH="${TEMP_DIR}/redis_runtime_search_monitor.log"
STATUS_JSON="${TEMP_DIR}/redis_runtime_search_latest_status.json"
RUNNER_STATUS_PATH="${TEMP_DIR}/redis_runtime_search_runner.status"
INTERVAL_SEC="${INTERVAL_SEC:-1200}"

mkdir -p "${TEMP_DIR}"
: > "${LOG_PATH}"

log_line() {
  printf '[%s] session=%s status=%s stage=%s candidate=%s state=%s detail=%s\n' \
    "$1" "${SESSION_NAME}" "$2" "$3" "$4" "$5" "$6" >> "${LOG_PATH}"
}

json_field() {
  local path="$1"
  local field="$2"
  python3 - <<'PY' "$path" "$field"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
field = sys.argv[2]
if not path.exists():
    print("")
    raise SystemExit(0)
data = json.loads(path.read_text(encoding="utf-8"))
value = data.get(field, "")
print("" if value is None else str(value))
PY
}

last_status_mtime=""
stale_count=0

while tmux has-session -t "${SESSION_NAME}" 2>/dev/null; do
  timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  stage="$(json_field "${STATUS_JSON}" "stage")"
  candidate="$(json_field "${STATUS_JSON}" "candidate_name")"
  state="$(json_field "${STATUS_JSON}" "state")"

  if [[ -f "${STATUS_JSON}" ]]; then
    current_mtime="$(stat -c %Y "${STATUS_JSON}")"
    if [[ "${current_mtime}" == "${last_status_mtime}" ]]; then
      stale_count=$((stale_count + 1))
    else
      stale_count=0
      last_status_mtime="${current_mtime}"
    fi
  else
    current_mtime=""
    stale_count=$((stale_count + 1))
  fi

  detail="alive"
  if (( stale_count >= 2 )); then
    detail="stale_status"
  fi

  log_line "${timestamp}" "alive" "${stage}" "${candidate}" "${state}" "${detail}"
  tmux capture-pane -pt "${SESSION_NAME}" -S -40 >> "${LOG_PATH}" || true
  printf '\n' >> "${LOG_PATH}"
  sleep "${INTERVAL_SEC}"
done

timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
final_status="missing"
if [[ -f "${RUNNER_STATUS_PATH}" ]]; then
  final_status="$(tr -d '\n' < "${RUNNER_STATUS_PATH}")"
fi

stage="$(json_field "${STATUS_JSON}" "stage")"
candidate="$(json_field "${STATUS_JSON}" "candidate_name")"
state="$(json_field "${STATUS_JSON}" "state")"

if [[ "${final_status}" == "completed" ]]; then
  log_line "${timestamp}" "finished" "${stage}" "${candidate}" "${state}" "runner_completed"
else
  log_line "${timestamp}" "failed" "${stage}" "${candidate}" "${state}" "session_missing_or_runner_failed"
fi
