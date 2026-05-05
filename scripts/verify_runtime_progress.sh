#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(mktemp -d /tmp/annotation-runtime-progress-XXXXXX)"
INPUT_FILE="$PROJECT_ROOT/input.jsonl"
FAKE_CODEX="$PROJECT_ROOT/fake-codex"

cd "$ROOT_DIR"

cat > "$FAKE_CODEX" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' '{"type":"thread.started","thread_id":"verify-thread"}'
printf '%s\n' '{"type":"item.completed","item":{"type":"agent_message","text":"{\"labels\":[]}"}}'
printf '%s\n' '{"type":"turn.completed","usage":{"total_tokens":1}}'
SH
chmod +x "$FAKE_CODEX"

printf '{"text":"alpha","source_dataset":"demo"}\n{"text":"beta","source_dataset":"demo"}\n{"text":"gamma","source_dataset":"demo"}\n' > "$INPUT_FILE"

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline init --project-root "$PROJECT_ROOT"

cat > "$PROJECT_ROOT/.annotation-pipeline/llm_profiles.yaml" <<YAML
profiles:
  fake_codex:
    provider: local_cli
    cli_kind: codex
    cli_binary: $FAKE_CODEX
    model: test-model
    reasoning_effort: none
    timeout_seconds: 30
targets:
  annotation: fake_codex
  qc: fake_codex
  coordinator: fake_codex
limits:
  local_cli_global_concurrency: 4
YAML

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline create-tasks --project-root "$PROJECT_ROOT" --source "$INPUT_FILE" --pipeline-id verify-progress

CYCLE_ONE="$PROJECT_ROOT/cycle-one.json"
CYCLE_TWO="$PROJECT_ROOT/cycle-two.json"
STATUS_JSON="$PROJECT_ROOT/status.json"

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime once --project-root "$PROJECT_ROOT" > "$CYCLE_ONE"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime once --project-root "$PROJECT_ROOT" > "$CYCLE_TWO"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime status --project-root "$PROJECT_ROOT" > "$STATUS_JSON"

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

assert_equal(cycle_one["queue_counts"]["accepted"], 2, "cycle one accepted count")
assert_equal(cycle_one["queue_counts"]["pending"], 1, "cycle one pending count")
assert_equal(cycle_one["cycle_stats"][-1]["started"], 2, "cycle one started")
assert_equal(cycle_one["cycle_stats"][-1]["accepted"], 2, "cycle one accepted stats")

assert_equal(cycle_two["queue_counts"]["accepted"], 3, "cycle two accepted count")
assert_equal(cycle_two["queue_counts"]["pending"], 0, "cycle two pending count")
assert_equal(cycle_two["cycle_stats"][-1]["started"], 1, "cycle two started")
assert_equal(cycle_two["cycle_stats"][-1]["accepted"], 1, "cycle two accepted stats")

assert_equal(status["queue_counts"]["accepted"], 3, "final accepted count")
assert_equal(status["capacity"]["active_count"], 0, "final active count")
assert_equal(status["active_runs"], [], "final active runs")

cycles = status["cycle_stats"]
if len(cycles) != 2:
    raise SystemExit(f"expected 2 cycle stats, got {len(cycles)}")
if any(cycle["failed"] != 0 for cycle in cycles):
    raise SystemExit(f"expected no failed cycles, got {cycles!r}")

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
    if task.get("current_attempt") != 1:
        raise SystemExit(f"{task_file.name} expected current_attempt 1")

print(f"runtime progress verification passed: {project_root}")
PY
