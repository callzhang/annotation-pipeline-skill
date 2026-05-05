# Runtime Operator Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dashboard Runtime view, monitor API endpoint, and end-to-end verification script for the monitored local runtime.

**Architecture:** Reuse the Phase 1 runtime snapshot and monitor services. The backend exposes one read-only monitor endpoint, the frontend consumes runtime snapshot/cycle/monitor APIs, and the verify script checks CLI and HTTP contracts against a temporary local project.

**Tech Stack:** Python standard-library HTTP API, pytest, Vite, React, TypeScript, Vitest, shell script, Python JSON validation.

---

## File Structure

- Modify `annotation_pipeline_skill/interfaces/api.py`
  - Add `GET /api/runtime/monitor`.
- Modify `tests/test_dashboard_api.py`
  - Add monitor endpoint test.
- Modify `web/src/types.ts`
  - Add runtime snapshot, monitor, and cycle interfaces.
- Modify `web/src/api.ts`
  - Add runtime API client methods.
- Create `web/src/runtime.ts`
  - Small pure helpers for runtime summary formatting.
- Create `web/src/runtime.test.ts`
  - Unit tests for runtime helper behavior.
- Create `web/src/components/RuntimePanel.tsx`
  - Runtime operator panel UI.
- Modify `web/src/App.tsx`
  - Add Runtime tab.
- Modify `web/src/styles.css`
  - Runtime panel layout and table styling.
- Create `scripts/verify_runtime_e2e.sh`
  - Local CLI/API/runtime contract smoke test.
- Modify `README.md`
  - Document the verify script.

## Task 1: Monitor API Endpoint

**Files:**
- Modify: `annotation_pipeline_skill/interfaces/api.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write failing API test**

Append this test to `tests/test_dashboard_api.py`:

```python
def test_dashboard_api_returns_runtime_monitor_report(tmp_path):
    store = FileStore(tmp_path)
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/runtime/monitor")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert payload["ok"] is False
    assert payload["failures"] == ["runtime_unhealthy"]
    assert payload["details"]["runtime_unhealthy"]["errors"] == ["heartbeat_missing"]
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_dashboard_api.py::test_dashboard_api_returns_runtime_monitor_report -q
```

Expected: FAIL because `/api/runtime/monitor` returns 404.

- [ ] **Step 3: Implement endpoint**

Modify imports in `annotation_pipeline_skill/interfaces/api.py`:

```python
from annotation_pipeline_skill.runtime.monitor import validate_runtime_snapshot
```

Add this branch in `DashboardApi.handle_get()` after `/api/runtime`:

```python
        if route == "/api/runtime/monitor":
            return self._json_response(200, validate_runtime_snapshot(self._runtime_snapshot()))
```

- [ ] **Step 4: Run API tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_dashboard_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/interfaces/api.py tests/test_dashboard_api.py
git commit -m "feat: expose runtime monitor api"
```

## Task 2: Runtime Frontend API And Helpers

**Files:**
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`
- Create: `web/src/runtime.ts`
- Test: `web/src/runtime.test.ts`

- [ ] **Step 1: Add failing helper tests**

Create `web/src/runtime.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { monitorLabel, orderedQueueCounts, runtimeHealthLabel } from "./runtime";
import type { RuntimeMonitorReport, RuntimeSnapshot } from "./types";

const snapshot: RuntimeSnapshot = {
  generated_at: "2026-05-05T00:00:00+00:00",
  runtime_status: {
    healthy: false,
    heartbeat_at: null,
    heartbeat_age_seconds: null,
    active: false,
    errors: ["heartbeat_missing"],
  },
  queue_counts: {
    draft: 0,
    pending: 2,
    annotating: 0,
    validating: 0,
    qc: 0,
    human_review: 0,
    accepted: 1,
    rejected: 0,
    blocked: 0,
    cancelled: 0,
  },
  active_runs: [],
  capacity: {
    max_concurrent_tasks: 4,
    max_starts_per_cycle: 2,
    active_count: 0,
    available_slots: 4,
  },
  stale_tasks: [],
  due_retries: [],
  project_summaries: [],
  cycle_stats: [],
};

