# Real Multistage Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct annotation-to-accepted behavior with a real annotation, validation, QC, feedback, and rerun loop.

**Architecture:** Keep `SubagentRuntime` as the local runtime entrypoint but split its internals into annotation attempt, validation gate, QC attempt, and feedback recording helpers. The runtime writes chronological task-wide attempts and artifacts, uses the configured annotation target plus the configured `"qc"` target, and treats QC failure as business feedback rather than scheduler failure.

**Tech Stack:** Python dataclasses and filesystem store, existing `LLMClient` protocol, pytest, Bash verification scripts, existing Vite React task detail UI.

---

## File Structure

- Modify `annotation_pipeline_skill/runtime/subagent_cycle.py`: split stage execution, parse QC result, write QC artifacts, record feedback, and include feedback context in rerun prompts.
- Modify `annotation_pipeline_skill/runtime/local_scheduler.py`: count accepted only when QC passes and leave QC-failed tasks for later cycles.
- Modify `tests/test_subagent_cycle.py`: add pass, fail, rerun tests.
- Modify `tests/test_local_runtime_scheduler.py`: update accepted count expectations for QC-driven acceptance.
- Modify `scripts/verify_runtime_progress.sh`: use a fake Codex provider that first QC-fails one task and then accepts after rerun.
- Modify `docs/agent-operator-guide.md`: document the multistage loop.

### Task 1: Runtime Unit Tests

**Files:**
- Modify: `tests/test_subagent_cycle.py`

- [ ] **Step 1: Write failing tests**

Add tests for annotation+QC pass, QC fail recording feedback, and a second-cycle rerun using feedback context.

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_subagent_cycle.py -q`

Expected: tests fail because current runtime accepts immediately after annotation and never calls QC.

### Task 2: Runtime Implementation

**Files:**
- Modify: `annotation_pipeline_skill/runtime/subagent_cycle.py`

- [ ] **Step 1: Split stage helpers**

Implement helpers for annotation prompt, validation, QC prompt, QC parsing, artifact writing, attempt writing, and feedback recording.

- [ ] **Step 2: Preserve runtime exceptions**

Provider call exceptions should still bubble to the scheduler and be counted as runtime failures.

- [ ] **Step 3: Run runtime tests**

Run: `uv run pytest tests/test_subagent_cycle.py -q`

Expected: tests pass.

### Task 3: Scheduler And Progress Verification

**Files:**
- Modify: `tests/test_local_runtime_scheduler.py`
- Modify: `scripts/verify_runtime_progress.sh`

- [ ] **Step 1: Update scheduler tests**

Expect accepted counts to reflect QC pass only. QC failure should keep tasks pending and failed count at zero.

- [ ] **Step 2: Update progress script**

Make the fake provider fail QC once for one task, rerun annotation with feedback, then pass QC in the next runtime cycle.

- [ ] **Step 3: Run scheduler tests and progress script**

Run: `uv run pytest tests/test_local_runtime_scheduler.py -q`

Run: `bash scripts/verify_runtime_progress.sh`

Expected: both pass.

### Task 4: Docs And Full Verification

**Files:**
- Modify: `docs/agent-operator-guide.md`

- [ ] **Step 1: Document multistage behavior**

Explain that QC failure writes feedback and returns the task to pending for a later annotation rerun.

- [ ] **Step 2: Run full verification**

Run:

```bash
uv run pytest -q
npm test -- --run
npm run build
bash scripts/verify_runtime_progress.sh
bash scripts/verify_runtime_e2e.sh
bash scripts/verify_runtime_codex_smoke.sh
```

Expected: all pass.

- [ ] **Step 3: Commit and push**

Commit the completed phase and push `main`.

## Self-Review

- Spec coverage: annotation, validation, QC, feedback rerun, task detail visibility, scheduler progress, and verification scripts are all mapped to tasks.
- Placeholder scan: no placeholders remain.
- Type consistency: this plan uses existing `TaskStatus`, `Attempt`, `ArtifactRef`, `FeedbackRecord`, and `LLMGenerateResult` names.
