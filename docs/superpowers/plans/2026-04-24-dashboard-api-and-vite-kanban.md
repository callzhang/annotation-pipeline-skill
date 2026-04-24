# Dashboard API And Vite Kanban Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local dashboard HTTP API and a Vite + React + TypeScript Kanban UI that renders annotation tasks by operational state.

**Architecture:** Keep the Python API narrow and dependency-free using `http.server`, backed by the existing `FileStore` and `build_kanban_snapshot()`. Keep the frontend as a standalone Vite app under `web/`, with typed API models, a fetch client, and presentational Kanban/detail components. Tests cover API JSON behavior and frontend data rendering helpers before implementation.

**Tech Stack:** Python 3.11+ standard library, pytest, Vite, React, TypeScript, Vitest, Testing Library.

---

## Scope

This plan implements a local operator dashboard foundation. It does not implement provider YAML editing, real authentication, real task mutation actions, real multimodal preview rendering, or production deployment.

## File Structure

- Create `annotation_pipeline_skill/interfaces/__init__.py`: interface package marker.
- Create `annotation_pipeline_skill/interfaces/api.py`: dependency-free dashboard API app and route handler.
- Create `tests/test_dashboard_api.py`: API JSON contract tests.
- Create `web/package.json`: Vite React app scripts and dependencies.
- Create `web/tsconfig.json`: TypeScript config.
- Create `web/tsconfig.node.json`: Vite config TS settings.
- Create `web/vite.config.ts`: Vite and Vitest config.
- Create `web/index.html`: app shell.
- Create `web/src/main.tsx`: React entrypoint.
- Create `web/src/App.tsx`: app composition.
- Create `web/src/api.ts`: snapshot fetch client.
- Create `web/src/types.ts`: TypeScript types matching backend snapshot.
- Create `web/src/kanban.ts`: pure grouping/summary helpers for tests.
- Create `web/src/kanban.test.ts`: Vitest tests for helper behavior.
- Create `web/src/components/KanbanBoard.tsx`: Kanban columns and task cards.
- Create `web/src/components/TaskDrawer.tsx`: selected task detail drawer.
- Create `web/src/styles.css`: restrained operational dashboard styling.
- Modify `README.md`: document API and frontend commands.
- Modify `.gitignore`: ignore `web/node_modules` and Vite build output.

## Task 1: Dashboard API Contract

**Files:**
- Create: `annotation_pipeline_skill/interfaces/__init__.py`
- Create: `annotation_pipeline_skill/interfaces/api.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write failing API tests**

```python
import json

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.interfaces.api import DashboardApi
from annotation_pipeline_skill.store.file_store import FileStore