describe("runtime helpers", () => {
  it("formats runtime health", () => {
    expect(runtimeHealthLabel(snapshot)).toBe("Unhealthy");
  });

  it("orders queue counts for compact display", () => {
    expect(orderedQueueCounts(snapshot).map((item) => item.key)).toEqual([
      "pending",
      "annotating",
      "validating",
      "qc",
      "human_review",
      "accepted",
      "rejected",
      "blocked",
      "cancelled",
      "draft",
    ]);
  });

  it("formats monitor report state", () => {
    const report: RuntimeMonitorReport = {
      ok: false,
      failures: ["runtime_unhealthy"],
      details: { runtime_unhealthy: { errors: ["heartbeat_missing"] } },
    };

    expect(monitorLabel(report)).toBe("Action needed");
  });
});
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd web
npm test -- --run src/runtime.test.ts
```

Expected: FAIL because `web/src/runtime.ts` does not exist and runtime types are missing.

- [ ] **Step 3: Add runtime types**

Append to `web/src/types.ts`:

```ts
export interface RuntimeStatus {
  healthy: boolean;
  heartbeat_at: string | null;
  heartbeat_age_seconds: number | null;
  active: boolean;
  errors: string[];
}

export interface QueueCounts {
  draft: number;
  pending: number;
  annotating: number;
  validating: number;
  qc: number;
  human_review: number;
  accepted: number;
  rejected: number;
  blocked: number;
  cancelled: number;
}

export interface ActiveRun {
  run_id: string;
  task_id: string;
  stage: string;
  attempt_id: string;
  provider_target: string;
  started_at: string;
  heartbeat_at: string;
  metadata: Record<string, unknown>;
}

export interface CapacitySnapshot {
  max_concurrent_tasks: number;
  max_starts_per_cycle: number;
  active_count: number;
  available_slots: number;
}

export interface RuntimeCycleStats {
  cycle_id: string;
  started_at: string;
  finished_at: string;
  started: number;
  accepted: number;
  failed: number;
  capacity_available: number;
  errors: Array<Record<string, unknown>>;
}

export interface RuntimeSnapshot {
  generated_at: string;
  runtime_status: RuntimeStatus;
  queue_counts: QueueCounts;
  active_runs: ActiveRun[];
  capacity: CapacitySnapshot;
  stale_tasks: string[];
  due_retries: string[];
  project_summaries: ProjectSummary[];
  cycle_stats: RuntimeCycleStats[];
}

export interface RuntimeCyclesResponse {
  cycles: RuntimeCycleStats[];
}

export interface RuntimeMonitorReport {
  ok: boolean;
  failures: string[];
  details: Record<string, Record<string, unknown>>;
}

export interface RuntimeRunOnceResponse {
  ok: boolean;
  snapshot: RuntimeSnapshot;
}
```

- [ ] **Step 4: Add runtime API methods**

Modify `web/src/api.ts` import:

```ts
import type {
  ConfigSnapshot,
  EventLog,
  KanbanSnapshot,
  ProjectSnapshot,
  RuntimeCyclesResponse,
  RuntimeMonitorReport,
  RuntimeRunOnceResponse,
  RuntimeSnapshot,
  TaskDetail,
} from "./types";
```

Append:

```ts
export async function fetchRuntimeSnapshot(): Promise<RuntimeSnapshot> {
  const response = await fetch("/api/runtime");
  if (!response.ok) {
    throw new Error(`Runtime API returned ${response.status}`);
  }
  return response.json() as Promise<RuntimeSnapshot>;
}

export async function fetchRuntimeCycles(): Promise<RuntimeCyclesResponse> {
  const response = await fetch("/api/runtime/cycles");
  if (!response.ok) {
    throw new Error(`Runtime cycles API returned ${response.status}`);
  }
  return response.json() as Promise<RuntimeCyclesResponse>;
}

export async function fetchRuntimeMonitor(): Promise<RuntimeMonitorReport> {
  const response = await fetch("/api/runtime/monitor");
  if (!response.ok) {
    throw new Error(`Runtime monitor API returned ${response.status}`);
  }
  return response.json() as Promise<RuntimeMonitorReport>;
}

export async function runRuntimeOnce(): Promise<RuntimeRunOnceResponse> {
  const response = await fetch("/api/runtime/run-once", { method: "POST", body: "{}" });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { error?: string } | null;
    throw new Error(payload?.error ?? `Runtime run-once API returned ${response.status}`);
  }
  return response.json() as Promise<RuntimeRunOnceResponse>;
}
```

- [ ] **Step 5: Add helper implementation**

Create `web/src/runtime.ts`:

```ts
import type { RuntimeMonitorReport, RuntimeSnapshot } from "./types";

const queueOrder = [
  "pending",
  "annotating",
  "validating",
  "qc",
  "human_review",
  "accepted",
  "rejected",
  "blocked",
  "cancelled",
  "draft",
] as const;

