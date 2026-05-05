#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(mktemp -d /tmp/annotation-runtime-codex-smoke-XXXXXX)"
INPUT_FILE="$PROJECT_ROOT/input.jsonl"
RUN_JSON="$PROJECT_ROOT/runtime-once.json"
RUN_ERR="$PROJECT_ROOT/runtime-once.stderr"
STATUS_JSON="$PROJECT_ROOT/runtime-status.json"
STATUS_ERR="$PROJECT_ROOT/runtime-status.stderr"

cd "$ROOT_DIR"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI is not available on PATH; install/authenticate Codex before running real smoke verification" >&2
  exit 2
fi

printf '{"text":"Codex smoke test row","source_dataset":"codex-smoke"}\n' > "$INPUT_FILE"

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline init --project-root "$PROJECT_ROOT"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline create-tasks --project-root "$PROJECT_ROOT" --source "$INPUT_FILE" --pipeline-id codex-smoke --batch-size 1

cat > "$PROJECT_ROOT/.annotation-pipeline/llm_profiles.yaml" <<'YAML'
profiles:
  local_codex:
    provider: local_cli
    cli_kind: codex
    cli_binary: codex
    model: gpt-5.4-mini
    reasoning_effort: none
    timeout_seconds: 900
    no_progress_timeout_seconds: 30
targets:
  annotation: local_codex
  qc: local_codex
  coordinator: local_codex
limits:
  local_cli_global_concurrency: 4
YAML

set +e
RUNTIME_EXIT="0"
for cycle_index in 1 2 3; do
  CYCLE_JSON="$PROJECT_ROOT/runtime-once-$cycle_index.json"
  CYCLE_ERR="$PROJECT_ROOT/runtime-once-$cycle_index.stderr"
  UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime once --project-root "$PROJECT_ROOT" > "$CYCLE_JSON" 2> "$CYCLE_ERR"
  RUNTIME_EXIT="$?"
  cp "$CYCLE_JSON" "$RUN_JSON"
  cp "$CYCLE_ERR" "$RUN_ERR"
  if [[ "$RUNTIME_EXIT" != "0" ]]; then
    break
  fi
  ACCEPTED_COUNT="$(python - "$CYCLE_JSON" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(payload.get("queue_counts", {}).get("accepted", 0))
PY
)"
  if [[ "$ACCEPTED_COUNT" == "1" ]]; then
    break
  fi
done
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime status --project-root "$PROJECT_ROOT" > "$STATUS_JSON" 2> "$STATUS_ERR"
STATUS_EXIT="$?"
set -e

python - "$PROJECT_ROOT" "$RUN_JSON" "$RUN_ERR" "$STATUS_JSON" "$STATUS_ERR" "$RUNTIME_EXIT" "$STATUS_EXIT" <<'PY'
import json
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
run_json = Path(sys.argv[2])
run_err = Path(sys.argv[3])
status_json = Path(sys.argv[4])
status_err = Path(sys.argv[5])
runtime_exit = int(sys.argv[6])
status_exit = int(sys.argv[7])
store_root = project_root / ".annotation-pipeline"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_json(path: Path):
    try:
        return json.loads(read_text(path))
    except Exception:
        return None


def dump_diagnostics(message: str) -> None:
    print(f"real codex smoke failed: {message}", file=sys.stderr)
    print(f"project_root={project_root}", file=sys.stderr)
    print(f"runtime_exit={runtime_exit}", file=sys.stderr)
    print(f"runtime_stderr={read_text(run_err)[-4000:]}", file=sys.stderr)
    print(f"runtime_stdout={read_text(run_json)[-4000:]}", file=sys.stderr)
    print(f"status_exit={status_exit}", file=sys.stderr)
    print(f"status_stderr={read_text(status_err)[-4000:]}", file=sys.stderr)
    print(f"status_stdout={read_text(status_json)[-4000:]}", file=sys.stderr)
    cycles = store_root / "runtime" / "cycle_stats.jsonl"
    print(f"cycle_stats={read_text(cycles)[-4000:]}", file=sys.stderr)
    for path in sorted((store_root / "tasks").glob("*.json")):
        print(f"task {path.name}={read_text(path)[-4000:]}", file=sys.stderr)
    for path in sorted((store_root / "events").glob("*.jsonl")):
        print(f"events {path.name}={read_text(path)[-4000:]}", file=sys.stderr)
    for path in sorted((store_root / "attempts").glob("*.jsonl")):
        print(f"attempts {path.name}={read_text(path)[-4000:]}", file=sys.stderr)
    for path in sorted((store_root / "artifacts").glob("*.jsonl")):
        print(f"artifacts {path.name}={read_text(path)[-4000:]}", file=sys.stderr)


