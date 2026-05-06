#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MEMORY_NER_ROOT="${MEMORY_NER_ROOT:-/home/derek/Projects/memory-ner}"
TASK_ROOT_PRIMARY="$MEMORY_NER_ROOT/data/derived/annotation_projects/v2/tasks"
TASK_ROOT_FALLBACK="$MEMORY_NER_ROOT/data/derived/annotation_tasks"
PROJECT_ROOT="${MEMORY_NER_E2E_PROJECT_ROOT:-$(mktemp -d /tmp/annotation-memory-ner-e2e-XXXXXX)}"
INPUT_FILE="$PROJECT_ROOT/input.jsonl"
TRUTH_FILE="$PROJECT_ROOT/truth.jsonl"
RUNTIME_JSON="$PROJECT_ROOT/runtime.jsonl"
STATUS_JSON="$PROJECT_ROOT/status.json"
REPORT_JSON="$PROJECT_ROOT/accepted-e2e-report.json"
LIMIT="${MEMORY_NER_E2E_LIMIT:-10}"
MAX_CYCLES="${MEMORY_NER_E2E_MAX_CYCLES:-7}"
MIN_ACCEPTED="${MEMORY_NER_E2E_MIN_ACCEPTED:-8}"

cleanup() {
  if [[ "${KEEP_MEMORY_NER_E2E_PROJECT:-0}" != "1" && -z "${MEMORY_NER_E2E_PROJECT_ROOT:-}" ]]; then
    rm -rf "$PROJECT_ROOT"
  fi
}
trap cleanup EXIT

cd "$ROOT_DIR"

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "DEEPSEEK_API_KEY is not set; source ~/.agents/auth/deepseek.env or export it before running this eval" >&2
  exit 2
fi

mkdir -p "$PROJECT_ROOT"

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . python - \
  "$TASK_ROOT_PRIMARY" "$TASK_ROOT_FALLBACK" "$INPUT_FILE" "$TRUTH_FILE" "$LIMIT" <<'PY'
import json
import sys
from pathlib import Path

primary = Path(sys.argv[1])
fallback = Path(sys.argv[2])
input_file = Path(sys.argv[3])
truth_file = Path(sys.argv[4])
limit = int(sys.argv[5])


def iter_task_files(root: Path):
    if root.exists():
        yield from sorted(root.rglob("*.task.json"))


def read_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def entity_inventory(gold_entities: dict) -> list[str]:
    return sorted(str(label) for label, values in gold_entities.items() if values)


selected: list[tuple[dict, dict]] = []
for task_file in list(iter_task_files(primary)) + list(iter_task_files(fallback)):
    task = json.loads(task_file.read_text(encoding="utf-8"))
    if task.get("status") not in {"accepted", "merged"}:
        continue
    output_file = Path(str(task.get("output_file") or ""))
    if not output_file.exists():
        continue
    for row_index, row in enumerate(read_rows(output_file), start=1):
        text = row.get("input") or row.get("text")
        output = row.get("output")
        if not isinstance(text, str) or not isinstance(output, dict):
            continue
        gold_entities = output.get("entities")
        if not isinstance(gold_entities, dict) or not any(gold_entities.values()):
            continue
        eval_id = f"memory-ner-eval-{len(selected) + 1:03d}"
        guidance = {
            "output_schema": {
                "entities": {"<entity_type>": ["exact text span"]},
                "classifications": [],
                "json_structures": [],
                "relations": [],
            },
            "allowed_entity_types": entity_inventory(gold_entities),
            "rules": [
                "Return JSON only.",
                "Extract exact entity text spans from the input text.",
                "Do not include entities that are not present as exact text in the input.",
                "Use empty arrays for classifications, json_structures, and relations unless the input clearly requires them.",
            ],
        }
        selected.append(
            (
                {
                    "eval_id": eval_id,
                    "text": text,
                    "source_dataset": row.get("source_dataset"),
                    "source_path": row.get("source_path"),
                    "modality": "text",
                    "annotation_types": ["entity_span", "structured_json"],
                    "annotation_guidance": guidance,
                    "source_task_id": task.get("task_id"),
                    "source_task_status": task.get("status"),
                    "source_row_index": row_index,
                },
                {
                    "eval_id": eval_id,
                    "source_task_id": task.get("task_id"),
                    "source_task_status": task.get("status"),
                    "source_row_index": row_index,
                    "gold_output": output,
                },
            )
        )
        if len(selected) >= limit:
            break
    if len(selected) >= limit:
        break

