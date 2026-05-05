# annotation-pipeline-skill

Local-first agent skill for running LLM-managed annotation projects that produce training data for algorithm engineers.

The skill gives an agent a durable project store, task state machine, configurable subagent providers, QC feedback, optional Human Review, Coordinator records, external task API integration, export readiness checks, and a Vite + React + TypeScript operator dashboard.

## Agent Quickstart

Install the skill with the host agent's skill installer, or copy this repository to `$CODEX_HOME/skills/annotation-pipeline-skill`. If the runtime supports `codex skill install`, the GitHub form is:

```bash
codex skill install https://github.com/callzhang/annotation-pipeline-skill
```

Initialize a project and validate the local setup:

```bash

annotation-pipeline init --project-root ./annotation-project
annotation-pipeline doctor --project-root ./annotation-project
annotation-pipeline provider doctor --project-root ./annotation-project
```

Create project-scoped tasks from JSONL:

```bash
annotation-pipeline create-tasks \
  --project-root ./annotation-project \
  --source ./input.jsonl \
  --pipeline-id memory-ner-v2
```

Run and monitor the project:

```bash
annotation-pipeline runtime status --project-root ./annotation-project
annotation-pipeline runtime once --project-root ./annotation-project
annotation-pipeline coordinator report --project-root ./annotation-project --project-id memory-ner-v2
annotation-pipeline report readiness --project-root ./annotation-project --project-id memory-ner-v2
```

Start the dashboard API when the user wants the Kanban, provider, Coordinator, or Event Log UI:

```bash
annotation-pipeline serve --project-root ./annotation-project --host 127.0.0.1 --port 8765
```

Export accepted labels for model training:

```bash
annotation-pipeline export training-data \
  --project-root ./annotation-project \
  --project-id memory-ner-v2 \
  --export-id export-001
```

## Current Slice

Implemented in the first backend foundation slice:

- Python package skeleton.
- Core task, attempt, artifact, feedback, external task, outbox, and audit event models.
- Validated task state transitions.
- File-system JSON/JSONL store.
- YAML-backed subagent provider, workflow, annotator, and external-task config loading.
- Structured annotator capability selection.
- Append-only feedback records.
- Annotator/QC feedback discussion records with consensus-based acceptance.
- Compact feedback bundle builder.
- Idempotent external HTTP task pull with status outbox creation.
- Local outbox records for status and submit operations.
- CLI init, doctor, JSONL task creation, subagent cycle, and dashboard serving commands.
- Configurable subagent runtime through `llm_profiles.yaml`.
- OpenAI Responses API, OpenAI-compatible API, Codex CLI, and Claude CLI provider profiles.
- Backend Kanban snapshot data shape.

Not implemented yet:

- Streamlit dashboard. This project will not use Streamlit.
- Production distributed runtime.
- Production multimodal renderers beyond the current image bounding-box preview artifact display.

## Design Docs

- Product design: `PRODUCT_DESIGN.md`
- Technical architecture: `TECHNICAL_ARCHITECTURE.md`
- Test plan: `VERIFY_MANAGER_CYCLES_TEST_PLAN.md`
- Agent operator guide: `docs/agent-operator-guide.md`
- Algorithm engineer user story: `docs/algorithm-engineer-user-story.md`
- Current spec: `docs/superpowers/specs/2026-04-24-annotation-pipeline-skill-design.md`
- Active learning/RL workflow design: `docs/superpowers/specs/2026-05-05-active-learning-rl-workflow-design.md`
- Current implementation plan: `docs/superpowers/plans/2026-04-24-core-foundation.md`

## Run Tests

Use `uv` so development dependencies stay local to the project:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest -v
```

The cache variables keep `uv` writes inside sandbox-writable locations.

Run the frontend tests and production build:

```bash
cd web
npm_config_cache=/tmp/npm-cache npm install
npm test -- --run
npm run build
```

Run the runtime end-to-end verification:

```bash
bash scripts/verify_runtime_e2e.sh
```

Run the multi-cycle runtime progress verification with an in-process scripted test provider:

```bash
bash scripts/verify_runtime_progress.sh
```

Run a 10-task real Codex project verification after local Codex auth is configured:

```bash
bash scripts/verify_real_codex_project.sh
```

Run the real DeepSeek runtime smoke after local DeepSeek auth is configured:

```bash
set -a
source ~/.agents/auth/deepseek.env
set +a
bash scripts/verify_runtime_deepseek_smoke.sh
```

The DeepSeek smoke passes when it reports `status=pending` or `status=accepted`. Pending is acceptable when QC returns feedback for another annotation cycle.

Run the training data export verification:

```bash
bash scripts/verify_export_training_data.sh
```

Run the external task pull verification with a real local HTTP task server:

```bash
bash scripts/verify_external_pull.sh
```

Run the external submit outbox verification with a real local HTTP callback server:

```bash
bash scripts/verify_outbox_dispatch.sh
```

Run the skill installability verification before publishing or handing the skill to another agent:

```bash
bash scripts/verify_agent_handoff.sh
bash scripts/verify_skill_installability.sh
```

`verify_agent_handoff.sh` is the stronger check. It copies the repo into a temporary `CODEX_HOME/skills/annotation-pipeline-skill`, runs the CLI from that installed skill location, starts the API, verifies project-scoped dashboard endpoints, records Coordinator rule and long-tail records, and exports a training-data package.

## Install As A Skill

Install from a local checkout while developing. Use the host agent's skill installer when available:

```bash
codex skill install /home/derek/Projects/annotation-pipeline-skill
```

If the current Codex CLI does not expose a skill install command, clone or copy the repo to:

```bash
$CODEX_HOME/skills/annotation-pipeline-skill
```

Install from GitHub for another agent when its runtime supports skill installation by URL:

```bash
codex skill install https://github.com/callzhang/annotation-pipeline-skill
```

After installation, verify the command entrypoint and initialize a project:

```bash
annotation-pipeline --help
annotation-pipeline init --project-root ./demo-project
annotation-pipeline doctor --project-root ./demo-project
annotation-pipeline provider doctor --project-root ./demo-project
annotation-pipeline serve --project-root ./demo-project --host 127.0.0.1 --port 8765
```

## Run The Dashboard

Start the Python dashboard API against a file-store root:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  python -m annotation_pipeline_skill.interfaces.api .annotation-pipeline \
  --host 127.0.0.1 \
  --port 8765
```