export function runtimeHealthLabel(snapshot: RuntimeSnapshot): string {
  return snapshot.runtime_status.healthy ? "Healthy" : "Unhealthy";
}

export function monitorLabel(report: RuntimeMonitorReport | null): string {
  if (!report) return "Unknown";
  return report.ok ? "Clear" : "Action needed";
}

export function orderedQueueCounts(snapshot: RuntimeSnapshot): Array<{ key: string; value: number }> {
  return queueOrder.map((key) => ({ key, value: snapshot.queue_counts[key] }));
}

export function formatRuntimeDate(value: string | null): string {
  if (!value) return "missing";
  return new Date(value).toLocaleString();
}
```

- [ ] **Step 6: Run frontend helper tests**

Run:

```bash
cd web
npm test -- --run src/runtime.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web/src/types.ts web/src/api.ts web/src/runtime.ts web/src/runtime.test.ts
git commit -m "feat: add runtime frontend client helpers"
```

## Task 3: Runtime Panel UI

**Files:**
- Create: `web/src/components/RuntimePanel.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`
- Test: frontend test/build

- [ ] **Step 1: Implement RuntimePanel**

Create `web/src/components/RuntimePanel.tsx`:

```tsx
import { useEffect, useState } from "react";
import { fetchRuntimeCycles, fetchRuntimeMonitor, fetchRuntimeSnapshot, runRuntimeOnce } from "../api";
import { formatRuntimeDate, monitorLabel, orderedQueueCounts, runtimeHealthLabel } from "../runtime";
import type { RuntimeCycleStats, RuntimeMonitorReport, RuntimeSnapshot } from "../types";

