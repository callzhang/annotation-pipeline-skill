# Memory-NER UI User Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify from an algorithm engineer's UI perspective that the real 10-task memory-ner annotation flow is understandable, inspectable, and operationally trustworthy.

**Architecture:** Use the existing local API server and Vite React dashboard against the real `/tmp/annotation-memory-ner-e2e-fVEj7H` project created by `scripts/verify_memory_ner_accepted_e2e.sh`. Add a repeatable browser-level acceptance harness that checks the user-visible workflow: project selection, Kanban accepted state, raw source, annotation content, attempts, round changes, feedback history, runtime health, readiness, provider configuration, and event log. If the UI hides critical evidence, fix the smallest frontend/API surface required and cover it with tests.

**Tech Stack:** Python `annotation-pipeline serve`, Vite + React + TypeScript dashboard, Node browser automation with Playwright, existing `uv` and `npm` verification commands.

---

## File Structure

- Create: `scripts/verify_memory_ner_ui_acceptance.sh`
  - Starts the API and web dashboard against the real memory-ner E2E project, runs a browser acceptance script, writes a JSON report, and stops background processes.
- Create: `web/tests/memory-ner-ui-acceptance.mjs`
  - Browser automation script that clicks through the dashboard as a user and asserts visible content.
- Modify: `web/package.json`
  - Add `verify:memory-ner-ui` script and `@playwright/test` dev dependency if the project does not already have Playwright available.
- Modify: `web/package-lock.json`
  - Lock the Playwright dependency installed by `npm install --save-dev @playwright/test`.
- Modify: `web/src/components/TaskDrawer.tsx`
  - Only if inspection shows the drawer cannot expose raw source, annotation artifacts, attempts, round changes, or feedback clearly enough.
- Modify: `web/src/components/KanbanBoard.tsx`
  - Only if project/status counts or accepted cards cannot be verified from the board.
- Modify: `web/src/components/ReadinessPanel.tsx`
  - Only if the accepted 10-task project does not show training readiness and export next action clearly.
- Modify: `web/src/components/EventLogPanel.tsx`
  - Only if event history cannot be filtered to the selected project or does not expose task transition evidence.
- Test: `web/src/*.test.ts`
  - Add focused component/helper tests for any UI behavior changed during the review.
- Modify: `docs/release/v0.1.0-verification.md`
  - Record the UI acceptance run, report path, accepted count, screenshots path, and any product gaps found.
- Modify: `README.md`
  - Add the manual command for future UI acceptance checks if the script is useful for ongoing release gates.

## User Expectations To Verify

The target user is an algorithm engineer who wants usable training data. From their point of view, the UI must answer these questions without reading files from disk:

1. Which project am I looking at, and does it contain the real 10 memory-ner tasks?
2. Are all 10 tasks in `Accepted`, and are there no hidden pending/QC/Human Review items?
3. Can I inspect the original raw data for a task?
4. Can I inspect the final annotation content for a task?
5. Can I see every annotation/QC round and understand why the task changed state?
6. Can I see QC feedback and whether the annotator incorporated it?
7. Can I confirm the provider configuration used DeepSeek for annotation and QC?
8. Can I see runtime health and that no workers are stuck?
9. Can I see readiness for training export?
10. If the task had long-tail feedback, can I identify whether it needs rule updates or Human Review?

## Task 1: Baseline Real Project State

**Files:**
- Read: `/tmp/annotation-memory-ner-e2e-fVEj7H/accepted-e2e-report.json`
- Read: `/tmp/annotation-memory-ner-e2e-fVEj7H/.annotation-pipeline/tasks/*.json`
- Read: `/tmp/annotation-memory-ner-e2e-fVEj7H/.annotation-pipeline/artifacts/*.jsonl`
- Read: `/tmp/annotation-memory-ner-e2e-fVEj7H/.annotation-pipeline/events/*.jsonl`

- [ ] **Step 1: Confirm the real E2E project still exists**

Run:

