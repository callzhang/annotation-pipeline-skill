#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(mktemp -d /tmp/annotation-runtime-progress-XXXXXX)"
INPUT_FILE="$PROJECT_ROOT/input.jsonl"
CYCLE_ONE="$PROJECT_ROOT/cycle-one.json"
CYCLE_TWO="$PROJECT_ROOT/cycle-two.json"
STATUS_JSON="$PROJECT_ROOT/status.json"

cd "$ROOT_DIR"

printf '{"text":"alpha","source_dataset":"demo"}\n{"text":"beta","source_dataset":"demo"}\n{"text":"gamma","source_dataset":"demo"}\n' > "$INPUT_FILE"

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline init --project-root "$PROJECT_ROOT"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline create-tasks --project-root "$PROJECT_ROOT" --source "$INPUT_FILE" --pipeline-id verify-progress

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . python - "$PROJECT_ROOT" "$CYCLE_ONE" "$CYCLE_TWO" "$STATUS_JSON" <<'PY'
import asyncio
import json
import sys
from pathlib import Path

from annotation_pipeline_skill.core.runtime import RuntimeConfig
from annotation_pipeline_skill.llm.client import LLMGenerateResult
from annotation_pipeline_skill.runtime.local_scheduler import LocalRuntimeScheduler
from annotation_pipeline_skill.store.file_store import FileStore


class ScriptedClient:
    def __init__(self):
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        if self.calls == 2:
            text = json.dumps(
                {
                    "passed": False,
                    "message": "missing entity",
                    "category": "quality",
                    "severity": "warning",
                    "suggested_action": "annotator_rerun",
                    "target": {"field": "labels"},
                },
                separators=(",", ":"),
            )
        elif self.calls in {4, 6, 8}:
            text = json.dumps({"passed": True, "summary": "acceptable"}, separators=(",", ":"))
        elif "missing entity" in (request.prompt or ""):
            text = json.dumps({"labels": [{"text": "alpha", "type": "ENTITY"}]}, separators=(",", ":"))
        else:
            text = json.dumps({"labels": []}, separators=(",", ":"))
        return LLMGenerateResult(
            runtime="scripted",
            provider="scripted_provider",
            model="scripted-model",
            continuity_handle="scripted-thread",
            final_text=text,
            usage={"total_tokens": 1},
            raw_response={"call": self.calls},
            diagnostics={},
        )


project_root = Path(sys.argv[1])
cycle_one_path = Path(sys.argv[2])
cycle_two_path = Path(sys.argv[3])
status_path = Path(sys.argv[4])

store = FileStore(project_root / ".annotation-pipeline")
client = ScriptedClient()
scheduler = LocalRuntimeScheduler(
    store=store,
    client_factory=lambda target: client,
    config=RuntimeConfig(max_concurrent_tasks=4, max_starts_per_cycle=2),
)

cycle_one = scheduler.run_once(stage_target="annotation")
cycle_one_path.write_text(json.dumps(cycle_one.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
cycle_two = scheduler.run_once(stage_target="annotation")
cycle_two_path.write_text(json.dumps(cycle_two.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
status_path.write_text(json.dumps((store.load_runtime_snapshot() or cycle_two).to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
PY

python - "$PROJECT_ROOT" "$CYCLE_ONE" "$CYCLE_TWO" "$STATUS_JSON" <<'PY'
import json
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
cycle_one = json.load(open(sys.argv[2], encoding="utf-8"))
cycle_two = json.load(open(sys.argv[3], encoding="utf-8"))
status = json.load(open(sys.argv[4], encoding="utf-8"))
store_root = project_root / ".annotation-pipeline"


def assert_equal(actual, expected, label):
    if actual != expected:
        raise SystemExit(f"{label}: expected {expected!r}, got {actual!r}")


assert_equal(cycle_one["queue_counts"]["accepted"], 1, "cycle one accepted count")
assert_equal(cycle_one["queue_counts"]["pending"], 2, "cycle one pending count")
assert_equal(cycle_one["cycle_stats"][-1]["started"], 2, "cycle one started")
assert_equal(cycle_one["cycle_stats"][-1]["accepted"], 1, "cycle one accepted stats")
assert_equal(cycle_one["cycle_stats"][-1]["failed"], 0, "cycle one failed stats")

assert_equal(cycle_two["queue_counts"]["accepted"], 3, "cycle two accepted count")
assert_equal(cycle_two["queue_counts"]["pending"], 0, "cycle two pending count")
assert_equal(cycle_two["cycle_stats"][-1]["started"], 2, "cycle two started")
assert_equal(cycle_two["cycle_stats"][-1]["accepted"], 2, "cycle two accepted stats")
assert_equal(cycle_two["cycle_stats"][-1]["failed"], 0, "cycle two failed stats")

assert_equal(status["queue_counts"]["accepted"], 3, "final accepted count")
assert_equal(status["capacity"]["active_count"], 0, "final active count")
assert_equal(status["active_runs"], [], "final active runs")

cycles = status["cycle_stats"]
if len(cycles) != 2:
    raise SystemExit(f"expected 2 cycle stats, got {len(cycles)}")

task_files = sorted((store_root / "tasks").glob("*.json"))
attempt_files = sorted((store_root / "attempts").glob("*.jsonl"))
artifact_files = sorted((store_root / "artifacts").glob("*.jsonl"))
event_files = sorted((store_root / "events").glob("*.jsonl"))
assert_equal(len(task_files), 3, "task file count")
assert_equal(len(attempt_files), 3, "attempt file count")
assert_equal(len(artifact_files), 3, "artifact file count")
assert_equal(len(event_files), 3, "event file count")

for task_file in task_files:
    task = json.load(open(task_file, encoding="utf-8"))
    assert_equal(task["status"], "accepted", f"{task_file.name} status")
    minimum_attempts = 4 if task["task_id"] == "verify-progress-000001" else 2
    if task.get("current_attempt") < minimum_attempts:
        raise SystemExit(f"{task_file.name} expected current_attempt >= {minimum_attempts}")

feedback_files = sorted((store_root / "feedback").glob("*.jsonl"))
if len(feedback_files) != 1:
    raise SystemExit(f"expected one feedback file, got {len(feedback_files)}")
feedback_text = feedback_files[0].read_text(encoding="utf-8")
if "missing entity" not in feedback_text:
    raise SystemExit("expected QC feedback text to include missing entity")

print(f"runtime progress verification passed: {project_root}")
PY
