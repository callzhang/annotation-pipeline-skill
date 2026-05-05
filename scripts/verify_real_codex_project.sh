#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(mktemp -d /tmp/annotation-real-codex-project-XXXXXX)"
INPUT_FILE="$PROJECT_ROOT/input.jsonl"
STATUS_JSON="$PROJECT_ROOT/runtime-status.json"
EXPORT_JSON="$PROJECT_ROOT/export.json"

cd "$ROOT_DIR"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI is not available on PATH; install/authenticate Codex before running real project verification" >&2
  exit 2
fi

python - "$INPUT_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
rows = [
    {"id": f"row-{index:02d}", "text": f"Project Apollo sample sentence {index}", "source_dataset": "real-codex"}
    for index in range(1, 11)
]
path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
PY

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline init --project-root "$PROJECT_ROOT"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline create-tasks \
  --project-root "$PROJECT_ROOT" \
  --source "$INPUT_FILE" \
  --pipeline-id real-codex \
  --batch-size 1 \
  --annotation-type entity_span

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
  human_review: local_codex
limits:
  local_cli_global_concurrency: 2
YAML

for cycle_index in 1 2 3 4 5 6 7 8; do
  CYCLE_JSON="$PROJECT_ROOT/runtime-cycle-$cycle_index.json"
  CYCLE_ERR="$PROJECT_ROOT/runtime-cycle-$cycle_index.stderr"
  set +e
  UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime once \
    --project-root "$PROJECT_ROOT" > "$CYCLE_JSON" 2> "$CYCLE_ERR"
  CYCLE_EXIT="$?"
  set -e
  if [[ "$CYCLE_EXIT" != "0" ]]; then
    echo "runtime cycle $cycle_index failed; diagnostics are under $PROJECT_ROOT" >&2
    tail -200 "$CYCLE_ERR" >&2 || true
    exit "$CYCLE_EXIT"
  fi
  PENDING_COUNT="$(python - "$CYCLE_JSON" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
counts = payload.get("queue_counts", {})
print(counts.get("pending", 0) + counts.get("annotating", 0) + counts.get("validating", 0) + counts.get("qc", 0))
PY
)"
  if [[ "$PENDING_COUNT" == "0" ]]; then
    break
  fi
done

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime status \
  --project-root "$PROJECT_ROOT" > "$STATUS_JSON"

set +e
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline export training-data \
  --project-root "$PROJECT_ROOT" \
  --project-id real-codex > "$EXPORT_JSON"
EXPORT_EXIT="$?"
set -e

python - "$PROJECT_ROOT" "$STATUS_JSON" "$EXPORT_JSON" "$EXPORT_EXIT" <<'PY'
import json
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
status_path = Path(sys.argv[2])
export_path = Path(sys.argv[3])
export_exit = int(sys.argv[4])
store_root = project_root / ".annotation-pipeline"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def fail(message: str) -> None:
    print(f"real Codex project verification failed: {message}", file=sys.stderr)
    print(f"project_root={project_root}", file=sys.stderr)
    print(f"runtime_status={read(status_path)[-4000:]}", file=sys.stderr)
    print(f"export={read(export_path)[-4000:]}", file=sys.stderr)
    print(f"cycle_stats={read(store_root / 'runtime' / 'cycle_stats.jsonl')[-4000:]}", file=sys.stderr)
    for directory in ("tasks", "attempts", "artifacts", "feedback", "events"):
        for path in sorted((store_root / directory).glob("*")):
            print(f"{directory}/{path.name}={read(path)[-3000:]}", file=sys.stderr)
    raise SystemExit(1)


status = json.loads(read(status_path))
if status.get("capacity", {}).get("active_count") != 0:
    fail("runtime left active runs behind")
if any(cycle.get("failed") for cycle in status.get("cycle_stats", [])):
    fail("one or more runtime cycles recorded provider failures")

tasks = [json.loads(read(path)) for path in sorted((store_root / "tasks").glob("*.json"))]
if len(tasks) != 10:
    fail(f"expected 10 tasks, got {len(tasks)}")
unexpected = [task["task_id"] for task in tasks if task.get("status") not in {"accepted", "pending", "human_review"}]
if unexpected:
    fail(f"unexpected task states after Codex run: {unexpected}")

attempt_files = sorted((store_root / "attempts").glob("*.jsonl"))
artifact_files = sorted((store_root / "artifacts").glob("*.jsonl"))
if len(attempt_files) != 10 or len(artifact_files) != 10:
    fail("every task should have attempts and artifacts")

for path in attempt_files:
    attempts = [json.loads(line) for line in read(path).splitlines() if line.strip()]
    stages = {attempt.get("stage") for attempt in attempts}
    providers = {attempt.get("provider_id") for attempt in attempts}
    if "annotation" not in stages or "qc" not in stages:
        fail(f"{path.name} missing annotation or QC attempt")
    if providers != {"local_codex"}:
        fail(f"{path.name} used unexpected providers: {providers}")

accepted = [task for task in tasks if task.get("status") == "accepted"]
if not accepted:
    fail("expected at least one accepted task to prove export path")
if export_exit != 0:
    fail("training data export command failed")

manifest = json.loads(read(export_path))
if manifest.get("project_id") != "real-codex":
    fail("export manifest project_id mismatch")
if not manifest.get("task_ids_included"):
    fail("export manifest did not include accepted tasks")

print(f"real Codex project verification passed: {project_root}; accepted={len(accepted)}")
PY
