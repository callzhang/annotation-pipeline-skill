# Coordinator Dashboard And DeepSeek Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class Coordinator dashboard view and make real DeepSeek API verification part of the operator handoff workflow.

**Architecture:** Keep `CoordinatorService` as the backend source of truth and add a typed React panel that consumes the existing `/api/coordinator` endpoints. The panel should be project-scoped, scan-friendly, and focused on operator decisions: Human Review reminders, open feedback, recommended actions, rule updates, long-tail issues, and provider health. DeepSeek verification remains a script, with docs explaining how to source the local secret env file before running it.

**Tech Stack:** Python 3.11 standard-library API, pytest, Vite React TypeScript, Vitest, plain CSS, shell verification scripts.

---

## File Structure

- Modify `web/src/types.ts`: add `CoordinatorReport`, `CoordinatorRuleUpdate`, `CoordinatorLongTailIssue`, and request payload interfaces.
- Modify `web/src/api.ts`: add `fetchCoordinatorReport()`, `postCoordinatorRuleUpdate()`, and `postCoordinatorLongTailIssue()`.
- Create `web/src/coordinator.ts`: pure helper functions for labels, counts, and provider diagnostic summaries.
- Create `web/src/coordinator.test.ts`: Vitest coverage for helper behavior.
- Create `web/src/components/CoordinatorPanel.tsx`: project-scoped coordinator UI with report cards and two record forms.
- Modify `web/src/App.tsx`: add a `Coordinator` tab and render the new panel.
- Modify `web/src/styles.css`: add compact coordinator panel styles consistent with existing runtime/readiness panels.
- Modify `web/src/api.test.ts`: add frontend API contract tests for coordinator endpoints.
- Modify `docs/agent-operator-guide.md` and `README.md`: document the Coordinator tab and DeepSeek smoke command using `~/.agents/auth/deepseek.env`.

## Task 1: Typed Coordinator API Client

**Files:**
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`
- Modify: `web/src/api.test.ts`

- [ ] **Step 1: Add failing API tests**

Append this test block to `web/src/api.test.ts`:

```ts
import { fetchCoordinatorReport, postCoordinatorLongTailIssue, postCoordinatorRuleUpdate } from "./api";

it("fetches project-scoped coordinator reports", async () => {
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ project_id: "pipe", task_count: 2, recommended_actions: ["resolve_annotator_qc_feedback"] }),
  });

  const report = await fetchCoordinatorReport("pipe");

  expect(fetchMock).toHaveBeenCalledWith("/api/coordinator?project=pipe");
  expect(report.project_id).toBe("pipe");
  expect(report.recommended_actions).toEqual(["resolve_annotator_qc_feedback"]);
});

it("posts coordinator records", async () => {
  fetchMock
    .mockResolvedValueOnce({ ok: true, json: async () => ({ record_id: "rule-1", project_id: "pipe" }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ issue_id: "issue-1", project_id: "pipe" }) });

  await postCoordinatorRuleUpdate({
    project_id: "pipe",
    source: "qc",
    summary: "Boundary examples are missing.",
    action: "Update annotation_rules.yaml.",
    created_by: "coordinator-agent",
    task_ids: ["task-1"],
  });
  await postCoordinatorLongTailIssue({
    project_id: "pipe",
    category: "ambiguous_abbreviation",
    summary: "Abbreviations need guidance.",
    recommended_action: "Ask the algorithm engineer for a rule.",
    severity: "medium",
    created_by: "coordinator-agent",
    task_ids: ["task-1"],
  });

  expect(fetchMock).toHaveBeenNthCalledWith(
    1,
    "/api/coordinator/rule-updates",
    expect.objectContaining({ method: "POST" }),
  );
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "/api/coordinator/long-tail-issues",
    expect.objectContaining({ method: "POST" }),
  );
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd web
npm test -- --run src/api.test.ts
```

Expected: FAIL because the coordinator API functions and types are missing.

- [ ] **Step 3: Add coordinator types**

Append these interfaces to `web/src/types.ts`:

```ts
export interface CoordinatorProviderDiagnostics {
  config_valid: boolean;
  error?: string;
  targets?: Record<string, string>;
  diagnostics: Record<string, ProviderDiagnostic>;
}

