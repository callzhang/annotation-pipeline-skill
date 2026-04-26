# annotation-pipeline-skill

Local-first foundation for a reusable annotation pipeline skill.

This repository is building toward a task-type-agnostic annotation manager with durable tasks, attempts, audit events, QC feedback, optional Human Review, feedback-driven annotation updates, external task API integration, and a Vite + React + TypeScript Kanban dashboard.

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
- Idempotent external task pull mapping.
- Local outbox records for status and submit operations.
- CLI init, doctor, JSONL task creation, subagent cycle, and dashboard serving commands.
- Configurable subagent runtime through `llm_profiles.yaml`.
- OpenAI Responses API and local LLM CLI provider profiles.
- Backend Kanban snapshot data shape.

Not implemented yet:

- Streamlit dashboard. This project will not use Streamlit.
- Production distributed runtime.
- Real external HTTP task API calls.
- Real multimodal preview renderers.

## Design Docs

- Product design: `PRODUCT_DESIGN.md`
- Technical architecture: `TECHNICAL_ARCHITECTURE.md`
- Test plan: `VERIFY_MANAGER_CYCLES_TEST_PLAN.md`
- Agent operator guide: `docs/agent-operator-guide.md`
- Algorithm engineer user story: `docs/algorithm-engineer-user-story.md`
- Current spec: `docs/superpowers/specs/2026-04-24-annotation-pipeline-skill-design.md`
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

You can import multiple JSONL sources into the same project root by using a different `--pipeline-id` for each logical annotation project. The dashboard exposes those pipeline IDs as projects, so switching projects filters the Kanban board and event log without moving or rewriting task data.

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

Subagent attempts record provider, model, diagnostics, artifacts, and continuity handles for later QC and feedback analysis.

QC is consensus-based: feedback can be discussed by the annotator and QC agent, including partial agreement. When every open feedback item has a recorded consensus, a task in QC or Human Review can move to Accepted without treating the first QC suggestion as the final authority.

Serve the dashboard API:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline serve \
  --project-root ./demo-project \
  --host 127.0.0.1 \
  --port 8765
```
