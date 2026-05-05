#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(mktemp -d /tmp/annotation-export-verify-XXXXXX)"
INPUT_FILE="$PROJECT_ROOT/input.jsonl"
MANIFEST_JSON="$PROJECT_ROOT/export-manifest.json"
READINESS_JSON="$PROJECT_ROOT/readiness.json"

cd "$ROOT_DIR"

printf '{"text":"alpha","source_dataset":"demo"}\n{"text":"beta","source_dataset":"demo"}\n{"text":"gamma","source_dataset":"demo"}\n' > "$INPUT_FILE"

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

task_three = store.load_task("export-verify-000003")
task_three.status = TaskStatus.ACCEPTED
store.save_task(task_three)
invalid_payload_path = store.root / "artifact_payloads/export-verify-000003/export-verify-000003-attempt-1_annotation_result.json"
invalid_payload_path.parent.mkdir(parents=True, exist_ok=True)
invalid_payload_path.write_text(json.dumps({"task_id": task_three.task_id, "text": "not json"}), encoding="utf-8")
store.append_artifact(
    ArtifactRef.new(
        task_id=task_three.task_id,
        kind="annotation_result",
        path=str(invalid_payload_path.relative_to(store.root)),
        content_type="application/json",
        metadata={"provider": "verify"},
    )
)
PY

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline export training-data --project-root "$PROJECT_ROOT" --project-id export-verify --export-id export-1 > "$MANIFEST_JSON"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline report readiness --project-root "$PROJECT_ROOT" --project-id export-verify > "$READINESS_JSON"

python - "$PROJECT_ROOT" "$MANIFEST_JSON" "$READINESS_JSON" <<'PY'
import json
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
readiness = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
store_root = project_root / ".annotation-pipeline"
training_path = store_root / "exports/export-1/training_data.jsonl"
rows = [json.loads(line) for line in training_path.read_text(encoding="utf-8").splitlines() if line.strip()]

if manifest["task_ids_included"] != ["export-verify-000001"]:
    raise SystemExit(f"unexpected included tasks: {manifest['task_ids_included']}")
expected_excluded = [
    {"task_id": "export-verify-000002", "reason": "missing_annotation_result"},
    {
        "task_id": "export-verify-000003",
        "reason": "invalid_training_row",
        "errors": ["annotation_string_must_be_json"],
    },
]
if manifest["task_ids_excluded"] != expected_excluded:
    raise SystemExit(f"unexpected excluded tasks: {manifest['task_ids_excluded']}")
if manifest["schema_version"] != "jsonl-training-v2":
    raise SystemExit(f"unexpected schema version: {manifest['schema_version']}")
if manifest["validator_version"] != "local-export-v2":
    raise SystemExit(f"unexpected validator version: {manifest['validator_version']}")
if manifest["validation_summary"]["row_errors"] != [
    {"task_id": "export-verify-000003", "errors": ["annotation_string_must_be_json"]}
]:
    raise SystemExit(f"unexpected row errors: {manifest['validation_summary']['row_errors']}")
if len(rows) != 1 or rows[0]["annotation"] != '{"labels":[{"text":"alpha"}]}':
    raise SystemExit(f"unexpected training rows: {rows}")
if not (store_root / "exports/export-1/manifest.json").exists():
    raise SystemExit("manifest.json was not saved")
if readiness["ready_for_training"] is not False:
    raise SystemExit(f"expected readiness to require blocker repair: {readiness}")
if readiness["recommended_next_action"] != "fix_export_blockers":
    raise SystemExit(f"unexpected readiness action: {readiness}")
if readiness["validation_blockers"] != expected_excluded:
    raise SystemExit(f"unexpected readiness blockers: {readiness['validation_blockers']}")

print(f"training data export verification passed: {project_root}")
PY
