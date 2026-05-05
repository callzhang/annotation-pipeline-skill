# Agent Operator Guide

This guide is for the agent running an annotation project on behalf of an algorithm engineer.

## Setup

Initialize the project:

```bash
annotation-pipeline init --project-root ./demo-project
```

This creates `.annotation-pipeline/llm_profiles.yaml` with default stage targets:

- `annotation`
- `qc`
- `coordinator`

Validate provider configuration:

```bash
annotation-pipeline provider doctor --project-root ./demo-project
annotation-pipeline provider targets --project-root ./demo-project
```

The dashboard UI exposes provider configuration in two ways:

- Use the Providers tab for normal operation. It provides form controls for profile kind, CLI binary, API key environment variable, base URL, model, timeout, stage target bindings, and local CLI concurrency.
- Use the Configuration tab for raw YAML inspection or advanced edits. Open `Subagent Providers` to edit `.annotation-pipeline/llm_profiles.yaml` directly.

The Providers tab is the operator path for changing annotation, QC, coordinator, Human Review, or future model-assist provider targets without editing code. Click Validate to run local provider doctor checks for schema validity, missing API key env vars, and missing local CLI binaries.

## Provider Profiles

OpenAI Responses API profile:

```yaml
profiles:
  openai_default:
    provider: openai_responses
    model: gpt-5.4-mini
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    reasoning_effort: medium
```

Local Codex CLI profile:

```yaml
profiles:
  local_codex:
    provider: local_cli
    cli_kind: codex
    cli_binary: codex
    model: gpt-5.4-mini
    reasoning_effort: none
```

The local CLI adapter uses an isolated Codex home, strips desktop session context, preserves auth/config needed to run, and records continuity handles from JSON events for audit. Because each Codex invocation uses a disposable isolated home, the runtime does not call `codex exec resume` with a previous thread id. Reruns receive context through the explicit feedback bundle and prior artifacts in the prompt.

Local Claude CLI profile:

```yaml
profiles:
  local_claude:
    provider: local_cli
    cli_kind: claude
    cli_binary: claude
    model: claude-sonnet-4-5
    permission_mode: dontAsk
```

OpenAI-compatible API profiles for DeepSeek, GLM, and MiniMax:

```yaml
profiles:
  deepseek_default:
    provider: openai_compatible
    provider_flavor: deepseek
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com

  glm_default:
    provider: openai_compatible
    provider_flavor: glm
    model: glm-4.5
    api_key_env: ZHIPUAI_API_KEY
    base_url: https://open.bigmodel.cn/api/paas/v4

  minimax_default:
    provider: openai_compatible
    provider_flavor: minimax
    model: MiniMax-M1
    api_key_env: MINIMAX_API_KEY
    base_url: https://api.minimax.io/v1
```

Switch QC to DeepSeek by editing targets:

```yaml
targets:
  annotation: local_codex
  qc: deepseek_default
  coordinator: local_codex
```

Provider selection is explicit. Do not rely on model-name prefixes to infer providers.

## Run A Cycle

Use configured subagents:

```bash
annotation-pipeline run-cycle --runtime subagent --project-root ./demo-project
```

Subagent attempts record provider, model, artifact metadata, diagnostics, and continuity handles. Treat those records as the audit trail for debugging quality and provider behavior, not as a guarantee that every provider supports persistent session resume.

The local runtime now runs a real multistage loop. A pending task first creates an annotation attempt and `annotation_result` artifact, then deterministic validation gates it into QC. The QC target creates a QC attempt and `qc_result` artifact. If QC passes, the task becomes `accepted`. If QC fails, the runtime records structured QC feedback and returns the task to `pending` so the next annotation attempt receives the feedback bundle and prior artifacts as context.

QC failure is business feedback, not a scheduler failure. Provider exceptions still count as runtime failures in cycle stats.

## Verification

Use stable local verification before changing a provider configuration:

```bash
bash scripts/verify_runtime_progress.sh
bash scripts/verify_runtime_e2e.sh
```

Use the real Codex smoke after configuring local Codex auth:

```bash
bash scripts/verify_runtime_codex_smoke.sh
```

This script runs one real `local_codex` task. If Codex is missing, unauthenticated, or the runtime cycle fails, it prints the project path, runtime stderr/stdout, cycle stats, task JSON, events, attempts, and artifacts for diagnosis.

## Runtime Operations

