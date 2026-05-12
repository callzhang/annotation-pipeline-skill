#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d /tmp/annotation-agent-handoff-XXXXXX)"
CODEX_HOME="$WORK_DIR/codex-home"
SKILL_DIR="$CODEX_HOME/skills/annotation-pipeline-skill"
PROJECT_ROOT="$WORK_DIR/project"
INPUT_FILE="$WORK_DIR/input.jsonl"
PORT="${ANNOTATION_PIPELINE_HANDOFF_PORT:-$((21000 + RANDOM % 20000))}"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

mkdir -p "$SKILL_DIR"
cd "$ROOT_DIR"
tar \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='.pytest_cache' \
  --exclude='annotation_pipeline_skill.egg-info' \
  --exclude='web/node_modules' \
  --exclude='web/dist' \
  --exclude='.worktrees' \
  -cf - . | tar -xf - -C "$SKILL_DIR"

export CODEX_HOME
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

run_cli() {
  uv run --project "$SKILL_DIR" --no-dev annotation-pipeline "$@"
}

printf '{"text":"alpha","source_dataset":"handoff"}\n{"text":"beta","source_dataset":"handoff"}\n' > "$INPUT_FILE"

run_cli --help >/dev/null
run_cli init --project-root "$PROJECT_ROOT"
run_cli doctor --project-root "$PROJECT_ROOT"
run_cli provider doctor --project-root "$PROJECT_ROOT" >/dev/null
run_cli provider targets --project-root "$PROJECT_ROOT" >/dev/null
run_cli create-tasks \
  --project-root "$PROJECT_ROOT" \
  --source "$INPUT_FILE" \
  --pipeline-id handoff \
  --batch-size 1

uv run --project "$SKILL_DIR" --no-dev python - "$PROJECT_ROOT" <<'PY'
import json
import sys
from pathlib import Path

from annotation_pipeline_skill.core.models import ArtifactRef
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.store.file_store import FileStore

project_root = Path(sys.argv[1])
store = FileStore(project_root / ".annotation-pipeline")
task = store.load_task("handoff-000001")
task.status = TaskStatus.ACCEPTED
store.save_task(task)

payload_path = store.root / "artifact_payloads/handoff-000001/handoff-000001_annotation_result.json"
payload_path.parent.mkdir(parents=True, exist_ok=True)
payload_path.write_text(
    json.dumps({"task_id": task.task_id, "text": '{"labels":[{"text":"alpha","type":"example"}]}'}),
    encoding="utf-8",
)
store.append_artifact(
    ArtifactRef.new(
        task_id=task.task_id,
        kind="annotation_result",
        path=str(payload_path.relative_to(store.root)),
        content_type="application/json",
        metadata={"provider": "handoff-verification"},
    )
)
PY

run_cli export training-data \
  --project-root "$PROJECT_ROOT" \
  --project-id handoff \
  --export-id handoff-export >/dev/null
run_cli report readiness --project-root "$PROJECT_ROOT" --project-id handoff >/dev/null
run_cli coordinator report --project-root "$PROJECT_ROOT" --project-id handoff >/dev/null
run_cli runtime status --project-root "$PROJECT_ROOT" >/dev/null

run_cli serve --project-root "$PROJECT_ROOT" --host 127.0.0.1 --port "$PORT" >/dev/null 2>&1 &
SERVER_PID="$!"

uv run --project "$SKILL_DIR" --no-dev python - "$PORT" <<'PY'
import json
import sys
import time
import urllib.request

port = int(sys.argv[1])
base = f"http://127.0.0.1:{port}"


def request(path: str, payload: dict | None = None) -> dict:
    data = None
    headers = {}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
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

projects = request("/api/projects")
if not any(project.get("project_id") == "handoff" for project in projects.get("projects", [])):
    raise SystemExit(f"handoff project missing from /api/projects: {projects}")

kanban = request("/api/kanban?project=handoff")
if kanban.get("project_id") != "handoff" or "columns" not in kanban:
    raise SystemExit(f"invalid kanban payload: {kanban}")

providers = request("/api/providers")
for key in ("config_valid", "profiles", "targets", "diagnostics"):
    if key not in providers:
        raise SystemExit(f"missing provider key {key}: {providers}")

coordinator = request("/api/coordinator?project=handoff")
if coordinator.get("project_id") != "handoff":
    raise SystemExit(f"invalid coordinator project: {coordinator}")

readiness = request("/api/readiness?project=handoff")
for key in ("ready_for_training", "accepted_count", "recommended_next_action"):
    if key not in readiness:
        raise SystemExit(f"missing readiness key {key}: {readiness}")

events = request("/api/events?project=handoff")
if "events" not in events:
    raise SystemExit(f"invalid event payload: {events}")

runtime = request("/api/runtime")
for key in ("runtime_status", "queue_counts", "capacity"):
    if key not in runtime:
        raise SystemExit(f"missing runtime key {key}: {runtime}")
PY

test -f "$PROJECT_ROOT/.annotation-pipeline/exports/handoff-export/training_data.jsonl"

echo "agent handoff verification passed: $WORK_DIR"
