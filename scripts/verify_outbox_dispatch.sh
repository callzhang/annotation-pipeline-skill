#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(mktemp -d /tmp/annotation-outbox-verify-XXXXXX)"
SERVER_DIR="$PROJECT_ROOT/server"
PORT_FILE="$SERVER_DIR/port"
REQUESTS_FILE="$SERVER_DIR/requests.jsonl"

mkdir -p "$SERVER_DIR"
cd "$ROOT_DIR"

python - "$PORT_FILE" "$REQUESTS_FILE" <<'PY' &
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

port_path = Path(sys.argv[1])
requests_path = Path(sys.argv[2])


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        payload = json.loads(body.decode("utf-8")) if body else None
        with requests_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"path": self.path, "payload": payload}, sort_keys=True) + "\n")
        response = b'{"ok":true}\n'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format, *args):
        return


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
port_path.write_text(str(server.server_address[1]), encoding="utf-8")
server.serve_forever()
PY
SERVER_PID="$!"
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  [[ -s "$PORT_FILE" ]] && break
  sleep 0.1
done
PORT="$(cat "$PORT_FILE")"

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline init --project-root "$PROJECT_ROOT"
cat > "$PROJECT_ROOT/.annotation-pipeline/callbacks.yaml" <<YAML
callbacks:
  status:
    enabled: false
    url: null
    secret_env: null
  submit:
    enabled: true
    url: http://127.0.0.1:${PORT}/submit
    secret_env: null
YAML

python - "$PROJECT_ROOT" <<'PY'
import json
import sys
from pathlib import Path

from annotation_pipeline_skill.core.models import ArtifactRef, ExternalTaskRef, Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.store.file_store import FileStore

project_root = Path(sys.argv[1])
store = FileStore(project_root / ".annotation-pipeline")
task = Task.new(
    task_id="external-task-1",
    pipeline_id="outbox-verify",
    source_ref={"kind": "external_task", "payload": {"text": "alpha"}},
    external_ref=ExternalTaskRef(
        system_id="verify",
        external_task_id="ext-1",
        source_url=None,
        idempotency_key="verify:ext-1",
    ),
)
task.status = TaskStatus.ACCEPTED
store.save_task(task)
payload_path = store.root / "artifact_payloads/external-task-1/external-task-1-attempt-1_annotation_result.json"
payload_path.parent.mkdir(parents=True, exist_ok=True)
payload_path.write_text(json.dumps({"text": '{"labels":[{"text":"alpha"}]}'}), encoding="utf-8")
store.append_artifact(
    ArtifactRef.new(
        task_id=task.task_id,
        kind="annotation_result",
        path=str(payload_path.relative_to(store.root)),
        content_type="application/json",
    )
)
PY

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline export training-data --project-root "$PROJECT_ROOT" --project-id outbox-verify --export-id export-1 --enqueue-external-submit >/dev/null
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline report readiness --project-root "$PROJECT_ROOT" --project-id outbox-verify > "$PROJECT_ROOT/readiness-before.json"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline outbox drain --project-root "$PROJECT_ROOT" --max-items 10 > "$PROJECT_ROOT/outbox-drain.json"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline report readiness --project-root "$PROJECT_ROOT" --project-id outbox-verify > "$PROJECT_ROOT/readiness-after.json"

python - "$PROJECT_ROOT" "$REQUESTS_FILE" <<'PY'
import json
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
requests_file = Path(sys.argv[2])
before = json.loads((project_root / "readiness-before.json").read_text(encoding="utf-8"))
drain = json.loads((project_root / "outbox-drain.json").read_text(encoding="utf-8"))
after = json.loads((project_root / "readiness-after.json").read_text(encoding="utf-8"))
requests = [json.loads(line) for line in requests_file.read_text(encoding="utf-8").splitlines() if line.strip()]

if before["recommended_next_action"] != "drain_external_outbox":
    raise SystemExit(f"unexpected readiness before drain: {before}")
if drain["result"] != {"dead_letter": 0, "retry": 0, "sent": 1, "skipped": 0}:
    raise SystemExit(f"unexpected drain result: {drain}")
if after["ready_for_training"] is not True:
    raise SystemExit(f"unexpected readiness after drain: {after}")
if len(requests) != 1 or requests[0]["path"] != "/submit":
    raise SystemExit(f"unexpected callback requests: {requests}")
if requests[0]["payload"]["export_id"] != "export-1":
    raise SystemExit(f"unexpected submit payload: {requests[0]['payload']}")

print(f"outbox dispatch verification passed: {project_root}")
PY