Start the Vite React dashboard:

```bash
cd web
npm run dev
```

The Vite dev server proxies `/api` to `http://127.0.0.1:8765`.
Use `VITE_API_TARGET=http://127.0.0.1:<port>` when the API runs on another port.

The dashboard includes Kanban, Runtime, Readiness, Outbox, Providers, Coordinator, Configuration, and Event Log views. The Coordinator tab shows the selected project's Human Review reminders, open feedback, provider diagnostics, rule updates, long-tail issues, and coordinator record forms. The Outbox view can follow the selected project and shows pending, sent, and dead-letter callback records with retry/error details.

## CLI Workflow

Initialize a local annotation project:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline init --project-root ./demo-project
```

Validate local configuration and store directories:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline doctor --project-root ./demo-project
```

Create pending tasks from JSONL:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline create-tasks \
  --project-root ./demo-project \
  --source ./input.jsonl \
  --pipeline-id demo
```

Create 100-row grouped JSONL batch tasks:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline create-tasks \
  --project-root ./demo-project \
  --source ./input.jsonl \
  --pipeline-id memory-ner-v2 \
  --task-prefix memory-ner-v2 \
  --batch-size 100 \
  --group-by source_dataset \
  --annotation-type entity_span \
  --annotation-type structured_json
```

Each generated task stores the batch rows in `source_ref.payload.rows`, records
line boundaries and row count, and includes an all-row QC policy in task
metadata.

You can import multiple JSONL sources into the same project root by using a different `--pipeline-id` for each logical annotation project. The dashboard exposes those pipeline IDs as projects, so switching projects filters the Kanban board and event log without moving or rewriting task data.

Pull tasks from an external HTTP task API by configuring `.annotation-pipeline/external_tasks.yaml`:

```yaml
external_tasks:
  default:
    enabled: true
    system_id: vendor-system
    pull_url: http://127.0.0.1:9000/tasks/pull
    auth_secret_env: EXTERNAL_TASK_API_TOKEN
```

Then run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline external pull \
  --project-root ./demo-project \
  --project-id memory-ner-v2 \
  --source-id default \
  --limit 100
```

The pull contract is a JSON `POST` to `pull_url` with `{"limit": 100}`. The response must be `{"tasks":[{"external_task_id":"...","payload":{...}}]}`. New external tasks become `pending`, receive an audit event, and enqueue a status outbox record. Re-pulling the same external id is idempotent.

Validate subagent provider profiles:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline provider doctor --project-root ./demo-project
```

Inspect configured stage targets:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline provider targets --project-root ./demo-project
```

Run one configured subagent cycle:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline run-cycle --project-root ./demo-project
```

The explicit runtime form is also accepted:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline run-cycle --runtime subagent --project-root ./demo-project
```

Provider configuration lives at `.annotation-pipeline/llm_profiles.yaml`.

Common provider routing examples:

```yaml
targets:
  annotation: local_codex
  qc: deepseek_default
  coordinator: local_codex
```

Use `provider: openai_responses` for OpenAI Responses API, `provider: openai_compatible` with `provider_flavor: deepseek`, `glm`, or `minimax` for compatible APIs, and `provider: local_cli` with `cli_kind: codex` or `claude` for local CLI subagents. Keep secrets in environment variables referenced by `api_key_env`.

Inspect and run the monitored local runtime:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline runtime status --project-root ./demo-project

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline runtime once --project-root ./demo-project

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline runtime run --project-root ./demo-project --max-cycles 3
```

The runtime writes `.annotation-pipeline/runtime/runtime_snapshot.json`, heartbeat data, active-run records, and cycle stats. The snapshot is the local read model for runtime health, queue counts, capacity, stale tasks, and due retries.

