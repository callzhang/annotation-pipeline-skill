# annotation-pipeline-skill

Local-first foundation for a reusable annotation pipeline skill.

This repository is building toward a task-type-agnostic annotation manager with durable tasks, attempts, audit events, QC feedback, optional Human Review, repair flows, external task API integration, and a Vite + React + TypeScript Kanban dashboard.

## Current Slice

Implemented in the first backend foundation slice:

- Python package skeleton.
- Core task, attempt, artifact, feedback, external task, outbox, and audit event models.
- Validated task state transitions.
- File-system JSON/JSONL store.
- YAML-backed provider, route, annotator, and external-task config loading.
- Structured annotator capability selection.
- Append-only feedback records.
- Compact feedback bundle builder.
- Idempotent external task pull mapping.
- Local outbox records for status and submit operations.
- CLI init, doctor, JSONL task creation, local cycle, merge, and dashboard serving commands.
- Deterministic local fake runtime cycle.
- Backend Kanban snapshot data shape.

Not implemented yet:

- Streamlit dashboard. This project will not use Streamlit.
- Vite + React + TypeScript frontend.
- Real provider clients.
- Real external HTTP task API calls.
- Real multimodal preview renderers.

## Design Docs

- Product design: `PRODUCT_DESIGN.md`
- Technical architecture: `TECHNICAL_ARCHITECTURE.md`
- Test plan: `VERIFY_MANAGER_CYCLES_TEST_PLAN.md`
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

Create ready tasks from JSONL:

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

Run one deterministic local fake cycle:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline run-cycle --project-root ./demo-project
```

Run a deterministic fake cycle and immediately merge accepted tasks:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline run-cycle --project-root ./demo-project --auto-merge
```

Merge tasks that already passed QC and reached `accepted`:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline merge-accepted --project-root ./demo-project
```

Merged tasks move through the validated `accepted -> merged` transition, append
an audit event, and enqueue a pending `submit` outbox record for downstream
merge sinks or external task APIs.

Serve the dashboard API:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline serve \
  --project-root ./demo-project \
  --host 127.0.0.1 \
  --port 8765
```
