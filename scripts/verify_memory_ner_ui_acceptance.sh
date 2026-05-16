#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_ROOT="${MEMORY_NER_UI_EVIDENCE_ROOT:-/tmp/annotation-memory-ner-ui-acceptance}"
DEFAULT_PROJECT_ROOT="$EVIDENCE_ROOT/project"
PROJECT_ID="${MEMORY_NER_UI_PROJECT_ID:-memory-ner-accepted-e2e}"
MIN_ACCEPTED="${MEMORY_NER_UI_MIN_ACCEPTED:-10}"
API_PORT="${MEMORY_NER_UI_API_PORT:-8509}"
WEB_PORT="${MEMORY_NER_UI_WEB_PORT:-5173}"
REPORT_JSON="$EVIDENCE_ROOT/report.json"
API_LOG="$EVIDENCE_ROOT/api.log"
WEB_LOG="$EVIDENCE_ROOT/web.log"

mkdir -p "$EVIDENCE_ROOT"
rm -f "$REPORT_JSON"
cd "$ROOT_DIR"

accepted_count_for() {
  python - "$1" <<'PY'
import json
import sys
from pathlib import Path

tasks_dir = Path(sys.argv[1]) / ".annotation-pipeline" / "tasks"
if not tasks_dir.is_dir():
    print(0)
    raise SystemExit
print(sum(1 for path in tasks_dir.glob("*.json") if json.loads(path.read_text(encoding="utf-8")).get("status") == "accepted"))
PY
}

discover_project_root() {
  if [[ -n "${MEMORY_NER_UI_PROJECT_ROOT:-}" ]]; then
    echo "$MEMORY_NER_UI_PROJECT_ROOT"
    return
  fi
  python - "$DEFAULT_PROJECT_ROOT" <<'PY'
import sys
from pathlib import Path

candidates = [Path(sys.argv[1])]
candidates.extend(Path("/tmp").glob("annotation-memory-ner-e2e-*"))
existing = [path for path in candidates if (path / ".annotation-pipeline" / "tasks").is_dir()]
for path in sorted(existing, key=lambda item: item.stat().st_mtime, reverse=True):
    print(path)
PY
}

if [[ "${MEMORY_NER_UI_REFRESH_PROJECT:-0}" == "1" && -z "${MEMORY_NER_UI_PROJECT_ROOT:-}" ]]; then
  PROJECT_ROOT="$DEFAULT_PROJECT_ROOT"
else
  PROJECT_ROOT=""
  while IFS= read -r candidate; do
    if [[ -z "$candidate" ]]; then
      continue
    fi
    if (( "$(accepted_count_for "$candidate")" >= MIN_ACCEPTED )); then
      PROJECT_ROOT="$candidate"
      break
    fi
  done < <(discover_project_root)
  if [[ -z "$PROJECT_ROOT" ]]; then
    PROJECT_ROOT="${MEMORY_NER_UI_PROJECT_ROOT:-$DEFAULT_PROJECT_ROOT}"
  fi
fi

if [[ "${MEMORY_NER_UI_REFRESH_PROJECT:-0}" == "1" ]]; then
  if [[ "$PROJECT_ROOT" != "$DEFAULT_PROJECT_ROOT" ]]; then
    echo "MEMORY_NER_UI_REFRESH_PROJECT=1 only supports the default project root: $DEFAULT_PROJECT_ROOT" >&2
    exit 1
  fi
  rm -rf "$PROJECT_ROOT"
fi

if [[ ! -d "$PROJECT_ROOT/.annotation-pipeline" ]]; then
  MEMORY_NER_E2E_PROJECT_ROOT="$PROJECT_ROOT" KEEP_MEMORY_NER_E2E_PROJECT=1 MEMORY_NER_E2E_MIN_ACCEPTED="$MIN_ACCEPTED" MEMORY_NER_E2E_MAX_CYCLES="${MEMORY_NER_UI_MAX_CYCLES:-10}" \
    bash scripts/verify_memory_ner_accepted_e2e.sh > "$EVIDENCE_ROOT/accepted-e2e.log"
fi

