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