export interface CoordinatorRuleUpdate {
  record_id: string;
  project_id: string;
  source: string;
  summary: string;
  action: string;
  status: string;
  task_ids: string[];
  created_at: string;
  created_by: string;
  metadata: Record<string, unknown>;
}

export interface CoordinatorLongTailIssue {
  issue_id: string;
  project_id: string;
  category: string;
  summary: string;
  recommended_action: string;
  severity: string;
  status: string;
  task_ids: string[];
  created_at: string;
  created_by: string;
  metadata: Record<string, unknown>;
}

export interface CoordinatorReport {
  project_id: string | null;
  generated_at: string;
  task_count: number;
  status_counts: Record<string, number>;
  human_review_task_ids: string[];
  blocked_task_ids: string[];
  open_feedback_count: number;
  open_feedback_ids: string[];
  feedback_by_category: Record<string, number>;
  blocking_feedback_count: number;
  outbox_counts: {
    pending: number;
    sent: number;
    dead_letter: number;
  };
  readiness: ReadinessReport | null;
  provider_diagnostics: CoordinatorProviderDiagnostics;
  rule_updates: CoordinatorRuleUpdate[];
  long_tail_issues: CoordinatorLongTailIssue[];
  recommended_actions: string[];
}

export interface CoordinatorRuleUpdatePayload {
  project_id: string;
  source: string;
  summary: string;
  action: string;
  created_by: string;
  task_ids: string[];
}

export interface CoordinatorLongTailIssuePayload {
  project_id: string;
  category: string;
  summary: string;
  recommended_action: string;
  severity: string;
  created_by: string;
  task_ids: string[];
}
```

- [ ] **Step 4: Add API functions**

Update the import list in `web/src/api.ts` to include the new types:

```ts
  CoordinatorLongTailIssue,
  CoordinatorLongTailIssuePayload,
  CoordinatorReport,
  CoordinatorRuleUpdate,
  CoordinatorRuleUpdatePayload,
```

Append these functions to `web/src/api.ts`:

```ts
export async function fetchCoordinatorReport(projectId: string | null = null): Promise<CoordinatorReport> {
  const response = await fetch(`/api/coordinator${projectQuery(projectId)}`);
  if (!response.ok) {
    throw new Error(`Coordinator API returned ${response.status}`);
  }
  return response.json() as Promise<CoordinatorReport>;
}

export async function postCoordinatorRuleUpdate(payload: CoordinatorRuleUpdatePayload): Promise<CoordinatorRuleUpdate> {
  const response = await fetch("/api/coordinator/rule-updates", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorPayload = (await response.json().catch(() => null)) as { detail?: string; error?: string } | null;
    throw new Error(errorPayload?.detail ?? errorPayload?.error ?? `Coordinator rule update returned ${response.status}`);
  }
  return response.json() as Promise<CoordinatorRuleUpdate>;
}

export async function postCoordinatorLongTailIssue(payload: CoordinatorLongTailIssuePayload): Promise<CoordinatorLongTailIssue> {
  const response = await fetch("/api/coordinator/long-tail-issues", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorPayload = (await response.json().catch(() => null)) as { detail?: string; error?: string } | null;
    throw new Error(errorPayload?.detail ?? errorPayload?.error ?? `Coordinator long-tail issue returned ${response.status}`);
  }
  return response.json() as Promise<CoordinatorLongTailIssue>;
}
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```bash
cd web
npm test -- --run src/api.test.ts
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/src/types.ts web/src/api.ts web/src/api.test.ts
git commit -m "feat: add coordinator frontend api client"
```

## Task 2: Coordinator View Helpers

**Files:**
- Create: `web/src/coordinator.ts`
- Create: `web/src/coordinator.test.ts`

- [ ] **Step 1: Write failing helper tests**