if len(selected) < limit:
    raise SystemExit(f"only found {len(selected)} accepted/merged truth rows, need {limit}")

input_file.write_text(
    "".join(json.dumps(item[0], ensure_ascii=False, sort_keys=True) + "\n" for item in selected),
    encoding="utf-8",
)
truth_file.write_text(
    "".join(json.dumps(item[1], ensure_ascii=False, sort_keys=True) + "\n" for item in selected),
    encoding="utf-8",
)
print(f"selected {len(selected)} memory-ner truth rows from accepted/merged annotation-manager tasks")
PY

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline init --project-root "$PROJECT_ROOT" >/dev/null
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline create-tasks \
  --project-root "$PROJECT_ROOT" \
  --source "$INPUT_FILE" \
  --pipeline-id memory-ner-accepted-e2e \
  --batch-size 1 \
  --annotation-type entity_span \
  --annotation-type structured_json

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . python - "$PROJECT_ROOT/.annotation-pipeline/workflow.yaml" <<'PY'
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text(encoding="utf-8"))
data["runtime"]["max_concurrent_tasks"] = 10
data["runtime"]["max_starts_per_cycle"] = 10
data["runtime"]["loop_interval_seconds"] = 0
path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
PY

cat > "$PROJECT_ROOT/.annotation-pipeline/llm_profiles.yaml" <<'YAML'
profiles:
  deepseek_default:
    provider: openai_compatible
    provider_flavor: deepseek
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
    timeout_seconds: 180
    max_retries: 1
targets:
  annotation: deepseek_default
  qc: deepseek_default
  coordinator: deepseek_default
limits:
  local_cli_global_concurrency: 1
YAML

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline provider doctor --project-root "$PROJECT_ROOT" >/dev/null
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime run \
  --project-root "$PROJECT_ROOT" \
  --max-cycles "$MAX_CYCLES" > "$RUNTIME_JSON"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime status \
  --project-root "$PROJECT_ROOT" > "$STATUS_JSON"

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . python - \
  "$PROJECT_ROOT" "$TRUTH_FILE" "$STATUS_JSON" "$REPORT_JSON" "$MIN_ACCEPTED" <<'PY'
import json
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
truth_file = Path(sys.argv[2])
status_file = Path(sys.argv[3])
report_file = Path(sys.argv[4])
min_accepted = int(sys.argv[5])
store_root = project_root / ".annotation-pipeline"

truth_rows = [json.loads(line) for line in truth_file.read_text(encoding="utf-8").splitlines() if line.strip()]
status = json.loads(status_file.read_text(encoding="utf-8"))
tasks = []
for task_file in sorted((store_root / "tasks").glob("*.json")):
    task = json.loads(task_file.read_text(encoding="utf-8"))
    feedback_file = store_root / "feedback" / f"{task['task_id']}.jsonl"
    feedback_count = 0
    latest_feedback = None
    if feedback_file.exists():
        feedback_lines = [json.loads(line) for line in feedback_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        feedback_count = len(feedback_lines)
        latest_feedback = feedback_lines[-1] if feedback_lines else None
    tasks.append(
        {
            "task_id": task["task_id"],
            "status": task["status"],
            "current_attempt": task.get("current_attempt", 0),
            "feedback_count": feedback_count,
            "latest_feedback_message": latest_feedback.get("message") if latest_feedback else None,
        }
    )

accepted_count = sum(1 for task in tasks if task["status"] == "accepted")
report = {
    "project_root": str(project_root),
    "truth_file": str(truth_file),
    "tasks": len(tasks),
    "truth_rows": len(truth_rows),
    "accepted_count": accepted_count,
    "minimum_accepted": min_accepted,
    "queue_counts": status["queue_counts"],
    "tasks_detail": tasks,
}
report_file.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

if accepted_count < min_accepted:
    print(f"memory-ner accepted e2e failed: accepted={accepted_count}/{len(tasks)} below minimum {min_accepted}", file=sys.stderr)
    print(f"project_root={project_root}", file=sys.stderr)
    print(f"report={report_file}", file=sys.stderr)
    raise SystemExit(1)

print(f"memory-ner accepted e2e passed: {report_file}; accepted={accepted_count}/{len(tasks)}")
PY
