#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(mktemp -d /tmp/annotation-runtime-deepseek-XXXXXX)"
INPUT_FILE="$PROJECT_ROOT/input.jsonl"
PROVIDER_JSON="$PROJECT_ROOT/provider-smoke.json"
RUNTIME_JSON="$PROJECT_ROOT/runtime-once.json"
STATUS_JSON="$PROJECT_ROOT/runtime-status.json"

cd "$ROOT_DIR"

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "DEEPSEEK_API_KEY is not set; export it before running real DeepSeek verification" >&2
  exit 2
fi

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . python - "$PROVIDER_JSON" <<'PY'
import asyncio
import json
import sys
from pathlib import Path

from annotation_pipeline_skill.llm.client import LLMGenerateRequest
from annotation_pipeline_skill.llm.openai_compatible import OpenAICompatibleClient
from annotation_pipeline_skill.llm.profiles import LLMProfile


async def main() -> None:
    profile = LLMProfile(
        name="deepseek_default",
        provider="openai_compatible",
        provider_flavor="deepseek",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
        timeout_seconds=120,
        max_retries=1,
    )
    result = await OpenAICompatibleClient(profile).generate(
        LLMGenerateRequest(
            instructions="Return compact JSON only.",
            prompt='Return {"passed": true, "summary": "deepseek api ok"}.',
            max_output_tokens=64,
        )
    )
    payload = {
        "provider": result.provider,
        "runtime": result.runtime,
        "model": result.model,
        "diagnostics": result.diagnostics,
        "final_text": result.final_text,
        "usage": result.usage,
    }
    Path(sys.argv[1]).write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if "passed" not in result.final_text:
        raise SystemExit("DeepSeek provider smoke did not return expected JSON text")


asyncio.run(main())
PY

printf '{"text":"Project Apollo sample sentence","source_dataset":"deepseek-smoke"}\n' > "$INPUT_FILE"

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline init --project-root "$PROJECT_ROOT"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline create-tasks \
  --project-root "$PROJECT_ROOT" \
  --source "$INPUT_FILE" \
  --pipeline-id deepseek-smoke \
  --batch-size 1 \
  --annotation-type entity_span

cat > "$PROJECT_ROOT/.annotation-pipeline/llm_profiles.yaml" <<'YAML'
profiles:
  deepseek_default:
    provider: openai_compatible
    provider_flavor: deepseek
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
    timeout_seconds: 120
    max_retries: 1
targets:
  annotation: deepseek_default
  qc: deepseek_default
  coordinator: deepseek_default
limits:
  local_cli_global_concurrency: 1
YAML

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime once \
  --project-root "$PROJECT_ROOT" > "$RUNTIME_JSON"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime status \
  --project-root "$PROJECT_ROOT" > "$STATUS_JSON"

python - "$PROJECT_ROOT" "$PROVIDER_JSON" "$RUNTIME_JSON" "$STATUS_JSON" <<'PY'
import json
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
provider_json = Path(sys.argv[2])
runtime_json = Path(sys.argv[3])
status_json = Path(sys.argv[4])
store_root = project_root / ".annotation-pipeline"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def fail(message: str) -> None:
    print(f"DeepSeek runtime smoke failed: {message}", file=sys.stderr)
    print(f"project_root={project_root}", file=sys.stderr)
    print(f"provider_smoke={read(provider_json)[-4000:]}", file=sys.stderr)
    print(f"runtime={read(runtime_json)[-4000:]}", file=sys.stderr)
    print(f"status={read(status_json)[-4000:]}", file=sys.stderr)
    print(f"cycle_stats={read(store_root / 'runtime' / 'cycle_stats.jsonl')[-4000:]}", file=sys.stderr)
    for directory in ("tasks", "attempts", "artifacts", "feedback", "events"):
        for path in sorted((store_root / directory).glob("*")):
            print(f"{directory}/{path.name}={read(path)[-3000:]}", file=sys.stderr)
    raise SystemExit(1)


provider = json.loads(read(provider_json))
if provider.get("diagnostics", {}).get("provider_flavor") != "deepseek":
    fail("provider diagnostics did not identify deepseek")

status = json.loads(read(status_json))
cycles = status.get("cycle_stats", [])
if not cycles:
    fail("runtime did not record cycle stats")
if any(cycle.get("failed") for cycle in cycles):
    fail("runtime cycle recorded provider failures")

tasks = [json.loads(read(path)) for path in sorted((store_root / "tasks").glob("*.json"))]
if len(tasks) != 1:
    fail(f"expected one task, got {len(tasks)}")
if tasks[0].get("status") not in {"accepted", "pending"}:
    fail(f"unexpected task status {tasks[0].get('status')!r}")

attempt_files = sorted((store_root / "attempts").glob("*.jsonl"))
artifact_files = sorted((store_root / "artifacts").glob("*.jsonl"))
if len(attempt_files) != 1 or len(artifact_files) != 1:
    fail("expected attempts and artifacts for the task")

attempts = [json.loads(line) for line in read(attempt_files[0]).splitlines() if line.strip()]
stages = {attempt.get("stage") for attempt in attempts}
providers = {attempt.get("provider_id") for attempt in attempts}
if "annotation" not in stages or "qc" not in stages:
    fail(f"expected annotation and qc attempts, got {sorted(stages)}")
if providers != {"deepseek_default"}:
    fail(f"expected deepseek_default provider, got {sorted(providers)}")

artifacts = [json.loads(line) for line in read(artifact_files[0]).splitlines() if line.strip()]
kinds = {artifact.get("kind") for artifact in artifacts}
if "annotation_result" not in kinds or "qc_result" not in kinds:
    fail(f"expected annotation_result and qc_result artifacts, got {sorted(kinds)}")

print(f"DeepSeek runtime smoke passed: {project_root}; status={tasks[0].get('status')}")
PY