Create `web/src/coordinator.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { coordinatorActionLabel, providerHealthSummary, statusCountItems } from "./coordinator";
import type { CoordinatorReport } from "./types";

const report: CoordinatorReport = {
  project_id: "pipe",
  generated_at: "2026-05-05T00:00:00+00:00",
  task_count: 3,
  status_counts: { pending: 1, human_review: 1, accepted: 1 },
  human_review_task_ids: ["task-2"],
  blocked_task_ids: [],
  open_feedback_count: 2,
  open_feedback_ids: ["feedback-1", "feedback-2"],
  feedback_by_category: { missing_entity: 2 },
  blocking_feedback_count: 1,
  outbox_counts: { pending: 0, sent: 1, dead_letter: 0 },
  readiness: null,
  provider_diagnostics: {
    config_valid: true,
    targets: { annotation: "deepseek_default" },
    diagnostics: {
      deepseek_default: { status: "ok", checks: [{ id: "api_key_env_present", status: "ok", message: "available" }] },
    },
  },
  rule_updates: [],
  long_tail_issues: [],
  recommended_actions: ["remind_user_to_complete_human_review", "resolve_annotator_qc_feedback"],
};

describe("coordinator helpers", () => {
  it("labels recommended actions", () => {
    expect(coordinatorActionLabel("remind_user_to_complete_human_review")).toBe("Complete Human Review");
    expect(coordinatorActionLabel("unknown_action")).toBe("unknown_action");
  });

  it("orders visible status counts", () => {
    expect(statusCountItems(report).map((item) => item.key)).toEqual(["pending", "human_review", "accepted"]);
  });

  it("summarizes provider health", () => {
    expect(providerHealthSummary(report)).toBe("1 ok, 0 attention");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd web
npm test -- --run src/coordinator.test.ts
```

Expected: FAIL because `web/src/coordinator.ts` does not exist.

- [ ] **Step 3: Implement helpers**

Create `web/src/coordinator.ts`:

```ts
import type { CoordinatorReport } from "./types";

const actionLabels: Record<string, string> = {
  remind_user_to_complete_human_review: "Complete Human Review",
  resolve_annotator_qc_feedback: "Resolve Feedback",
  inspect_blocked_tasks: "Inspect Blocked Tasks",
  drain_external_outbox: "Drain External Outbox",
  fix_export_blockers: "Fix Export Blockers",
  export_training_data: "Export Training Data",
  export_or_deliver_training_data: "Deliver Training Data",
  run_annotation_runtime: "Run Runtime",
  inspect_project_state: "Inspect Project",
};

const statusOrder = ["pending", "annotating", "validating", "qc", "human_review", "accepted", "rejected", "blocked", "cancelled", "draft"];

export function coordinatorActionLabel(action: string): string {
  return actionLabels[action] ?? action;
}

export function statusCountItems(report: CoordinatorReport): Array<{ key: string; value: number }> {
  return statusOrder
    .filter((key) => (report.status_counts[key] ?? 0) > 0)
    .map((key) => ({ key, value: report.status_counts[key] ?? 0 }));
}

export function providerHealthSummary(report: CoordinatorReport): string {
  const diagnostics = Object.values(report.provider_diagnostics.diagnostics ?? {});
  const ok = diagnostics.filter((item) => item.status === "ok").length;
  const attention = diagnostics.length - ok;
  return `${ok} ok, ${attention} attention`;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
cd web
npm test -- --run src/coordinator.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/coordinator.ts web/src/coordinator.test.ts
git commit -m "feat: add coordinator view helpers"
```

## Task 3: Coordinator Panel UI

**Files:**
- Create: `web/src/components/CoordinatorPanel.tsx`
- Modify: `web/src/styles.css`

- [ ] **Step 1: Create the panel component**

Create `web/src/components/CoordinatorPanel.tsx`:

