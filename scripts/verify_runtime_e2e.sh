#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(mktemp -d /tmp/annotation-runtime-e2e-XXXXXX)"
INPUT_FILE="$PROJECT_ROOT/input.jsonl"
PORT="${ANNOTATION_PIPELINE_VERIFY_PORT:-18765}"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

cd "$ROOT_DIR"

printf '{"text":"alpha","source_dataset":"demo"}\n{"text":"beta","source_dataset":"demo"}\n' > "$INPUT_FILE"

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline init --project-root "$PROJECT_ROOT"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline create-tasks --project-root "$PROJECT_ROOT" --source "$INPUT_FILE" --pipeline-id verify --batch-size 2 --group-by source_dataset

STATUS_JSON="$PROJECT_ROOT/runtime-status.json"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime status --project-root "$PROJECT_ROOT" > "$STATUS_JSON"

python - "$STATUS_JSON" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ("runtime_status", "queue_counts", "capacity"):
    if key not in payload:
        raise SystemExit(f"missing {key} in runtime status")
PY

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline serve --project-root "$PROJECT_ROOT" --host 127.0.0.1 --port "$PORT" &
SERVER_PID="$!"

python - "$PORT" <<'PY'
import json
import sys
import time
import urllib.request

port = int(sys.argv[1])
base = f"http://127.0.0.1:{port}"


def request(path: str, method: str = "GET") -> dict:
    req = urllib.request.Request(base + path, data=b"{}" if method == "POST" else None, method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


deadline = time.time() + 10
while True:
    try:
        request("/api/health")
        break
    except Exception:
        if time.time() > deadline:
            raise
        time.sleep(0.1)

runtime = request("/api/runtime")
for key in ("runtime_status", "queue_counts", "capacity"):
    if key not in runtime:
        raise SystemExit(f"missing {key} in /api/runtime")

monitor = request("/api/runtime/monitor")
for key in ("ok", "failures", "details"):
    if key not in monitor:
        raise SystemExit(f"missing {key} in /api/runtime/monitor")

cycles = request("/api/runtime/cycles")
if "cycles" not in cycles:
    raise SystemExit("missing cycles in /api/runtime/cycles")

run_once = request("/api/runtime/run-once", method="POST")
if run_once.get("ok") is not True or "snapshot" not in run_once:
    raise SystemExit("invalid /api/runtime/run-once response")

cycles_after = request("/api/runtime/cycles")
if len(cycles_after["cycles"]) < 1:
    raise SystemExit("run-once did not record a runtime cycle")
PY

echo "runtime e2e verification passed: $PROJECT_ROOT"