ACCEPTED_COUNT="$(accepted_count_for "$PROJECT_ROOT")"
if (( ACCEPTED_COUNT < MIN_ACCEPTED )); then
  if [[ -n "${MEMORY_NER_UI_PROJECT_ROOT:-}" ]]; then
    echo "project has only $ACCEPTED_COUNT accepted tasks, expected at least $MIN_ACCEPTED: $PROJECT_ROOT" >&2
    exit 1
  fi
  rm -rf "$PROJECT_ROOT"
  MEMORY_NER_E2E_PROJECT_ROOT="$PROJECT_ROOT" KEEP_MEMORY_NER_E2E_PROJECT=1 MEMORY_NER_E2E_MIN_ACCEPTED="$MIN_ACCEPTED" MEMORY_NER_E2E_MAX_CYCLES="${MEMORY_NER_UI_MAX_CYCLES:-10}" \
    bash scripts/verify_memory_ner_accepted_e2e.sh > "$EVIDENCE_ROOT/accepted-e2e.log"
fi

port_is_free() {
  python - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.2)
    sys.exit(0 if sock.connect_ex(("127.0.0.1", port)) != 0 else 1)
PY
}

select_port() {
  local requested="$1"
  local explicit="$2"
  local port="$requested"

  if [[ -n "$explicit" ]]; then
    if port_is_free "$port"; then
      echo "$port"
      return
    fi
    echo "configured port is already in use: $port" >&2
    exit 1
  fi

  while ! port_is_free "$port"; do
    port=$((port + 1))
  done
  echo "$port"
}

API_PORT="$(select_port "$API_PORT" "${MEMORY_NER_UI_API_PORT:-}")"

cleanup() {
  if [[ -n "${API_PID:-}" ]]; then
    kill -- "-$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  if [[ -n "${WEB_PID:-}" ]]; then
    kill -- "-$WEB_PID" 2>/dev/null || true
    wait "$WEB_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

setsid bash -c 'exec env UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}" UV_LINK_MODE="${UV_LINK_MODE:-copy}" uv run --with-editable . annotation-pipeline serve --project-root "$1" --host 127.0.0.1 --port "$2"' \
  _ "$PROJECT_ROOT" "$API_PORT" > "$API_LOG" 2>&1 &
API_PID=$!

for _ in $(seq 1 80); do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "API server exited before readiness; see $API_LOG" >&2
    exit 1
  fi
  if curl -fsS "http://127.0.0.1:$API_PORT/api/projects" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
curl -fsS "http://127.0.0.1:$API_PORT/api/projects" >/dev/null

if [[ -n "${MEMORY_NER_UI_WEB_PORT:-}" && "$WEB_PORT" == "$API_PORT" ]]; then
  echo "configured web port must differ from API port: $WEB_PORT" >&2
  exit 1
fi
WEB_PORT="$(select_port "$WEB_PORT" "${MEMORY_NER_UI_WEB_PORT:-}")"

(
  cd web
  npx playwright install chromium
) > "$EVIDENCE_ROOT/playwright-install.log" 2>&1

setsid bash -c 'cd web && exec env VITE_API_TARGET="http://127.0.0.1:'"$API_PORT"'" npm run dev -- --host 127.0.0.1 --port "$1" --strictPort' \
  _ "$WEB_PORT" > "$WEB_LOG" 2>&1 &
WEB_PID=$!

for _ in $(seq 1 80); do
  if ! kill -0 "$WEB_PID" 2>/dev/null; then
    echo "Vite server exited before readiness; see $WEB_LOG" >&2
    exit 1
  fi
  if curl -fsS "http://127.0.0.1:$WEB_PORT/" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
curl -fsS "http://127.0.0.1:$WEB_PORT/" >/dev/null

MEMORY_NER_UI_BASE_URL="http://127.0.0.1:$WEB_PORT" \
MEMORY_NER_UI_REPORT="$REPORT_JSON" \
MEMORY_NER_UI_PROJECT_ID="$PROJECT_ID" \
node web/tests/memory-ner-ui-acceptance.mjs

echo "memory-ner UI acceptance passed: $REPORT_JSON"
