#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d)"
SERVER_SCRIPT="$WORK_DIR/pull_server.py"
REQUEST_LOG="$WORK_DIR/requests.jsonl"
PROJECT_DIR="$WORK_DIR/project"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

cat > "$SERVER_SCRIPT" <<'PY'
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

request_log = os.environ["REQUEST_LOG"]


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", "0")))
        with open(request_log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"path": self.path, "body": json.loads(body.decode("utf-8"))}) + "\n")
        payload = json.dumps(
            {
                "tasks": [
                    {"external_task_id": "ext-1", "payload": {"text": "alpha"}},
                    {"external_task_id": "ext-2", "payload": {"text": "beta"}},
                ]
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return None


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(server.server_port, flush=True)
server.serve_forever()
PY

cd "$ROOT_DIR"
REQUEST_LOG="$REQUEST_LOG" python "$SERVER_SCRIPT" > "$WORK_DIR/server.port" &
SERVER_PID=$!

for _ in $(seq 1 50); do
  if [[ -s "$WORK_DIR/server.port" ]]; then
    break
  fi
  sleep 0.1
done

PORT="$(cat "$WORK_DIR/server.port")"
PULL_URL="http://127.0.0.1:${PORT}/pull"

uv run annotation-pipeline init --project-root "$PROJECT_DIR"
cat > "$PROJECT_DIR/.annotation-pipeline/external_tasks.yaml" <<YAML
external_tasks:
  default:
    enabled: true
    system_id: verify-vendor
    pull_url: ${PULL_URL}
YAML

uv run annotation-pipeline external pull \
  --project-root "$PROJECT_DIR" \
  --project-id verify-project \
  --source-id default \
  --limit 2 > "$WORK_DIR/pull-result.json"

PROJECT_DIR="$PROJECT_DIR" REQUEST_LOG="$REQUEST_LOG" python - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["PROJECT_DIR"]) / ".annotation-pipeline"
tasks = sorted((root / "tasks").glob("*.json"))
outbox = sorted((root / "outbox").glob("*.json"))
requests = [json.loads(line) for line in Path(os.environ["REQUEST_LOG"]).read_text(encoding="utf-8").splitlines()]
assert len(tasks) == 2, len(tasks)
assert len(outbox) == 2, len(outbox)
assert requests == [{"path": "/pull", "body": {"limit": 2}}], requests
for task_path in tasks:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    assert task["status"] == "pending"
    assert task["pipeline_id"] == "verify-project"
    assert task["external_ref"]["system_id"] == "verify-vendor"
    events_path = root / "events" / f"{task['task_id']}.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert events[0]["reason"] == "created from external task pull"
print("external pull verification passed")
PY