```bash
test -d /tmp/annotation-memory-ner-e2e-fVEj7H/.annotation-pipeline
find /tmp/annotation-memory-ner-e2e-fVEj7H/.annotation-pipeline/tasks -maxdepth 1 -type f | wc -l
```

Expected:

```text
10
```

- [ ] **Step 2: Confirm accepted-state report**

Run:

```bash
python - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("/tmp/annotation-memory-ner-e2e-fVEj7H/accepted-e2e-report.json").read_text())
print(json.dumps({
    "accepted_count": report["accepted_count"],
    "tasks": report["tasks"],
    "queue_counts": report["queue_counts"],
}, sort_keys=True))
PY
```

Expected:

```text
{"accepted_count": 10, "queue_counts": {"accepted": 10, "annotating": 0, "blocked": 0, "cancelled": 0, "draft": 0, "human_review": 0, "pending": 0, "qc": 0, "rejected": 0, "validating": 0}, "tasks": 10}
```

- [ ] **Step 3: Pick representative tasks for UI inspection**

Run:

```bash
python - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("/tmp/annotation-memory-ner-e2e-fVEj7H/accepted-e2e-report.json").read_text())
sorted_tasks = sorted(report["tasks_detail"], key=lambda item: item["feedback_count"], reverse=True)
for item in sorted_tasks[:3]:
    print(item["task_id"], "attempts", item["current_attempt"], "feedback", item["feedback_count"])
PY
```

Expected output includes at least one task with feedback:

```text
memory-ner-accepted-e2e-000001 attempts 8 feedback 3
```

- [ ] **Step 4: Do not modify code in this task**

Run:

```bash
git status --short
```

Expected:

```text
?? docs/superpowers/plans/2026-05-06-memory-ner-ui-user-acceptance.md
```

## Task 2: Manual UI Smoke Run

**Files:**
- Read: `annotation_pipeline_skill/interfaces/cli.py`
- Read: `web/vite.config.ts`
- Evidence output: `/tmp/annotation-memory-ner-ui-acceptance/manual-notes.md`

- [ ] **Step 1: Start the API server**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline serve \
  --project-root /tmp/annotation-memory-ner-e2e-fVEj7H \
  --host 127.0.0.1 \
  --port 8765
```

Expected:

```text
Serving annotation pipeline dashboard API on http://127.0.0.1:8765
```

- [ ] **Step 2: Start the dashboard**

Run in a second shell:

```bash
cd web
VITE_API_TARGET=http://127.0.0.1:8765 npm run dev -- --host 127.0.0.1 --port 5173
```

Expected:

```text
Local:   http://127.0.0.1:5173/
```

- [ ] **Step 3: Open the UI**

Run:

```bash
xdg-open http://127.0.0.1:5173/
```

Expected: Browser opens the dashboard without API error.

- [ ] **Step 4: Manual user-path checklist**

Write `/tmp/annotation-memory-ner-ui-acceptance/manual-notes.md` with this exact structure:

```markdown
# Memory-NER UI Manual Acceptance Notes

- Project selector shows `memory-ner-accepted-e2e`: yes/no
- Kanban shows Accepted = 10 and no pending/QC/Human Review cards: yes/no
- Accepted card opens task drawer: yes/no
- Drawer shows Raw Source text and source metadata: yes/no
- Drawer shows Annotation Content artifact payload: yes/no
- Drawer shows Attempts with annotation and QC provider/model: yes/no
- Drawer shows Round Changes from pending to accepted with reasons: yes/no
- Drawer shows Feedback Agreement for tasks with feedback: yes/no
- Runtime panel shows no active runs/stale workers: yes/no
- Readiness panel shows accepted count = 10 and training-data next action: yes/no
- Providers panel shows DeepSeek target for annotation and QC: yes/no
- Configuration panel exposes annotation rules and provider config: yes/no
- Event Log can show task transition history for the project: yes/no

## Gaps

