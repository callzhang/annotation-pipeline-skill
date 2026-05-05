#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(mktemp -d /tmp/annotation-export-verify-XXXXXX)"
INPUT_FILE="$PROJECT_ROOT/input.jsonl"
MANIFEST_JSON="$PROJECT_ROOT/export-manifest.json"

cd "$ROOT_DIR"

printf '{"text":"alpha","source_dataset":"demo"}\n{"text":"beta","source_dataset":"demo"}\n' > "$INPUT_FILE"

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline init --project-root "$PROJECT_ROOT"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline create-tasks --project-root "$PROJECT_ROOT" --source "$INPUT_FILE" --pipeline-id export-verify --batch-size 1

python - "$PROJECT_ROOT" <<'PY'
import json
import sys
from pathlib import Path

from annotation_pipeline_skill.core.models import ArtifactRef
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.store.file_store import FileStore

project_root = Path(sys.argv[1])
store = FileStore(project_root / ".annotation-pipeline")

task_one = store.load_task("export-verify-000001")
task_one.status = TaskStatus.ACCEPTED
store.save_task(task_one)

payload_path = store.root / "artifact_payloads/export-verify-000001/export-verify-000001-attempt-1_annotation_result.json"
payload_path.parent.mkdir(parents=True, exist_ok=True)
payload_path.write_text(
    json.dumps({"task_id": task_one.task_id, "text": '{"labels":[{"text":"alpha"}]}'}),
    encoding="utf-8",
)
store.append_artifact(
    ArtifactRef.new(
        task_id=task_one.task_id,
        kind="annotation_result",
        path=str(payload_path.relative_to(store.root)),
        content_type="application/json",
        metadata={"provider": "verify"},
    )
)

task_two = store.load_task("export-verify-000002")
task_two.status = TaskStatus.ACCEPTED
store.save_task(task_two)
PY

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline export training-data --project-root "$PROJECT_ROOT" --project-id export-verify --export-id export-1 > "$MANIFEST_JSON"

python - "$PROJECT_ROOT" "$MANIFEST_JSON" <<'PY'
import json
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
store_root = project_root / ".annotation-pipeline"
training_path = store_root / "exports/export-1/training_data.jsonl"
rows = [json.loads(line) for line in training_path.read_text(encoding="utf-8").splitlines() if line.strip()]

if manifest["task_ids_included"] != ["export-verify-000001"]:
    raise SystemExit(f"unexpected included tasks: {manifest['task_ids_included']}")
if manifest["task_ids_excluded"] != [{"task_id": "export-verify-000002", "reason": "missing_annotation_result"}]:
    raise SystemExit(f"unexpected excluded tasks: {manifest['task_ids_excluded']}")
if len(rows) != 1 or rows[0]["annotation"] != '{"labels":[{"text":"alpha"}]}':
    raise SystemExit(f"unexpected training rows: {rows}")
if not (store_root / "exports/export-1/manifest.json").exists():
    raise SystemExit("manifest.json was not saved")

print(f"training data export verification passed: {project_root}")
PY