export function RuntimePanel() {
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot | null>(null);
  const [cycles, setCycles] = useState<RuntimeCycleStats[]>([]);
  const [monitor, setMonitor] = useState<RuntimeMonitorReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadRuntime() {
    const [nextSnapshot, nextCycles, nextMonitor] = await Promise.all([
      fetchRuntimeSnapshot(),
      fetchRuntimeCycles(),
      fetchRuntimeMonitor(),
    ]);
    setSnapshot(nextSnapshot);
    setCycles(nextCycles.cycles);
    setMonitor(nextMonitor);
  }

  useEffect(() => {
    let active = true;
    setLoading(true);
    loadRuntime()
      .then(() => {
        if (active) setError(null);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Unable to load runtime data");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function runOnce() {
    setRunning(true);
    setError(null);
    try {
      const result = await runRuntimeOnce();
      setSnapshot(result.snapshot);
      const [nextCycles, nextMonitor] = await Promise.all([fetchRuntimeCycles(), fetchRuntimeMonitor()]);
      setCycles(nextCycles.cycles);
      setMonitor(nextMonitor);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Unable to run runtime cycle");
    } finally {
      setRunning(false);
    }
  }

  if (loading) return <section className="runtime-panel">Loading runtime...</section>;
  if (!snapshot) return <section className="runtime-panel notice compact">{error ?? "Runtime unavailable"}</section>;

  return (
    <section className="runtime-panel">
      {error ? <div className="notice compact">{error}</div> : null}
      <div className="runtime-header">
        <div>
          <h2>Runtime</h2>
          <p>{runtimeHealthLabel(snapshot)} · Monitor {monitorLabel(monitor)}</p>
        </div>
        <button className="primary-button" type="button" disabled={running} onClick={runOnce}>
          {running ? "Running..." : "Run once"}
        </button>
      </div>

      <div className="runtime-grid">
        <div className="runtime-card">
          <h3>Status</h3>
          <dl className="runtime-facts">
            <div><dt>Heartbeat</dt><dd>{formatRuntimeDate(snapshot.runtime_status.heartbeat_at)}</dd></div>
            <div><dt>Age</dt><dd>{snapshot.runtime_status.heartbeat_age_seconds ?? "missing"}</dd></div>
            <div><dt>Active</dt><dd>{snapshot.runtime_status.active ? "yes" : "no"}</dd></div>
            <div><dt>Generated</dt><dd>{formatRuntimeDate(snapshot.generated_at)}</dd></div>
          </dl>
        </div>

        <div className="runtime-card">
          <h3>Capacity</h3>
          <dl className="runtime-facts">
            <div><dt>Active</dt><dd>{snapshot.capacity.active_count}</dd></div>
            <div><dt>Available</dt><dd>{snapshot.capacity.available_slots}</dd></div>
            <div><dt>Max concurrent</dt><dd>{snapshot.capacity.max_concurrent_tasks}</dd></div>
            <div><dt>Max starts</dt><dd>{snapshot.capacity.max_starts_per_cycle}</dd></div>
          </dl>
        </div>

        <div className="runtime-card">
          <h3>Monitor</h3>
          {monitor?.ok ? <p className="runtime-muted">No runtime failures detected.</p> : null}
          {!monitor?.ok ? (
            <div className="runtime-list">
              {(monitor?.failures ?? []).map((failure) => (
                <div key={failure}>
                  <strong>{failure}</strong>
                  <pre>{JSON.stringify(monitor?.details[failure] ?? {}, null, 2)}</pre>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      <div className="runtime-grid secondary">
        <div className="runtime-card">
          <h3>Queue Counts</h3>
          <div className="runtime-counts">
            {orderedQueueCounts(snapshot).map((item) => (
              <div key={item.key}><span>{item.key}</span><strong>{item.value}</strong></div>
            ))}
          </div>
        </div>

        <RuntimeList title="Active Runs" values={snapshot.active_runs.map((run) => `${run.task_id} · ${run.provider_target}`)} empty="No active runs" />
        <RuntimeList title="Stale Tasks" values={snapshot.stale_tasks} empty="No stale tasks" />
        <RuntimeList title="Due Retries" values={snapshot.due_retries} empty="No due retries" />
      </div>

      <div className="runtime-card">
        <h3>Recent Cycles</h3>
        <table className="runtime-table">
          <thead>
            <tr><th>Cycle</th><th>Started</th><th>Accepted</th><th>Failed</th><th>Capacity</th></tr>
          </thead>
          <tbody>
            {cycles.slice(-8).reverse().map((cycle) => (
              <tr key={cycle.cycle_id}>
                <td>{cycle.cycle_id}</td>
                <td>{cycle.started}</td>
                <td>{cycle.accepted}</td>
                <td>{cycle.failed}</td>
                <td>{cycle.capacity_available}</td>
              </tr>
            ))}
            {cycles.length === 0 ? <tr><td colSpan={5}>No cycles recorded</td></tr> : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RuntimeList({ title, values, empty }: { title: string; values: string[]; empty: string }) {
  return (
    <div className="runtime-card">
      <h3>{title}</h3>
      {values.length === 0 ? <p className="runtime-muted">{empty}</p> : null}
      {values.length > 0 ? (
        <ul className="runtime-list compact-list">
          {values.map((value) => <li key={value}>{value}</li>)}
        </ul>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 2: Add Runtime tab**

Modify `web/src/App.tsx`:

```tsx
import { RuntimePanel } from "./components/RuntimePanel";
```

Change:

```ts
type ViewMode = "kanban" | "config" | "events";
```

to:

```ts
type ViewMode = "kanban" | "runtime" | "config" | "events";
```

Add a Runtime tab after Kanban:

```tsx
        <button className={viewMode === "runtime" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setViewMode("runtime")}>
          Runtime
        </button>
```

Add render branch:

```tsx
      {viewMode === "runtime" ? <RuntimePanel /> : null}
```

- [ ] **Step 3: Add runtime CSS**

Append to `web/src/styles.css`:

```css
.runtime-panel {
  display: grid;
  gap: 14px;
  margin: 0 auto;
  max-width: 1680px;
}

.runtime-header,
.runtime-card {
  background: #ffffff;
  border: 1px solid #d7e0e5;
  border-radius: 6px;
  padding: 14px;
}

.runtime-header {
  align-items: center;
  display: flex;
  justify-content: space-between;
}

.runtime-header h2,
.runtime-card h3 {
  margin: 0;
}

.runtime-header p,
.runtime-muted {
  color: #52616b;
  font-size: 13px;
  margin: 4px 0 0;
}

.runtime-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.runtime-grid.secondary {
  grid-template-columns: 2fr repeat(3, 1fr);
}

.runtime-facts {
  display: grid;
  gap: 8px;
  margin: 12px 0 0;
}

.runtime-facts div,
.runtime-counts div {
  align-items: center;
  display: flex;
  justify-content: space-between;
}

.runtime-facts dt,
.runtime-counts span {
  color: #52616b;
  font-size: 12px;
}

.runtime-facts dd {
  font-size: 13px;
  margin: 0;
  overflow-wrap: anywhere;
  text-align: right;
}

.runtime-counts {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 12px;
}

.runtime-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.runtime-list pre {
  background: #f4f6f8;
  border: 1px solid #d7e0e5;
  margin: 6px 0 0;
  overflow-x: auto;
  padding: 8px;
}

.compact-list {
  margin: 12px 0 0;
  padding-left: 18px;
}

.runtime-table {
  border-collapse: collapse;
  margin-top: 12px;
  width: 100%;
}

.runtime-table th,
.runtime-table td {
  border-top: 1px solid #d7e0e5;
  font-size: 12px;
  padding: 8px;
  text-align: left;
}
```

- [ ] **Step 4: Run frontend tests and build**

Run:

```bash
cd web
npm test -- --run
npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/RuntimePanel.tsx web/src/App.tsx web/src/styles.css
git commit -m "feat: add runtime operator panel"
```

## Task 4: End-To-End Runtime Verify Script

**Files:**
- Create: `scripts/verify_runtime_e2e.sh`
- Modify: `README.md`

- [ ] **Step 1: Create verify script**

Create `scripts/verify_runtime_e2e.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(mktemp -d /tmp/annotation-runtime-e2e-XXXXXX)"
INPUT_FILE="$PROJECT_ROOT/input.jsonl"
PORT="${ANNOTATION_PIPELINE_VERIFY_PORT:-18765}"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

cd "$ROOT_DIR"

printf '{"text":"alpha","source_dataset":"demo"}\n{"text":"beta","source_dataset":"demo"}\n' > "$INPUT_FILE"

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline init --project-root "$PROJECT_ROOT"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline create-tasks --project-root "$PROJECT_ROOT" --source "$INPUT_FILE" --pipeline-id verify --batch-size 2 --group-by source_dataset

STATUS_JSON="$PROJECT_ROOT/runtime-status.json"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime status --project-root "$PROJECT_ROOT" > "$STATUS_JSON"

python - "$STATUS_JSON" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ("runtime_status", "queue_counts", "capacity"):
    if key not in payload:
        raise SystemExit(f"missing {key} in runtime status")
PY

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline serve --project-root "$PROJECT_ROOT" --host 127.0.0.1 --port "$PORT" &
SERVER_PID="$!"

python - "$PORT" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

port = int(sys.argv[1])
base = f"http://127.0.0.1:{port}"

def request(path: str, method: str = "GET") -> dict:
    req = urllib.request.Request(base + path, data=b"{}" if method == "POST" else None, method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))

deadline = time.time() + 10
while True:
    try:
        request("/api/health")
        break
    except Exception:
        if time.time() > deadline:
            raise
        time.sleep(0.1)

runtime = request("/api/runtime")
for key in ("runtime_status", "queue_counts", "capacity"):
    if key not in runtime:
        raise SystemExit(f"missing {key} in /api/runtime")

monitor = request("/api/runtime/monitor")
for key in ("ok", "failures", "details"):
    if key not in monitor:
        raise SystemExit(f"missing {key} in /api/runtime/monitor")

cycles = request("/api/runtime/cycles")
if "cycles" not in cycles:
    raise SystemExit("missing cycles in /api/runtime/cycles")

run_once = request("/api/runtime/run-once", method="POST")
if run_once.get("ok") is not True or "snapshot" not in run_once:
    raise SystemExit("invalid /api/runtime/run-once response")

cycles_after = request("/api/runtime/cycles")
if len(cycles_after["cycles"]) < 1:
    raise SystemExit("run-once did not record a runtime cycle")
PY

echo "runtime e2e verification passed: $PROJECT_ROOT"
```

- [ ] **Step 2: Make script executable**

Run:

```bash
chmod +x scripts/verify_runtime_e2e.sh
```

- [ ] **Step 3: Document verify script**

Append to `README.md` Run Tests section:

```markdown
Run the runtime end-to-end verification:

```bash
bash scripts/verify_runtime_e2e.sh
```
```

- [ ] **Step 4: Run verify script**

Run:

```bash
bash scripts/verify_runtime_e2e.sh
```

Expected: exits 0 and prints `runtime e2e verification passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_runtime_e2e.sh README.md
git commit -m "test: add runtime e2e verification script"
```

## Task 5: Full Verification And Push

**Files:**
- No expected source edits unless verification exposes a bug.

- [ ] **Step 1: Run backend tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend tests**

Run:

```bash
cd web
npm test -- --run
```

Expected: PASS.

- [ ] **Step 3: Run frontend build**

Run:

```bash
cd web
npm run build
```

Expected: PASS.

- [ ] **Step 4: Run runtime e2e verify**

Run:

```bash
bash scripts/verify_runtime_e2e.sh
```

Expected: PASS.

- [ ] **Step 5: Push main**

Run:

```bash
git status --short
git push origin main
```

Expected: clean worktree and successful push.
