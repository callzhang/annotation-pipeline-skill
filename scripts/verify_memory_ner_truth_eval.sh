#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MEMORY_NER_ROOT="${MEMORY_NER_ROOT:-/home/derek/Projects/memory-ner}"
TASK_ROOT_PRIMARY="$MEMORY_NER_ROOT/data/derived/annotation_projects/v2/tasks"
TASK_ROOT_FALLBACK="$MEMORY_NER_ROOT/data/derived/annotation_tasks"
PROJECT_ROOT="$(mktemp -d /tmp/annotation-memory-ner-eval-XXXXXX)"
INPUT_FILE="$PROJECT_ROOT/input.jsonl"
TRUTH_FILE="$PROJECT_ROOT/truth.jsonl"
REPORT_JSON="$PROJECT_ROOT/eval-report.json"
LIMIT="${MEMORY_NER_EVAL_LIMIT:-10}"
MIN_F1="${MEMORY_NER_EVAL_MIN_F1:-0.20}"

cleanup() {
  if [[ "${KEEP_MEMORY_NER_EVAL_PROJECT:-0}" != "1" ]]; then
    rm -rf "$PROJECT_ROOT"
  fi
}
trap cleanup EXIT

cd "$ROOT_DIR"

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "DEEPSEEK_API_KEY is not set; source ~/.agents/auth/deepseek.env or export it before running this eval" >&2
  exit 2
fi

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


def entity_inventory(gold_entities: dict) -> list[str]:
    return sorted(str(label) for label, values in gold_entities.items() if values)


def read_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


selected: list[tuple[dict, dict, dict]] = []
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
        input_row = {
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
        }
        truth_row = {
            "eval_id": eval_id,
            "source_task_id": task.get("task_id"),
            "source_task_status": task.get("status"),
            "source_row_index": row_index,
            "gold_output": output,
        }
        selected.append((input_row, truth_row, task))
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

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . python - \
  "$PROJECT_ROOT" "$INPUT_FILE" "$TRUTH_FILE" "$REPORT_JSON" "$MIN_F1" <<'PY'
import asyncio
import inspect
import json
import sys
from pathlib import Path
from typing import Any

from annotation_pipeline_skill.llm.client import LLMGenerateRequest
from annotation_pipeline_skill.llm.openai_compatible import OpenAICompatibleClient
from annotation_pipeline_skill.llm.profiles import LLMProfile

project_root = Path(sys.argv[1])
input_file = Path(sys.argv[2])
truth_file = Path(sys.argv[3])
report_json = Path(sys.argv[4])
min_f1 = float(sys.argv[5])


def fail(message: str) -> None:
    print(f"memory-ner truth eval failed: {message}", file=sys.stderr)
    print(f"project_root={project_root}", file=sys.stderr)
    if report_json.exists():
        print(f"report={report_json.read_text(encoding='utf-8')[-4000:]}", file=sys.stderr)
    raise SystemExit(1)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalize_text(value: object) -> str:
    return " ".join(str(value).strip().lower().split())


def entity_pairs(entities: Any) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    if not isinstance(entities, dict):
        return pairs
    for label, values in entities.items():
        if not isinstance(values, list):
            values = [values]
        for item in values:
            text = item.get("text") if isinstance(item, dict) else item
            normalized = normalize_text(text)
            if normalized:
                pairs.add((normalize_text(label), normalized))
    return pairs


def parse_json_text(text: str) -> Any:
    return json.loads(text)


def predicted_entities(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("entities"), dict):
        return payload["entities"]
    output = payload.get("output")
    if isinstance(output, dict) and isinstance(output.get("entities"), dict):
        return output["entities"]
    return {}