if runtime_exit != 0:
    dump_diagnostics("runtime once command exited non-zero")
    raise SystemExit(1)
if status_exit != 0:
    dump_diagnostics("runtime status command exited non-zero")
    raise SystemExit(1)

snapshot = load_json(run_json)
status = load_json(status_json)
if not isinstance(snapshot, dict) or not isinstance(status, dict):
    dump_diagnostics("runtime output was not valid JSON")
    raise SystemExit(1)

cycles = snapshot.get("cycle_stats", [])
latest_cycle = cycles[-1] if cycles else {}
if any(cycle.get("failed") != 0 for cycle in cycles):
    dump_diagnostics("runtime cycle recorded provider failures")
    raise SystemExit(1)
if status.get("capacity", {}).get("active_count") != 0:
    dump_diagnostics(f"expected no active runs, got {status.get('capacity')!r}")
    raise SystemExit(1)

task_files = sorted((store_root / "tasks").glob("*.json"))
attempt_files = sorted((store_root / "attempts").glob("*.jsonl"))
artifact_files = sorted((store_root / "artifacts").glob("*.jsonl"))
event_files = sorted((store_root / "events").glob("*.jsonl"))
if len(task_files) != 1 or len(attempt_files) != 1 or len(artifact_files) != 1 or len(event_files) != 1:
    dump_diagnostics("expected one task, attempt file, artifact file, and event file")
    raise SystemExit(1)

task = json.loads(read_text(task_files[0]))
if task.get("status") not in {"accepted", "pending"}:
    dump_diagnostics(f"expected accepted task or pending task with QC feedback, got {task.get('status')!r}")
    raise SystemExit(1)

attempts = [json.loads(line) for line in read_text(attempt_files[0]).splitlines() if line.strip()]
stages = {attempt.get("stage") for attempt in attempts}
providers = {attempt.get("provider_id") for attempt in attempts}
statuses = {attempt.get("status") for attempt in attempts}
if "annotation" not in stages or "qc" not in stages:
    dump_diagnostics(f"expected both annotation and qc attempts, got stages {sorted(stages)}")
    raise SystemExit(1)
if providers != {"local_codex"}:
    dump_diagnostics(f"expected all attempts to use local_codex, got providers {sorted(providers)}")
    raise SystemExit(1)
if statuses != {"succeeded"}:
    dump_diagnostics(f"expected all attempts to succeed, got statuses {sorted(statuses)}")
    raise SystemExit(1)

artifacts = [json.loads(line) for line in read_text(artifact_files[0]).splitlines() if line.strip()]
kinds = {artifact.get("kind") for artifact in artifacts}
if "annotation_result" not in kinds or "qc_result" not in kinds:
    dump_diagnostics(f"expected annotation_result and qc_result artifacts, got kinds {sorted(kinds)}")
    raise SystemExit(1)

if task.get("status") == "pending":
    feedback_files = sorted((store_root / "feedback").glob("*.jsonl"))
    if len(feedback_files) != 1 or not read_text(feedback_files[0]).strip():
        dump_diagnostics("expected QC feedback for pending smoke task")
        raise SystemExit(1)

print(f"real codex runtime smoke passed: {project_root} ({task.get('status')})")
PY