def test_dashboard_api_returns_kanban_snapshot_json(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.READY
    store.save_task(task)
    api = DashboardApi(store)

    status, headers, body = api.handle_get("/api/kanban")

    assert status == 200
    assert headers["content-type"] == "application/json"
    payload = json.loads(body.decode("utf-8"))
    assert payload["columns"][0]["id"] == "ready"
    assert payload["columns"][0]["cards"][0]["task_id"] == "task-1"


def test_dashboard_api_returns_404_for_unknown_route(tmp_path):
    api = DashboardApi(FileStore(tmp_path))

    status, headers, body = api.handle_get("/api/missing")

    assert status == 404
    assert headers["content-type"] == "application/json"
    assert json.loads(body.decode("utf-8")) == {"error": "not_found"}
```

- [ ] **Step 2: Run test and verify it fails**

Run: `UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_dashboard_api.py -v`

Expected: FAIL because `annotation_pipeline_skill.interfaces.api` is missing.

- [ ] **Step 3: Implement minimal API**

Create `DashboardApi.handle_get(path)` returning `(status, headers, body)` for `/api/kanban`, `/api/health`, and unknown routes. Add `serve_dashboard_api(store, host, port)` as the CLI/server integration point but keep tests on `handle_get()`.

- [ ] **Step 4: Run test and verify it passes**

Run: `UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_dashboard_api.py -v`

Expected: PASS.

## Task 2: Frontend App Skeleton And Typed Helpers

**Files:**
- Create: `web/package.json`
- Create: `web/tsconfig.json`
- Create: `web/tsconfig.node.json`
- Create: `web/vite.config.ts`
- Create: `web/index.html`
- Create: `web/src/types.ts`
- Create: `web/src/kanban.ts`
- Test: `web/src/kanban.test.ts`

- [ ] **Step 1: Write failing frontend helper tests**

```ts
import { describe, expect, it } from "vitest";
import { countCards, visibleColumns } from "./kanban";
import type { KanbanSnapshot } from "./types";

const snapshot: KanbanSnapshot = {
  columns: [
    { id: "ready", title: "Ready", cards: [{ task_id: "task-1", status: "ready", modality: "text", annotation_types: ["entity_span"], selected_annotator_id: null, status_age_seconds: 3, latest_attempt_status: null, feedback_count: 0, retry_pending: false, blocked: false, external_sync_pending: false }] },
    { id: "human_review", title: "Human Review", cards: [] },
  ],
};

describe("kanban helpers", () => {
  it("counts cards across columns", () => {
    expect(countCards(snapshot)).toBe(1);
  });

  it("keeps empty operational columns visible", () => {
    expect(visibleColumns(snapshot).map((column) => column.id)).toEqual(["ready", "human_review"]);
  });
});
```

- [ ] **Step 2: Run test and verify it fails**

Run: `cd web && npm test -- --run`

Expected: FAIL because the Vite/Vitest app does not exist.

- [ ] **Step 3: Implement package skeleton and helpers**

Create Vite React config, TypeScript types matching backend card fields, and pure helpers `countCards(snapshot)` and `visibleColumns(snapshot)`.

- [ ] **Step 4: Install dependencies and run test**

Run: `cd web && npm install`

Run: `cd web && npm test -- --run`

Expected: PASS.

## Task 3: Kanban UI Components

**Files:**
- Create: `web/src/api.ts`
- Create: `web/src/main.tsx`
- Create: `web/src/App.tsx`
- Create: `web/src/components/KanbanBoard.tsx`
- Create: `web/src/components/TaskDrawer.tsx`
- Create: `web/src/styles.css`
- Test: `web/src/kanban.test.ts`

- [ ] **Step 1: Add failing UI-adjacent helper test**

```ts
import { cardSubtitle } from "./kanban";

it("builds a compact card subtitle from modality and annotation types", () => {
  expect(cardSubtitle({ modality: "image", annotation_types: ["bounding_box", "segmentation"] })).toBe("image · bounding_box, segmentation");
});
```

- [ ] **Step 2: Run test and verify it fails**

Run: `cd web && npm test -- --run`

Expected: FAIL because `cardSubtitle` is missing.

- [ ] **Step 3: Implement UI and helper**

Implement a functional Kanban UI with operational columns, task cards, selected-task drawer, loading state, and error state. Use `/api/kanban` as the API endpoint and keep all text within constrained operational panels.

- [ ] **Step 4: Run frontend tests and build**

Run: `cd web && npm test -- --run`

Run: `cd web && npm run build`

Expected: PASS.

## Task 4: Full Verification And Documentation

**Files:**
- Modify: `.gitignore`
- Modify: `README.md`

- [ ] **Step 1: Run backend tests**

Run: `UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest -v`

Expected: PASS.

- [ ] **Step 2: Run frontend tests and build**

Run: `cd web && npm test -- --run`

Run: `cd web && npm run build`

Expected: PASS.

- [ ] **Step 3: Update README**

Document:

- `python -m annotation_pipeline_skill.interfaces.api <store-root> --host 127.0.0.1 --port 8765`
- `cd web && npm run dev`
- Vite proxy from `/api` to the Python server.

- [ ] **Step 4: Commit and push**

Run:

```bash
git add .
git commit -m "feat: add dashboard api and vite kanban"
git push
```

Expected: branch `main` pushed to `origin/main`.

## Self-Review

- Spec coverage: this plan covers the local HTTP dashboard API and the Vite React TypeScript Kanban interface selected in the design spec.
- Placeholder scan: no unresolved placeholder markers remain.
- Type consistency: backend snapshot field names match frontend `KanbanSnapshot` and `TaskCard` types.