```tsx
import { useEffect, useState } from "react";
import { fetchCoordinatorReport, postCoordinatorLongTailIssue, postCoordinatorRuleUpdate } from "../api";
import { coordinatorActionLabel, providerHealthSummary, statusCountItems } from "../coordinator";
import type { CoordinatorReport } from "../types";

export function CoordinatorPanel({ projectId }: { projectId: string | null }) {
  const [report, setReport] = useState<CoordinatorReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    try {
      setReport(await fetchCoordinatorReport(projectId));
      setMessage(null);
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "Unable to load coordinator report");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, [projectId]);

  async function submitRuleUpdate(payload: { source: string; summary: string; action: string; task_ids: string[] }) {
    if (!projectId) {
      setMessage("Select a project before recording coordinator updates.");
      return;
    }
    setSaving(true);
    try {
      await postCoordinatorRuleUpdate({ project_id: projectId, created_by: "coordinator-agent", ...payload });
      await refresh();
      setMessage("Rule update recorded");
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "Unable to record rule update");
    } finally {
      setSaving(false);
    }
  }

  async function submitLongTailIssue(payload: { category: string; summary: string; recommended_action: string; severity: string; task_ids: string[] }) {
    if (!projectId) {
      setMessage("Select a project before recording coordinator updates.");
      return;
    }
    setSaving(true);
    try {
      await postCoordinatorLongTailIssue({ project_id: projectId, created_by: "coordinator-agent", ...payload });
      await refresh();
      setMessage("Long-tail issue recorded");
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "Unable to record long-tail issue");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <section className="work-panel">Loading coordinator report</section>;
  if (!report) return <section className="work-panel">{message ?? "No coordinator report loaded"}</section>;

  return (
    <section className="coordinator-panel" aria-label="Coordinator">
      <div className="panel-header">
        <div>
          <h2>Coordinator</h2>
          <p>{projectId ? projectId : "All projects"} · {report.task_count} tasks · {providerHealthSummary(report)}</p>
        </div>
        <button className="view-tab" type="button" onClick={refresh}>Refresh</button>
      </div>

      {message ? <div className="notice compact">{message}</div> : null}

      <div className="coordinator-grid">
        <SummaryCard title="Recommended Actions">
          <div className="pill-list">
            {report.recommended_actions.map((action) => <span key={action}>{coordinatorActionLabel(action)}</span>)}
          </div>
        </SummaryCard>
        <SummaryCard title="Queue">
          <dl className="mini-facts">
            {statusCountItems(report).map((item) => (
              <div key={item.key}><dt>{item.key}</dt><dd>{item.value}</dd></div>
            ))}
          </dl>
        </SummaryCard>
        <SummaryCard title="Feedback">
          <dl className="mini-facts">
            <div><dt>Open</dt><dd>{report.open_feedback_count}</dd></div>
            <div><dt>Blocking</dt><dd>{report.blocking_feedback_count}</dd></div>
            <div><dt>Human Review</dt><dd>{report.human_review_task_ids.length}</dd></div>
          </dl>
        </SummaryCard>
      </div>

      <div className="coordinator-layout">
        <RecordList title="Rule Updates" records={report.rule_updates} empty="No rule updates recorded." />
        <RecordList title="Long-Tail Issues" records={report.long_tail_issues} empty="No long-tail issues recorded." />
      </div>

      <div className="coordinator-layout">
        <RuleUpdateForm saving={saving} onSubmit={submitRuleUpdate} />
        <LongTailIssueForm saving={saving} onSubmit={submitLongTailIssue} />
      </div>
    </section>
  );
}

function SummaryCard({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="runtime-card"><h3>{title}</h3>{children}</section>;
}

function RecordList({ title, records, empty }: { title: string; records: Array<Record<string, unknown>>; empty: string }) {
  return (
    <section className="runtime-card">
      <h3>{title}</h3>
      <div className="record-list">
        {records.length === 0 ? <p className="runtime-muted">{empty}</p> : records.map((record) => (
          <details key={String(record.record_id ?? record.issue_id)}>
            <summary>{String(record.summary)}<small>{String(record.status)} · {String(record.created_by)}</small></summary>
            <pre>{JSON.stringify(record, null, 2)}</pre>
          </details>
        ))}
      </div>
    </section>
  );
}

function RuleUpdateForm({ saving, onSubmit }: { saving: boolean; onSubmit: (payload: { source: string; summary: string; action: string; task_ids: string[] }) => Promise<void> }) {
  const [source, setSource] = useState("qc");
  const [summary, setSummary] = useState("");
  const [action, setAction] = useState("");
  const [taskIds, setTaskIds] = useState("");
  return (
    <section className="runtime-card coordinator-form">
      <h3>Record Rule Update</h3>
      <input value={source} onChange={(event) => setSource(event.target.value)} placeholder="source" />
      <textarea value={summary} onChange={(event) => setSummary(event.target.value)} placeholder="summary" />
      <textarea value={action} onChange={(event) => setAction(event.target.value)} placeholder="action" />
      <input value={taskIds} onChange={(event) => setTaskIds(event.target.value)} placeholder="task ids, comma separated" />
      <button className="primary-button" type="button" disabled={saving || !summary.trim() || !action.trim()} onClick={() => onSubmit({ source, summary, action, task_ids: splitTaskIds(taskIds) })}>Save Rule Update</button>
    </section>
  );
}

function LongTailIssueForm({ saving, onSubmit }: { saving: boolean; onSubmit: (payload: { category: string; summary: string; recommended_action: string; severity: string; task_ids: string[] }) => Promise<void> }) {
  const [category, setCategory] = useState("");
  const [summary, setSummary] = useState("");
  const [recommendedAction, setRecommendedAction] = useState("");
  const [severity, setSeverity] = useState("medium");
  const [taskIds, setTaskIds] = useState("");
  return (
    <section className="runtime-card coordinator-form">
      <h3>Record Long-Tail Issue</h3>
      <input value={category} onChange={(event) => setCategory(event.target.value)} placeholder="category" />
      <textarea value={summary} onChange={(event) => setSummary(event.target.value)} placeholder="summary" />
      <textarea value={recommendedAction} onChange={(event) => setRecommendedAction(event.target.value)} placeholder="recommended action" />
      <select value={severity} onChange={(event) => setSeverity(event.target.value)}>
        <option value="low">low</option>
        <option value="medium">medium</option>
        <option value="high">high</option>
      </select>
      <input value={taskIds} onChange={(event) => setTaskIds(event.target.value)} placeholder="task ids, comma separated" />
      <button className="primary-button" type="button" disabled={saving || !category.trim() || !summary.trim() || !recommendedAction.trim()} onClick={() => onSubmit({ category, summary, recommended_action: recommendedAction, severity, task_ids: splitTaskIds(taskIds) })}>Save Issue</button>
    </section>
  );
}

function splitTaskIds(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}
```

