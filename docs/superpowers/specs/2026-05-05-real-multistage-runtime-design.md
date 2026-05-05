# Real Multistage Runtime Design

## Goal

Phase 3.5 replaces the current simplified subagent cycle, which accepts a task immediately after annotation, with a real local multistage loop:

```text
Pending -> Annotating -> Validating -> QC -> Accepted
```

If QC rejects an annotation, the runtime records structured feedback and sends the task back through annotation with the feedback bundle and prior artifacts available to the annotator.

## Scope

This phase implements the smallest useful end-to-end loop:

- Annotation target produces an `annotation_result` artifact.
- Deterministic validation checks that the annotation artifact is non-empty.
- QC target receives task source, latest annotation artifact, and feedback history.
- QC target returns JSON with `passed: true | false`.
- QC pass marks the task `accepted`.
- QC fail records a `FeedbackRecord`, increments a QC attempt, and returns the task to `pending` for a later annotation rerun.
- Annotation rerun prompt includes prior annotation artifacts and feedback bundle.

This phase does not implement:

- External task API execution.
- Callback delivery.
- Human Review action buttons.
- Merge/export sinks.
- Schema-specific validation.

## Runtime Contract

`SubagentRuntime.run_task(task, stage_target="annotation")` keeps the same public shape but changes behavior:

- Pending tasks run one annotation attempt and then one QC attempt in the same scheduler cycle.
- The annotation provider is selected from `stage_target`.
- The QC provider is selected from the `"qc"` target.
- The accepted count only increases when QC passes.
- A QC failure is not a runtime error. It is product feedback and leaves the task pending for rerun.
- Provider exceptions remain runtime failures and are counted in cycle errors.

The scheduler can still process multiple tasks per cycle. A task that QC-fails should not loop repeatedly in the same cycle; it should be eligible in a later cycle so operators can inspect events and feedback between attempts.

## QC Response Format

QC output is parsed as JSON. The minimal accepted shapes are:

```json
{"passed": true, "summary": "acceptable"}
```

```json
{
  "passed": false,
  "message": "entity span is missing",
  "category": "quality",
  "severity": "warning",
  "suggested_action": "annotator_rerun",
  "target": {"field": "entities"}
}
```

If QC output is not JSON, the runtime treats it as a QC failure and records the raw text as feedback. This is not a semantic keyword fallback; it is structural parsing of the expected protocol.

## Artifacts And Attempts

Each annotation attempt writes:

- `attempt.stage = "annotation"`
- `artifact.kind = "annotation_result"`
- artifact payload includes task id, model text, raw response, usage, diagnostics.

Each QC attempt writes:

- `attempt.stage = "qc"`
- `artifact.kind = "qc_result"`
- artifact payload includes parsed QC decision, raw response, usage, diagnostics.

Attempt indices use the task-wide `current_attempt` sequence so the UI can show chronological order across annotation and QC.

## Feedback

QC failure writes one `FeedbackRecord` with:

- `source_stage = FeedbackSource.QC`
- `severity` mapped from JSON when valid, otherwise warning.
- `category` from JSON or `"qc"`.
- `message` from JSON or raw QC output.
- `target` from JSON or `{}`.
- `suggested_action` from JSON or `"annotator_rerun"`.

The next annotation prompt includes `build_feedback_bundle(store, task_id)` and all prior artifact payload summaries. This gives annotator reruns concrete context without inventing a separate repair state.

## UI Impact

No new UI component is required in this phase. The existing Task Drawer already shows:

- source data,
- attempts,
- artifacts,
- events,
- feedback records,
- feedback discussions.

The new runtime data should appear there naturally once attempts and artifacts are written correctly.

## Verification

Tests must cover:

- Annotation + QC pass writes annotation and QC attempts/artifacts and accepts the task.
- QC fail writes feedback and returns task to pending without counting a runtime failure.
- Second cycle reruns annotation with feedback context and accepts after QC pass.
- Scheduler accepted count reflects QC pass only.
- Runtime progress script verifies one task needs two cycles: first QC fails, second QC passes.

Full verification after implementation:

- `uv run pytest -q`
- `npm test -- --run`
- `npm run build`
- `bash scripts/verify_runtime_progress.sh`
- `bash scripts/verify_runtime_e2e.sh`
- `bash scripts/verify_runtime_codex_smoke.sh`