def request_for_row(row: dict) -> LLMGenerateRequest:
    prompt_payload = {
        "pipeline_id": "memory-ner-truth-eval",
        "task": row,
    }
    return LLMGenerateRequest(
        instructions=(
            "You are an annotation subagent for a text NER evaluation. "
            "Return JSON only with this schema: "
            '{"entities":{"<entity_type>":["exact text span"]},"classifications":[],"json_structures":[],"relations":[]}. '
            "Extract exact entity spans from the input text. "
            "Use only the allowed entity types in annotation_guidance.allowed_entity_types. "
            "Do not include entities that are not present exactly in the input text."
        ),
        prompt=json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True),
        max_output_tokens=800,
    )


async def close_client(client: OpenAICompatibleClient) -> None:
    close = getattr(client.client, "close", None)
    if close is None:
        close = getattr(client.client, "aclose", None)
    if close is None:
        return
    value = close()
    if inspect.isawaitable(value):
        await value


async def evaluate() -> dict:
    input_rows = read_jsonl(input_file)
    truth_rows = read_jsonl(truth_file)
    truth_by_id = {row["eval_id"]: row for row in truth_rows}
    client = OpenAICompatibleClient(
        LLMProfile(
            name="deepseek_default",
            provider="openai_compatible",
            provider_flavor="deepseek",
            model="deepseek-chat",
            api_key_env="DEEPSEEK_API_KEY",
            base_url="https://api.deepseek.com",
            timeout_seconds=180,
            max_retries=1,
        )
    )
    results = []
    total_tp = total_fp = total_fn = 0
    parsed_count = 0
    try:
        for row in input_rows:
            truth = truth_by_id[row["eval_id"]]
            gold_pairs = entity_pairs(truth["gold_output"].get("entities"))
            response = await client.generate(request_for_row(row))
            raw_text = response.final_text.strip()
            try:
                parsed = parse_json_text(raw_text)
                parse_error = None
            except json.JSONDecodeError as exc:
                parsed = None
                parse_error = str(exc)
            predicted_pairs = entity_pairs(predicted_entities(parsed))
            parsed_ok = parsed is not None
            parsed_count += 1 if parsed_ok else 0
            tp = len(gold_pairs & predicted_pairs)
            fp = len(predicted_pairs - gold_pairs)
            fn = len(gold_pairs - predicted_pairs)
            total_tp += tp
            total_fp += fp
            total_fn += fn
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
            results.append(
                {
                    "eval_id": row["eval_id"],
                    "source_task_id": truth["source_task_id"],
                    "source_row_index": truth["source_row_index"],
                    "parsed": parsed_ok,
                    "parse_error": parse_error,
                    "gold_count": len(gold_pairs),
                    "predicted_count": len(predicted_pairs),
                    "true_positive": tp,
                    "false_positive": fp,
                    "false_negative": fn,
                    "precision": round(precision, 4),
                    "recall": round(recall, 4),
                    "f1": round(f1, 4),
                    "missing_gold": sorted(gold_pairs - predicted_pairs)[:20],
                    "extra_predicted": sorted(predicted_pairs - gold_pairs)[:20],
                    "raw_text": raw_text[:2000],
                }
            )
    finally:
        await close_client(client)

    precision = total_tp / (total_tp + total_fp) if total_tp + total_fp else 0.0
    recall = total_tp / (total_tp + total_fn) if total_tp + total_fn else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return {
        "project_root": str(project_root),
        "input_file": str(input_file),
        "truth_file": str(truth_file),
        "tasks": len(results),
        "parsed_count": parsed_count,
        "provider": "openai_compatible",
        "provider_flavor": "deepseek",
        "model": "deepseek-chat",
        "true_positive": total_tp,
        "false_positive": total_fp,
        "false_negative": total_fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "minimum_f1": min_f1,
        "results": results,
    }


report = asyncio.run(evaluate())
report_json.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

if report["parsed_count"] < report["tasks"]:
    fail(f"only parsed {report['parsed_count']}/{report['tasks']} annotation outputs")
if report["f1"] < min_f1:
    fail(f"F1 {report['f1']:.4f} below required minimum {min_f1:.4f}")

print(
    "memory-ner truth eval passed: "
    f"{report_json}; precision={report['precision']:.4f}; recall={report['recall']:.4f}; f1={report['f1']:.4f}"
)
PY