- [ ] **Step 2: Add styles**

Append to `web/src/styles.css`:

```css
.coordinator-panel {
  display: grid;
  gap: 14px;
  margin: 0 auto;
  max-width: 1680px;
}

.coordinator-grid,
.coordinator-layout {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.coordinator-layout {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.pill-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}

.pill-list span {
  background: #eef2f4;
  border: 1px solid #d7e0e5;
  border-radius: 999px;
  color: #172026;
  font-size: 12px;
  padding: 4px 8px;
}

.mini-facts {
  display: grid;
  gap: 8px;
  margin: 12px 0 0;
}

.mini-facts div {
  align-items: center;
  display: flex;
  justify-content: space-between;
}

.mini-facts dt {
  color: #52616b;
  font-size: 12px;
}

.mini-facts dd {
  font-size: 13px;
  margin: 0;
}

.record-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.record-list details {
  border: 1px solid #d7e0e5;
  border-radius: 6px;
  overflow: hidden;
}

.record-list summary {
  cursor: pointer;
  display: grid;
  gap: 4px;
  padding: 9px 10px;
}

.record-list summary small {
  color: #52616b;
  font-size: 12px;
}

.record-list pre {
  background: #101820;
  color: #e9f1f4;
  margin: 0;
  max-height: 260px;
  overflow: auto;
  padding: 10px;
}

.coordinator-form {
  display: grid;
  gap: 8px;
}

.coordinator-form input,
.coordinator-form textarea,
.coordinator-form select {
  border: 1px solid #cfd9df;
  border-radius: 6px;
  color: #172026;
  font: inherit;
  padding: 8px 9px;
}

.coordinator-form textarea {
  min-height: 80px;
  resize: vertical;
}
```

- [ ] **Step 3: Run frontend build to catch type/style errors**

Run:

```bash
cd web
npm run build
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/CoordinatorPanel.tsx web/src/styles.css
git commit -m "feat: add coordinator dashboard panel"
```

## Task 4: Wire Coordinator Tab

