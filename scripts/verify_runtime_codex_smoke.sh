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

set +e
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime once --project-root "$PROJECT_ROOT" > "$RUN_JSON" 2> "$RUN_ERR"
RUNTIME_EXIT="$?"
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

latest_cycle = snapshot.get("cycle_stats", [{}])[-1] if snapshot.get("cycle_stats") else {}
if latest_cycle.get("failed") != 0:
    dump_diagnostics("latest runtime cycle recorded failures")
    raise SystemExit(1)
if latest_cycle.get("accepted") != 1:
    dump_diagnostics(f"expected latest cycle accepted=1, got {latest_cycle.get('accepted')!r}")
    raise SystemExit(1)
if snapshot.get("queue_counts", {}).get("accepted") != 1:
    dump_diagnostics(f"expected accepted queue count 1, got {snapshot.get('queue_counts')!r}")
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
if task.get("status") != "accepted":
    dump_diagnostics(f"expected accepted task, got {task.get('status')!r}")
    raise SystemExit(1)

print(f"real codex runtime smoke passed: {project_root}")
PY