Use `annotation-pipeline runtime status --project-root <project>` before starting work. A healthy project has a fresh heartbeat, no stale active runs, and capacity that is not exceeded.

Use `annotation-pipeline runtime once --project-root <project>` for one monitored cycle. Use `annotation-pipeline runtime run --project-root <project>` when the agent should keep the local project moving.

If runtime status shows stale tasks or due retries that are not draining, inspect task detail and event logs before changing annotation rules or provider config.

## Operating Loop

1. Pull or create tasks.
2. Select annotators by task modality and annotation type.
3. Run annotation and QC stages.
4. Let annotator and QC exchange feedback, including partial agreement.
5. Record consensus when both sides agree on the final resolution.
6. Notify the user when unresolved items need Human Review.
7. Re-run annotation with feedback when consensus requires label updates, then submit accepted training data.

Human Review decisions are executable state changes, not notes. Use the dashboard task drawer or the CLI:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline human-review decide \
  --project-root ./demo-project \
  --task-id <task-id> \
  --action request_changes \
  --correction-mode batch_code_update \
  --actor algorithm-engineer \
  --feedback "Apply the updated rule before QC retries."
```

`accept` moves the task to `accepted`, `reject` moves it to `rejected`, and `request_changes` returns it to `annotating`. Every decision writes an audit event and `human_review_decision` artifact so the algorithm engineer can inspect why labels were accepted, rejected, or sent back.

For multimodal projects, keep the core task model generic and add adapters/renderers for images, video, point clouds, or model-specific previews such as bounding boxes from a VC detection model.

## External Task Pull

Configure `.annotation-pipeline/external_tasks.yaml` before pulling from an external task API:

```yaml
external_tasks:
  default:
    enabled: true
    system_id: vendor-system
    pull_url: http://127.0.0.1:9000/tasks/pull
    auth_secret_env: EXTERNAL_TASK_API_TOKEN
```

Pull into a project:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline external pull \
  --project-root ./demo-project \
  --project-id memory-ner-v2 \
  --source-id default \
  --limit 100
```

The external service must return `{"tasks":[{"external_task_id":"...","payload":{...}}]}`. New tasks become `pending`, get a prepare-stage audit event, and create status outbox records for callback delivery. Re-pulling the same external id returns the existing internal task and does not create duplicate outbox records.

## Training Data Export

After tasks reach `accepted`, export the project into a JSONL package:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline export training-data \
  --project-root ./demo-project \
  --project-id memory-ner-v2 \
  --export-id export-001
```

The export service writes `training_data.jsonl` plus `manifest.json` under `.annotation-pipeline/exports/<export-id>/`. The manifest is the audit record for algorithm engineers: it includes included and excluded task ids, source files, annotation artifact ids, annotation rules hash, schema/validator versions, validation summary, output paths, and known limitations.

Schema `jsonl-training-v2` validates that every exported row has the required training fields and that string annotations are non-empty JSON strings. Invalid rows are excluded and reported as `invalid_training_row` with `row_errors`, so the algorithm engineer does not receive silently malformed training data.

Accepted tasks are exported only when they still have a readable `annotation_result` artifact. Missing annotation artifacts are recorded as excluded validation failures, and the task remains `accepted` so the operator can repair/export again without losing QC history.

Check the coordinator readiness report before handing data to an algorithm engineer:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline report readiness \
  --project-root ./demo-project \
  --project-id memory-ner-v2
```

The report returns `ready_for_training`, accepted/exported/exportable counts, open feedback count, Human Review count, validation blockers, pending external outbox count, latest export metadata, and a deterministic recommended next action. The dashboard exposes the same information in the Readiness tab for the selected project.

## External Outbox

When export uses `--enqueue-external-submit`, accepted external tasks create `submit` outbox records. Inspect them before handoff:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline outbox status --project-root ./demo-project
```

Drain due pending records through the configured callback endpoints:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline outbox drain \
  --project-root ./demo-project \
  --max-items 10 \
  --max-attempts 3
```

The dispatcher sends JSON POST requests to `callbacks.yaml`. A successful response marks the record `sent` and writes an audit event. Retryable failures keep the record `pending` with `retry_count`, `next_retry_at`, and `last_error`. Permanent failures or exhausted retries move to `dead_letter`; readiness reports those as blockers until an operator resolves them.

The dashboard Outbox tab shows the same records and follows the selected project filter. Use it with the Readiness tab: pending outbox records mean the export is not delivered yet, and dead-letter records require operator inspection before the training data handoff.
