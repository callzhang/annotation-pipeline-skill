# Runtime Operator Panel Design

## Goal

Build Phase 2 as the smallest usable operator loop on top of the monitored local runtime: a dashboard Runtime view, a monitor API endpoint, and an end-to-end verification script that proves the CLI, API, runtime snapshot, and monitor contract work together.

## Scope

In scope:

- Add a Runtime tab to the existing Vite dashboard.
- Display runtime health, monitor failures, capacity, queue counts, active runs, stale tasks, due retries, and recent cycle stats.
- Add a Run once button that calls the existing `POST /api/runtime/run-once` endpoint and refreshes runtime data.
- Add `GET /api/runtime/monitor`, backed by `validate_runtime_snapshot()`.
- Add a local shell verification script that initializes a temporary project, creates tasks, starts the dashboard API, calls runtime endpoints, and verifies JSON response structure.

Out of scope:

- Browser automation.
- Real provider quality checks.
- Long-running multi-sample progress validation.
- Runtime trend charts.
- Distributed runtime backend.

## Product Behavior

The Runtime view is an operator surface for an algorithm engineer or coordinating agent. It answers:

- Is the runtime alive?
- Is there runnable work waiting?
- Is capacity available or exceeded?
- Are tasks stale or retries due?
- Did recent scheduler cycles start, accept, or fail tasks?
- Can I run one monitored cycle from the UI?

The view should be dense and work-focused, matching the existing dashboard style. It should not be a landing page or explanatory page. It should use compact panels and tables that support repeated inspection.

## Backend Design

`DashboardApi` adds:

- `GET /api/runtime/monitor`

The endpoint builds or loads the runtime snapshot through the existing `_runtime_snapshot()` helper, passes it to `validate_runtime_snapshot()`, and returns the monitor report. The report shape stays:

```json
{
  "ok": false,
  "failures": ["runtime_unhealthy"],
  "details": {
    "runtime_unhealthy": {
      "errors": ["heartbeat_missing"],
      "heartbeat_age_seconds": null,
      "active": false
    }
  }
}
```

No endpoint mutates store state except the already-existing `POST /api/runtime/run-once`.

## Frontend Design

Add runtime API methods:

- `fetchRuntimeSnapshot()`
- `fetchRuntimeCycles()`
- `fetchRuntimeMonitor()`
- `runRuntimeOnce()`

Add runtime types for the existing backend JSON shape. Keep these as explicit TypeScript interfaces rather than loose `Record<string, unknown>` for the primary dashboard data.

Add `RuntimePanel.tsx`:

- Loads runtime snapshot, cycles, and monitor report on mount.
- Shows a compact health strip with Healthy/Unhealthy, heartbeat age, active flag, and Run once action.
- Shows monitor failures with details.
- Shows capacity and queue counts.
- Shows active runs, stale tasks, due retries, and recent cycles.
- On Run once, disables the button, calls the API, then refreshes runtime snapshot, monitor report, and cycles.

Add `Runtime` to the existing tab set in `App.tsx`.

## Verification Script

Add `scripts/verify_runtime_e2e.sh`.

The script should:

1. Create a temporary project.
2. Write a two-row JSONL input file.
3. Run `annotation-pipeline init`.
4. Run `annotation-pipeline create-tasks`.
5. Run `annotation-pipeline runtime status` and validate it contains `runtime_status`, `queue_counts`, and `capacity`.
6. Start `annotation-pipeline serve` on a local port.
7. Call `/api/runtime`, `/api/runtime/monitor`, `/api/runtime/cycles`, and `/api/runtime/run-once`.
8. Validate JSON structure with a small Python check.
9. Stop the server on exit.

The script verifies wiring and contracts. It does not assert that a real local Codex provider succeeds because that depends on local auth and model availability.

## Testing

Backend:

- Add API tests for `/api/runtime/monitor`.
- Keep existing runtime endpoint tests passing.

Frontend:

- Add pure helper tests for runtime status formatting and summary extraction.
- Run `npm test -- --run` and `npm run build`.

Integration:

- Run `UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest -q`.
- Run `bash scripts/verify_runtime_e2e.sh`.

## Success Criteria

- `main` has a Runtime tab that can inspect runtime state and trigger one cycle.
- Monitor report is available through API and displayed in the UI.
- The verify script passes on a local machine without requiring real provider execution success.
- Backend tests, frontend tests, frontend build, and verify script all pass.
