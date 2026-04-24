# annotation-pipeline-skill

Local-first foundation for a reusable annotation pipeline skill.

This repository is building toward a task-type-agnostic annotation manager with durable tasks, attempts, audit events, QC feedback, optional Human Review, repair flows, external task API integration, and a Vite + React + TypeScript Kanban dashboard.

## Current Slice

Implemented in the first backend foundation slice:

- Python package skeleton.
- Core task, attempt, artifact, feedback, external task, outbox, and audit event models.
- Validated task state transitions.
- File-system JSON/JSONL store.
- Append-only feedback records.
- Compact feedback bundle builder.
- Idempotent external task pull mapping.
- Local outbox records for status and submit operations.
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
