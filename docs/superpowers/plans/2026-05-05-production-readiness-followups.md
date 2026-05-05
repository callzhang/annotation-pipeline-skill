# Production Readiness Followups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the remaining installability, review, external task, export validation, and operator UI gaps so the skill can be used as an agent-operated annotation project coordinator.

**Architecture:** Keep core domain task-type agnostic. Add behavior through focused services plus CLI/API/UI interfaces, with file-store persistence and audit events for every task mutation.

**Tech Stack:** Python 3.11, pytest, Vite/React/TypeScript, Vitest, local filesystem store.

---

### Task 1: Skill Packaging / Installability

**Files:**
- Modify: `SKILL.md`
- Modify: `tests/test_skill_packaging.py`
- Create: `scripts/verify_skill_installability.sh`
- Modify: `README.md`

- [ ] Write tests that assert `SKILL.md` has required frontmatter, agent use cases, CLI entrypoints, install commands, and verification guidance.
- [ ] Run `uv run pytest tests/test_skill_packaging.py -q` and confirm the new assertions fail before updating docs.
- [ ] Update `SKILL.md` and README with a concise install-to-first-run workflow.
- [ ] Add `scripts/verify_skill_installability.sh` to run CLI help, project init, doctor, and provider doctor in a temporary project.
- [ ] Run the packaging test and verify script.
- [ ] Commit and push.

### Task 2: Human Review Actions

**Files:**
- Create: `annotation_pipeline_skill/services/human_review_service.py`
- Modify: `annotation_pipeline_skill/interfaces/cli.py`
- Modify: `annotation_pipeline_skill/interfaces/api.py`
- Modify: `web/src/components/TaskDrawer.tsx`
- Add/modify tests under `tests/` and `web/src/`

- [ ] Write failing tests for accept, reject, and return-to-annotation actions from `human_review`.
- [ ] Implement a service that records a decision artifact/event and transitions tasks through existing state rules.
- [ ] Expose CLI/API actions and UI controls in the task drawer.
- [ ] Run focused Python and UI tests.
- [ ] Commit and push.

### Task 3: Real External Task Pull

**Files:**
- Modify: `annotation_pipeline_skill/services/external_task_service.py`
- Modify: `annotation_pipeline_skill/interfaces/cli.py`
- Create: `scripts/verify_external_pull.sh`
- Add tests under `tests/`

- [ ] Write failing tests for HTTP pull creating idempotent internal tasks with `ExternalTaskRef`.
- [ ] Implement the HTTP pull client using `.annotation-pipeline/external_tasks.yaml`.
- [ ] Add CLI `annotation-pipeline external pull`.
- [ ] Verify against a local HTTP server script, not an in-process mock.
- [ ] Commit and push.

### Task 4: Export Schema / Validator

**Files:**
- Modify: `annotation_pipeline_skill/services/export_service.py`
- Add/modify tests under `tests/`
- Modify docs.

- [ ] Write failing tests for required export row fields and invalid annotation payload exclusion.
- [ ] Add a minimal schema contract and manifest validation summary details.
- [ ] Update export verification script.
- [ ] Commit and push.

### Task 5: UI Outbox / Readiness Detail

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/api.ts`
- Modify: `web/src/types.ts`
- Create/modify UI components and tests.

- [ ] Write failing UI tests for rendering pending and dead-letter outbox records.
- [ ] Add an operator panel fed by `/api/outbox` and readiness dead-letter counts.
- [ ] Run `npm test -- --run` and `npm run build`.
- [ ] Commit and push.