OpenAI Responses API example:

```yaml
profiles:
  openai_default:
    provider: openai_responses
    model: gpt-5.4-mini
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
targets:
  qc: openai_default
```

Local LLM CLI example:

```yaml
profiles:
  local_codex:
    provider: local_cli
    cli_kind: codex
    cli_binary: codex
    model: gpt-5.4-mini
targets:
  annotation: local_codex
```

OpenAI-compatible providers use `provider: openai_compatible` with `provider_flavor` set to `deepseek`, `glm`, or `minimax`. The Providers tab exposes these choices without requiring code changes.

Subagent attempts record provider, model, diagnostics, artifacts, and continuity handles for later QC and feedback analysis. Local Codex runs are isolated and do not reuse prior CLI sessions; feedback and prior artifacts are passed explicitly in the next prompt.

QC is consensus-based: feedback can be discussed by the annotator and QC agent, including partial agreement. When every open feedback item has a recorded consensus, a task in QC or Human Review can move to Accepted without treating the first QC suggestion as the final authority.

Record a Human Review decision from the CLI:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline human-review decide \
  --project-root ./demo-project \
  --task-id pipe-000001 \
  --action request_changes \
  --correction-mode batch_code_update \
  --actor algorithm-engineer \
  --feedback "Apply the updated boundary rule before QC retries."
```

Human Review actions are `accept`, `reject`, and `request_changes`. Each decision writes an audit event plus a `human_review_decision` artifact. `request_changes` returns the task to `annotating` with feedback for either `manual_annotation` or `batch_code_update`.

Record coordinator findings when QC, Human Review, or model-training feedback implies a rule change or a long-tail issue:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline coordinator rule-update \
  --project-root ./demo-project \
  --project-id memory-ner-v2 \
  --source qc \
  --summary "Boundary examples are missing for product names." \
  --action "Update annotation_rules.yaml and rerun affected tasks."

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline coordinator long-tail-issue \
  --project-root ./demo-project \
  --project-id memory-ner-v2 \
  --category ambiguous_abbreviation \
  --summary "Abbreviations need user-specific disambiguation." \
  --recommended-action "Ask the algorithm engineer for a project rule."
```

Inspect the coordinator report before handoff:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline coordinator report \
  --project-root ./demo-project \
  --project-id memory-ner-v2
```

The coordinator report combines queue state, Human Review reminders, open feedback, provider diagnostics, outbox state, readiness, rule updates, and long-tail issues.

Export accepted tasks into a traceable JSONL training package:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline export training-data \
  --project-root ./demo-project \
  --project-id memory-ner-v2 \
  --export-id export-001
```

The command writes `.annotation-pipeline/exports/<export-id>/training_data.jsonl` and `manifest.json`. The manifest records included and excluded task ids, source files, annotation artifact ids, annotation rules hash, validation summary, output paths, and known limitations. Accepted tasks without a readable `annotation_result` artifact are excluded rather than exported with incomplete data.

Export schema `jsonl-training-v2` requires each row to include `task_id`, `pipeline_id`, `source_ref`, `modality`, `annotation_requirements`, `annotation`, `annotation_artifact_id`, and `annotation_artifact_path`. String annotations must be non-empty JSON strings; invalid rows are excluded with `invalid_training_row` and detailed `row_errors` in the manifest.

Inspect whether the project is ready for an algorithm engineer:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline report readiness \
  --project-root ./demo-project \
  --project-id memory-ner-v2
```

The readiness report summarizes accepted, exported, exportable, Human Review, open feedback, validation blocker, and external outbox counts, plus the recommended next action.

## Failure Recovery

- Provider failure: run `annotation-pipeline provider doctor --project-root <project>` and inspect the Providers or Coordinator tab for the missing env var, CLI binary, or invalid target.
- Stale runtime: run `annotation-pipeline runtime status --project-root <project>` and inspect stale active runs, heartbeat age, retry drain state, and queue capacity.
- QC disagreement: record annotator/QC discussion entries until feedback has consensus, then allow Accepted when the parties agree.
- Human Review needed: use the dashboard task drawer or `annotation-pipeline human-review decide` with `accept`, `reject`, or `request_changes`.
- Export blocked: run `annotation-pipeline report readiness` and fix missing or invalid `annotation_result` artifacts before exporting again.
- Long-tail issue: record it with `annotation-pipeline coordinator long-tail-issue` so it remains visible after chat context disappears.

Inspect and drain callback/submit outbox records:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline outbox status --project-root ./demo-project

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline outbox drain \
  --project-root ./demo-project \
  --max-items 10
```

`outbox drain` POSTs JSON to the enabled `callbacks.yaml` endpoint for each due pending record. Successful callbacks are marked `sent`; retryable failures keep the record `pending` with `retry_count`, `next_retry_at`, and `last_error`; permanent failures or exhausted retries move to `dead_letter`.

Serve the dashboard API:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline serve \
  --project-root ./demo-project \
  --host 127.0.0.1 \
  --port 8765
```