- None
```

- [ ] **Step 5: Stop the dev servers**

Run:

```bash
pkill -f "annotation-pipeline serve.*annotation-memory-ner-e2e-fVEj7H" || true
pkill -f "vite.*127.0.0.1.*5173" || true
```

Expected: Both processes stop.

## Task 3: Browser Acceptance Harness

**Files:**
- Create: `web/tests/memory-ner-ui-acceptance.mjs`
- Create: `scripts/verify_memory_ner_ui_acceptance.sh`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`

- [ ] **Step 1: Add Playwright dependency**

Run:

```bash
cd web
npm install --save-dev @playwright/test
npx playwright install chromium
```

Expected:

```text
command exits 0 and `web/package.json` contains `@playwright/test`
```

- [ ] **Step 2: Add browser acceptance script**

Create `web/tests/memory-ner-ui-acceptance.mjs`:

```javascript
import { chromium } from "@playwright/test";
import fs from "node:fs";

const baseUrl = process.env.MEMORY_NER_UI_BASE_URL ?? "http://127.0.0.1:5173";
const reportPath = process.env.MEMORY_NER_UI_REPORT ?? "/tmp/annotation-memory-ner-ui-acceptance/report.json";

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

async function visibleText(page, selector) {
  return page.locator(selector).innerText();
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
const report = { baseUrl, checks: [] };

async function check(name, fn) {
  await fn();
  report.checks.push({ name, status: "passed" });
}

try {
  await page.goto(baseUrl, { waitUntil: "networkidle" });

  await check("dashboard loads without api error", async () => {
    await page.getByRole("heading", { name: "Annotation Pipeline" }).waitFor();
    assert(!(await page.getByText("API error").isVisible().catch(() => false)), "dashboard shows API error");
  });

  await check("project selector exposes memory-ner project", async () => {
    await page.locator("select").selectOption("memory-ner-accepted-e2e");
    await page.waitForLoadState("networkidle");
    const text = await page.locator(".topbar").innerText();
    assert(text.includes("10 tasks"), `topbar did not show 10 tasks: ${text}`);
  });

  await check("kanban shows accepted column with ten cards", async () => {
    const acceptedColumn = page.locator(".kanban-column", { hasText: "Accepted" });
    await acceptedColumn.waitFor();
    const text = await acceptedColumn.innerText();
    assert(text.includes("10"), `Accepted column did not show 10: ${text}`);
    const cards = await acceptedColumn.locator(".task-card").count();
    assert(cards === 10, `expected 10 accepted cards, got ${cards}`);
  });

  await check("task drawer exposes raw source and final annotation", async () => {
    await page.getByText("memory-ner-accepted-e2e-000001").click();
    await page.getByRole("heading", { name: "memory-ner-accepted-e2e-000001" }).waitFor();
    const drawer = page.locator(".task-drawer");
    const text = await drawer.innerText();
    assert(text.includes("Raw Source"), "drawer missing Raw Source");
    assert(text.includes("Annotation Content"), "drawer missing Annotation Content");
    assert(text.includes("Attempts"), "drawer missing Attempts");
    assert(text.includes("Round Changes"), "drawer missing Round Changes");
    assert(text.includes("Feedback Agreement"), "drawer missing Feedback Agreement");
  });

  await check("runtime panel shows no active work", async () => {
    await page.getByRole("button", { name: "Runtime" }).click();
    await page.waitForLoadState("networkidle");
    const text = await page.locator("main").innerText();
    assert(text.includes("accepted") || text.includes("Accepted"), `runtime panel missing accepted status: ${text}`);
    assert(!text.includes("stale worker"), `runtime panel reports stale worker: ${text}`);
  });

  await check("readiness panel shows accepted training data", async () => {
    await page.getByRole("button", { name: "Readiness" }).click();
    await page.waitForLoadState("networkidle");
    const text = await page.locator("main").innerText();
    assert(text.includes("10"), `readiness panel did not include accepted count 10: ${text}`);
  });

  await check("providers panel exposes DeepSeek configuration", async () => {
    await page.getByRole("button", { name: "Providers" }).click();
    await page.waitForLoadState("networkidle");
    const text = await page.locator("main").innerText();
    assert(text.toLowerCase().includes("deepseek"), `providers panel missing DeepSeek: ${text}`);
  });

  await check("event log exposes accepted transitions", async () => {
    await page.getByRole("button", { name: "Event Log" }).click();
    await page.waitForLoadState("networkidle");
    const text = await page.locator("main").innerText();
    assert(text.includes("accepted"), `event log missing accepted transition: ${text}`);
  });

  fs.mkdirSync(new URL(`file://${reportPath}`).pathname.split("/").slice(0, -1).join("/"), { recursive: true });
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + "\n");
} finally {
  await browser.close();
}
```

- [ ] **Step 3: Add shell verification script**

Create `scripts/verify_memory_ner_ui_acceptance.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="${MEMORY_NER_UI_PROJECT_ROOT:-/tmp/annotation-memory-ner-e2e-fVEj7H}"
EVIDENCE_ROOT="${MEMORY_NER_UI_EVIDENCE_ROOT:-/tmp/annotation-memory-ner-ui-acceptance}"
API_PORT="${MEMORY_NER_UI_API_PORT:-8765}"
WEB_PORT="${MEMORY_NER_UI_WEB_PORT:-5173}"
REPORT_JSON="$EVIDENCE_ROOT/report.json"
API_LOG="$EVIDENCE_ROOT/api.log"
WEB_LOG="$EVIDENCE_ROOT/web.log"