**Files:**
- Modify: `web/src/App.tsx`

- [ ] **Step 1: Add import and view mode**

Modify `web/src/App.tsx` imports:

```ts
import { CoordinatorPanel } from "./components/CoordinatorPanel";
```

Change the `ViewMode` type to:

```ts
type ViewMode = "kanban" | "runtime" | "readiness" | "outbox" | "providers" | "coordinator" | "config" | "events";
```

- [ ] **Step 2: Add tab button**

Insert this button after the Providers tab:

```tsx
<button className={viewMode === "coordinator" ? "view-tab selected" : "view-tab"} type="button" onClick={() => setViewMode("coordinator")}>
  Coordinator
</button>
```

- [ ] **Step 3: Render panel**

Insert this render branch after Providers:

```tsx
{viewMode === "coordinator" ? <CoordinatorPanel projectId={selectedProjectId} /> : null}
```

- [ ] **Step 4: Verify build**

Run:

```bash
cd web
npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/App.tsx
git commit -m "feat: expose coordinator dashboard tab"
```

## Task 5: DeepSeek Handoff Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/agent-operator-guide.md`

- [ ] **Step 1: Update README verification section**

Add this paragraph near the existing verification scripts in `README.md`:

````markdown
Run the real DeepSeek API/runtime smoke when `~/.agents/auth/deepseek.env` is available:

```bash
set -a
source ~/.agents/auth/deepseek.env
set +a
bash scripts/verify_runtime_deepseek_smoke.sh
```

The script first verifies direct DeepSeek API access through the OpenAI-compatible client, then initializes a one-task project and runs annotation plus QC through `provider: openai_compatible` with `provider_flavor: deepseek`.
````

- [ ] **Step 2: Update operator guide verification section**

Add this paragraph under `## Verification` in `docs/agent-operator-guide.md`:

````markdown
For DeepSeek validation, source the local auth file before running the smoke:

```bash
set -a
source ~/.agents/auth/deepseek.env
set +a
bash scripts/verify_runtime_deepseek_smoke.sh
```

Passing output looks like `DeepSeek runtime smoke passed: ...; status=pending` or `status=accepted`. `pending` is acceptable when QC returns feedback; the script fails only on provider/runtime errors or missing attempts/artifacts.
````

- [ ] **Step 3: Run docs-adjacent verification**

Run:

```bash
bash -n scripts/verify_runtime_deepseek_smoke.sh
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_skill_packaging.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/agent-operator-guide.md
git commit -m "docs: document deepseek runtime smoke"
```

## Task 6: Final Verification

**Files:**
- No source changes expected.

- [ ] **Step 1: Run backend tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest -q
```

Expected: `127 passed` or higher if more tests were added.

- [ ] **Step 2: Run frontend tests and build**

Run:

```bash
cd web
npm test -- --run
npm run build
```

Expected: all Vitest files pass and Vite build succeeds.

- [ ] **Step 3: Run stable verification scripts**

Run:

```bash
bash scripts/verify_export_training_data.sh
bash scripts/verify_runtime_progress.sh
bash scripts/verify_skill_installability.sh
```

Expected: each script prints `passed`.

- [ ] **Step 4: Run real DeepSeek smoke**

Run:

```bash
set -a
source ~/.agents/auth/deepseek.env
set +a
bash scripts/verify_runtime_deepseek_smoke.sh
```

Expected: prints `DeepSeek runtime smoke passed: ...`.

- [ ] **Step 5: Commit verification-only changes if any**

If no files changed, do not commit. If docs or snapshots changed unexpectedly, inspect them and either keep only intentional changes or remove generated output.

## Self-Review

- Spec coverage: this plan closes the remaining operator surface gap by adding UI for coordinator report, rule updates, and long-tail issues. It also formalizes DeepSeek API validation as a handoff step.
- Placeholder scan: no task uses unresolved placeholder language; all code snippets and commands are explicit.
- Type consistency: frontend API functions use `CoordinatorReport`, `CoordinatorRuleUpdatePayload`, and `CoordinatorLongTailIssuePayload` consistently across `types.ts`, `api.ts`, and `CoordinatorPanel.tsx`.