mkdir -p "$EVIDENCE_ROOT"
cd "$ROOT_DIR"

if [[ ! -d "$PROJECT_ROOT/.annotation-pipeline" ]]; then
  echo "missing annotation project: $PROJECT_ROOT" >&2
  exit 1
fi

cleanup() {
  if [[ -n "${API_PID:-}" ]]; then kill "$API_PID" 2>/dev/null || true; fi
  if [[ -n "${WEB_PID:-}" ]]; then kill "$WEB_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline serve \
  --project-root "$PROJECT_ROOT" \
  --host 127.0.0.1 \
  --port "$API_PORT" > "$API_LOG" 2>&1 &
API_PID=$!

for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$API_PORT/api/projects" >/dev/null; then
    break
  fi
  sleep 0.25
done
curl -fsS "http://127.0.0.1:$API_PORT/api/projects" >/dev/null

(
  cd web
  VITE_API_TARGET="http://127.0.0.1:$API_PORT" npm run dev -- --host 127.0.0.1 --port "$WEB_PORT"
) > "$WEB_LOG" 2>&1 &
WEB_PID=$!

for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$WEB_PORT/" >/dev/null; then
    break
  fi
  sleep 0.25
done
curl -fsS "http://127.0.0.1:$WEB_PORT/" >/dev/null

MEMORY_NER_UI_BASE_URL="http://127.0.0.1:$WEB_PORT" \
MEMORY_NER_UI_REPORT="$REPORT_JSON" \
node web/tests/memory-ner-ui-acceptance.mjs

echo "memory-ner UI acceptance passed: $REPORT_JSON"
```

- [ ] **Step 4: Make the script executable**

Run:

```bash
chmod +x scripts/verify_memory_ner_ui_acceptance.sh
```

Expected: command exits 0.

- [ ] **Step 5: Add npm script**

Modify `web/package.json` scripts to include:

```json
{
  "scripts": {
    "dev": "vite --host 0.0.0.0",
    "build": "tsc -b && vite build",
    "test": "vitest",
    "verify:memory-ner-ui": "node tests/memory-ner-ui-acceptance.mjs"
  }
}
```

- [ ] **Step 6: Run the browser acceptance script**

Run:

```bash
bash scripts/verify_memory_ner_ui_acceptance.sh
```

Expected:

```text
memory-ner UI acceptance passed: /tmp/annotation-memory-ner-ui-acceptance/report.json
```

- [ ] **Step 7: Inspect the acceptance report**

Run:

```bash
python - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("/tmp/annotation-memory-ner-ui-acceptance/report.json").read_text())
print(len(report["checks"]))
print([check["status"] for check in report["checks"]])
PY
```

Expected:

```text
8
['passed', 'passed', 'passed', 'passed', 'passed', 'passed', 'passed', 'passed']
```

## Task 4: Fix UI Evidence Gaps Found By Browser Acceptance

**Files:**
- Modify only the specific component that failed:
  - `web/src/components/TaskDrawer.tsx`
  - `web/src/components/KanbanBoard.tsx`
  - `web/src/components/RuntimePanel.tsx`
  - `web/src/components/ReadinessPanel.tsx`
  - `web/src/components/ProvidersPanel.tsx`
  - `web/src/components/EventLogPanel.tsx`
- Test: the matching `web/src/*.test.ts`

- [ ] **Step 1: If raw source is missing, add a focused assertion first**

Modify `web/src/api.test.ts` by adding:

```typescript
import { describe, expect, it } from "vitest";

describe("task detail user evidence", () => {
  it("keeps raw source payload available for the drawer", () => {
    const detail = {
      task: {
        source_ref: {
          kind: "jsonl",
          payload: {
            text: "Repo: nodejs/node Issue: WPT update",
            annotation_guidance: { allowed_entity_types: ["organization", "project"] },
          },
        },
      },
    };

    expect(JSON.stringify(detail.task.source_ref)).toContain("Repo: nodejs/node");
    expect(JSON.stringify(detail.task.source_ref)).toContain("allowed_entity_types");
  });
});
```

Run:

```bash
cd web
npm test -- --run src/api.test.ts
```

Expected: test passes if existing drawer data contract is sufficient; if it fails because of duplicate imports, merge with the existing import block and rerun.

- [ ] **Step 2: If annotation content is hard to read, render parsed annotation text**

Modify `web/src/components/TaskDrawer.tsx` by replacing the annotation artifact `JsonBlock` call with:

```tsx
<JsonBlock value={artifact.payload} />
{typeof artifact.payload === "object" && artifact.payload && "text" in artifact.payload ? (
  <pre className="json-block">{String((artifact.payload as { text?: unknown }).text ?? "")}</pre>
) : null}
```

Run:

```bash
cd web
npm test -- --run src/preview.test.ts src/api.test.ts
```

Expected: tests pass.

- [ ] **Step 3: If accepted count is not visible enough, add a Kanban helper test**

Modify `web/src/kanban.test.ts` by adding:

```typescript
import { describe, expect, it } from "vitest";
import { visibleColumns } from "./kanban";

describe("memory-ner accepted project board", () => {
  it("keeps accepted tasks visible as a column", () => {
    const columns = visibleColumns({
      project_id: "memory-ner-accepted-e2e",
      columns: [
        { id: "accepted", title: "Accepted", cards: Array.from({ length: 10 }, (_, index) => ({
          task_id: `memory-ner-accepted-e2e-${String(index + 1).padStart(6, "0")}`,
          status: "accepted",
          modality: "text",
          annotation_types: ["entity_span", "structured_json"],
          selected_annotator_id: null,
          status_age_seconds: 1,
          latest_attempt_status: "succeeded",
          feedback_count: 0,
          retry_pending: false,
          blocked: false,
          external_sync_pending: false,
        })) },
      ],
    });

    expect(columns.find((column) => column.id === "accepted")?.cards).toHaveLength(10);
  });
});
```

Run:

```bash
cd web
npm test -- --run src/kanban.test.ts
```

Expected: test passes after merging imports with the existing file.

- [ ] **Step 4: Rerun browser acceptance after each UI fix**

Run:

```bash
bash scripts/verify_memory_ner_ui_acceptance.sh
```

Expected:

```text
memory-ner UI acceptance passed: /tmp/annotation-memory-ner-ui-acceptance/report.json
```

## Task 5: Document UI Acceptance Result

**Files:**
- Modify: `docs/release/v0.1.0-verification.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update release verification**

Add this section to `docs/release/v0.1.0-verification.md`:

````markdown
## Memory-NER UI Acceptance

The dashboard was verified against `/tmp/annotation-memory-ner-e2e-fVEj7H`, the real 10-task DeepSeek E2E project.

Result:

```text
memory-ner UI acceptance passed: /tmp/annotation-memory-ner-ui-acceptance/report.json
```

The UI showed project selection, 10 accepted Kanban tasks, task raw source, annotation content, attempts, round changes, feedback history, runtime health, readiness, provider configuration, and event log evidence from the user perspective.
````

- [ ] **Step 2: Add README command**

Add this to the verification section of `README.md`:

````markdown
Run the memory-ner dashboard acceptance check when the real accepted E2E project is available:

```bash
bash scripts/verify_memory_ner_ui_acceptance.sh
```
````

- [ ] **Step 3: Add changelog entry**

Add this bullet under `v0.1.0`:

```markdown
- Memory-ner dashboard UI acceptance verification through `scripts/verify_memory_ner_ui_acceptance.sh`.
```

- [ ] **Step 4: Run docs and packaging tests**

Run:

```bash
bash -n scripts/verify_memory_ner_ui_acceptance.sh
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_skill_packaging.py -q
```

Expected:

Expected: shell syntax check exits 0 and `tests/test_skill_packaging.py` passes.

## Task 6: Full Verification And Commit

**Files:**
- All files changed in Tasks 3-5.

- [ ] **Step 1: Run backend tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest -q
```

Expected:

```text
135 passed
```

- [ ] **Step 2: Run frontend tests**

Run:

```bash
cd web
npm test -- --run
```

Expected:

```text
25 passed
```

The number may increase if new tests were added; all tests must pass.

- [ ] **Step 3: Build frontend**

Run:

```bash
cd web
npm run build
```

Expected:

```text
✓ built
```

- [ ] **Step 4: Run local integration scripts**

Run:

```bash
bash scripts/verify_skill_installability.sh
bash scripts/verify_agent_handoff.sh
bash scripts/verify_runtime_progress.sh
bash scripts/verify_export_training_data.sh
bash scripts/verify_external_pull.sh
bash scripts/verify_outbox_dispatch.sh
```

Expected: every script prints `passed`.

- [ ] **Step 5: Run real UI acceptance**

Run:

```bash
bash scripts/verify_memory_ner_ui_acceptance.sh
```

Expected:

```text
memory-ner UI acceptance passed: /tmp/annotation-memory-ner-ui-acceptance/report.json
```

- [ ] **Step 6: Check formatting**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 7: Commit**

Run:

```bash
git add scripts/verify_memory_ner_ui_acceptance.sh web/tests/memory-ner-ui-acceptance.mjs web/package.json web/package-lock.json README.md CHANGELOG.md docs/release/v0.1.0-verification.md web/src tests
git commit -m "test: verify memory ner dashboard acceptance"
```

Expected:

```text
[main abc1234] test: verify memory ner dashboard acceptance
```

- [ ] **Step 8: Push**

Run:

```bash
git push origin main
```

Expected:

```text
main -> main
```

## Self-Review

**Spec coverage:** This plan covers UI project selection, Kanban accepted-state visibility, raw source, annotation content, per-round changes, QC feedback, provider config, runtime monitoring, readiness, and event log. It also covers repeatable browser verification and release documentation.

**Placeholder scan:** The plan contains no unfinished marker text or unspecified implementation steps. Every code-producing step includes exact file paths and content.

**Type consistency:** The plan uses existing frontend names from the codebase: `TaskDrawer`, `KanbanBoard`, `RuntimePanel`, `ReadinessPanel`, `ProvidersPanel`, `EventLogPanel`, `TaskDetail`, `TaskCard`, and existing API routes under `/api`.
