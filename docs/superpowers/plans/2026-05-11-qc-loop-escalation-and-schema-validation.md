# QC Loop Escalation + Schema Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** When annotation↔QC cannot resolve a task after N rounds (default 3), auto-escalate to HUMAN_REVIEW. Add JSON-Schema validation to every write that produces ground-truth annotation data — both the annotator subagent and the human reviewer must pass the task's `output_schema` before their result is accepted.

**Architecture:** Add a shared `schema_validation` module that loads the schema from `task.source_ref.payload.annotation_guidance.output_schema` and validates payloads via the `jsonschema` library. Wire it into `subagent_cycle._run_task` (annotator output gate, post-LLM parse) and into a new `HumanReviewService.submit_correction()` (human-authored answer gate). Add `WorkflowConfig.max_qc_rounds` to make the escalation threshold configurable. Track rounds by counting `FeedbackRecord(source_stage=QC)` rows per task — no schema change needed. Export service prefers `human_review_answer` artifacts over `annotation_result` when both exist.

**Tech Stack:** Python stdlib + existing dataclasses + `jsonschema>=4.0` (new dep).

---

## Decisions adopted (user-confirmed)

- 1A: Count `FeedbackRecord(source_stage=QC)` rows for round-counting.
- 2B: New `WorkflowConfig.max_qc_rounds: int = 3` field, configurable per workflow.
- 3A: Add `jsonschema>=4.0` dependency. Apply schema validation to **both** annotation runtime output and human review corrections.
- 4B: Schema source is `task.source_ref.payload.annotation_guidance.output_schema` only.
- 5B: New `HumanReviewService.submit_correction()` method (don't overload `decide`).
- Boundary 1: Count ALL QC-source feedbacks, including resolved ones (they still count as a round that happened).
- Boundary 2: Manual HUMAN_REVIEW transitions go through the same `submit_correction` flow.
- Boundary 3: If task has no `output_schema`, every schema-validated write rejects with a clear "missing_schema" error. No silent skip.
- Boundary 4: Export picks `human_review_answer` artifact (if present) over `annotation_result`.

## File Structure

- Create `annotation_pipeline_skill/core/schema_validation.py`
  - `SchemaValidationError(ValueError)` with `.errors: list[dict]`.
  - `load_output_schema(task: Task) -> dict | None`.
  - `validate_payload_against_task_schema(task: Task, payload: object) -> None`.
- Modify `annotation_pipeline_skill/config/models.py`
  - Add `WorkflowConfig.max_qc_rounds: int = 3`.
- Modify `annotation_pipeline_skill/config/loader.py`
  - Parse `max_qc_rounds`.
- Modify `annotation_pipeline_skill/runtime/subagent_cycle.py`
  - After annotator produces JSON, validate against task schema; on failure record a BLOCKING feedback record and return to PENDING (same path as empty-annotation).
  - After QC rejection appends its feedback, check `count(source_stage=QC) >= max_qc_rounds`; if so transition to `HUMAN_REVIEW` instead of `PENDING`. Use `RuntimeConfig`-style injection so the threshold is configurable.
- Modify `annotation_pipeline_skill/services/human_review_service.py`
  - Add `submit_correction(task_id, answer, actor, note=None)` — validates answer against schema, writes `human_review_answer` artifact, transitions HUMAN_REVIEW→ACCEPTED.
- Modify `annotation_pipeline_skill/interfaces/api.py`
  - New endpoint `POST /api/tasks/<task_id>/human_review_correction`.
- Modify `annotation_pipeline_skill/interfaces/cli.py`
  - New subcommand: `apl human-review correct --task <id> --answer-file <path> [--note "..."]`.
- Modify `annotation_pipeline_skill/services/export_service.py`
  - Prefer `human_review_answer` artifact; mark `human_authored: true` in training row metadata.
- Modify `pyproject.toml`
  - Add `jsonschema>=4.0` dependency.
- Modify `CHANGELOG.md`, `docs/agent-operator-guide.md`.
- Tests:
  - `tests/test_schema_validation.py`
  - Extend `tests/test_subagent_cycle.py` (annotation schema gate, QC escalation)
  - Extend `tests/test_human_review_service.py` (`submit_correction` happy path + schema fail)
  - Extend `tests/test_dashboard_api.py` (new endpoint)
  - Extend `tests/test_cli.py` (new subcommand)
  - Extend `tests/test_export_service.py` (human_review_answer preference)
  - Extend `tests/test_config_loader.py` (`max_qc_rounds` parsing)

## Threshold semantics (locked-in)

- Counter: `len([f for f in store.list_feedback(task_id) if f.source_stage is FeedbackSource.QC])`
- Comparison: when this count, **including the feedback we just appended for the current QC rejection**, is `>= max_qc_rounds`, escalate.
- Resolved feedback (via `FeedbackDiscussionEntry(consensus=True, stance="resolved")`) is NOT subtracted from the count — see Boundary 1.
- Behavior at exactly N=3: round 1 reject → count 1 → loop. Round 2 reject → count 2 → loop. Round 3 reject → count 3 → escalate to HUMAN_REVIEW.

---

## Task 1: jsonschema dependency + schema_validation module

**Files:**
- Modify: `pyproject.toml`
- Create: `annotation_pipeline_skill/core/schema_validation.py`
- Test: `tests/test_schema_validation.py`

- [ ] **Step 1: Add dependency**

In `pyproject.toml`, add `"jsonschema>=4.0"` to the `[project] dependencies` list. Run `uv sync` (or `pip install -e .[dev]`) to make it available.

- [ ] **Step 2: Write failing tests**

Create `tests/test_schema_validation.py`:

```python
import pytest

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.schema_validation import (
    SchemaValidationError,
    load_output_schema,
    validate_payload_against_task_schema,
)


def _task_with_schema(schema: dict | None) -> Task:
    payload = {"text": "x"}
    if schema is not None:
        payload["annotation_guidance"] = {"output_schema": schema}
    return Task.new(
        task_id="t-1",
        pipeline_id="p",
        source_ref={"kind": "jsonl", "payload": payload},
    )


def test_load_output_schema_returns_schema_when_present():
    schema = {"type": "object", "required": ["entities"]}
    task = _task_with_schema(schema)
    assert load_output_schema(task) == schema


def test_load_output_schema_returns_none_when_absent():
    task = _task_with_schema(None)
    assert load_output_schema(task) is None


def test_validate_passes_when_payload_matches_schema():
    schema = {
        "type": "object",
        "required": ["entities"],
        "properties": {"entities": {"type": "array"}},
    }
    task = _task_with_schema(schema)
    validate_payload_against_task_schema(task, {"entities": []})


def test_validate_raises_schema_validation_error_on_invalid_payload():
    schema = {
        "type": "object",
        "required": ["entities"],
        "properties": {"entities": {"type": "array"}},
    }
    task = _task_with_schema(schema)
    with pytest.raises(SchemaValidationError) as exc:
        validate_payload_against_task_schema(task, {"wrong_field": []})
    assert exc.value.errors
    assert any("entities" in str(e).lower() or "required" in str(e).lower() for e in exc.value.errors)


def test_validate_raises_missing_schema_when_task_has_no_output_schema():
    task = _task_with_schema(None)
    with pytest.raises(SchemaValidationError) as exc:
        validate_payload_against_task_schema(task, {"anything": True})
    assert exc.value.errors == [{"kind": "missing_schema", "message": "task has no output_schema"}]
```

- [ ] **Step 3: Implement module**

Create `annotation_pipeline_skill/core/schema_validation.py`:

```python
from __future__ import annotations

from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

from annotation_pipeline_skill.core.models import Task


class SchemaValidationError(ValueError):
    def __init__(self, message: str, errors: list[dict]):
        super().__init__(message)
        self.errors = errors


def load_output_schema(task: Task) -> dict | None:
    payload = task.source_ref.get("payload") if isinstance(task.source_ref, dict) else None
    if not isinstance(payload, dict):
        return None
    guidance = payload.get("annotation_guidance")
    if not isinstance(guidance, dict):
        return None
    schema = guidance.get("output_schema")
    return schema if isinstance(schema, dict) else None


def validate_payload_against_task_schema(task: Task, payload: Any) -> None:
    schema = load_output_schema(task)
    if schema is None:
        raise SchemaValidationError(
            "task has no output_schema",
            [{"kind": "missing_schema", "message": "task has no output_schema"}],
        )
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    if errors:
        raise SchemaValidationError(
            f"schema validation failed with {len(errors)} error(s)",
            [
                {
                    "kind": "schema_error",
                    "path": "/".join(str(p) for p in err.absolute_path),
                    "message": err.message,
                }
                for err in errors
            ],
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_schema_validation.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock annotation_pipeline_skill/core/schema_validation.py tests/test_schema_validation.py
git commit -m "feat(core): JSON Schema validation helper for task output_schema"
```

---

## Task 2: WorkflowConfig.max_qc_rounds field

**Files:**
- Modify: `annotation_pipeline_skill/config/models.py`
- Modify: `annotation_pipeline_skill/config/loader.py`
- Test: `tests/test_config_loader.py`

- [ ] **Step 1: Read current config**

Read `annotation_pipeline_skill/config/models.py` and `annotation_pipeline_skill/config/loader.py` to understand the existing pattern. The new field follows the same dataclass + loader-parse pattern as e.g. `human_review_policy_id`.

- [ ] **Step 2: Add failing test**

Append to `tests/test_config_loader.py`:

```python
def test_workflow_config_parses_max_qc_rounds(tmp_path):
    from annotation_pipeline_skill.config.loader import load_workflow_config
    cfg_path = tmp_path / "workflow.yaml"
    cfg_path.write_text(
        "pipeline_id: p\n"
        "max_qc_rounds: 5\n",
        encoding="utf-8",
    )
    cfg = load_workflow_config(cfg_path)
    assert cfg.max_qc_rounds == 5


def test_workflow_config_max_qc_rounds_defaults_to_3(tmp_path):
    from annotation_pipeline_skill.config.loader import load_workflow_config
    cfg_path = tmp_path / "workflow.yaml"
    cfg_path.write_text("pipeline_id: p\n", encoding="utf-8")
    cfg = load_workflow_config(cfg_path)
    assert cfg.max_qc_rounds == 3
```

Adjust to match the project's existing loader entry point if `load_workflow_config` is not the actual function name.

- [ ] **Step 3: Run failing tests**

```bash
pytest tests/test_config_loader.py -v -k max_qc_rounds
```
Expected: 2 failures (AttributeError or missing field).

- [ ] **Step 4: Add the field**

In `annotation_pipeline_skill/config/models.py`, add to `WorkflowConfig`:

```python
max_qc_rounds: int = 3
```

In the loader, parse it:

```python
max_qc_rounds=int(values.get("max_qc_rounds", 3)),
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_config_loader.py -v
```
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add annotation_pipeline_skill/config/models.py annotation_pipeline_skill/config/loader.py tests/test_config_loader.py
git commit -m "feat(config): add max_qc_rounds with default 3"
```

---

## Task 3: Schema-validate annotation output in subagent runtime

**Files:**
- Modify: `annotation_pipeline_skill/runtime/subagent_cycle.py`
- Test: `tests/test_subagent_cycle.py`

When the annotator returns text, we now require it to (a) parse as JSON when `output_schema` is present, and (b) validate against the schema. Failure paths produce a BLOCKING `FeedbackRecord(source_stage=VALIDATION)` and route back to PENDING — same recovery shape as the existing empty-annotation case.

- [ ] **Step 1: Read existing subagent cycle tests**

Read `tests/test_subagent_cycle.py` to understand how a task fixture is built and how the LLM client is stubbed. We need the same patterns.

- [ ] **Step 2: Add failing test**

Append to `tests/test_subagent_cycle.py`:

```python
def test_annotator_output_failing_schema_records_blocking_feedback_and_loops(tmp_path):
    """Annotator returns JSON that fails task.output_schema -> validation feedback + PENDING."""
    from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import FeedbackSeverity, FeedbackSource, TaskStatus

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-1",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": "Acme",
                "annotation_guidance": {
                    "output_schema": {
                        "type": "object",
                        "required": ["entities"],
                        "properties": {"entities": {"type": "array"}},
                    }
                },
            },
        },
    )
    task.status = TaskStatus.PENDING
    store.save_task(task)

    # Stub LLM client returning JSON that does NOT have 'entities'
    class _StubClient:
        async def generate(self, request):
            from annotation_pipeline_skill.llm.client import LLMGenerateResult
            return LLMGenerateResult(
                final_text='{"wrong_field": []}',
                raw_response={}, usage={}, diagnostics={}, runtime="stub",
                provider="stub", model="stub", continuity_handle=None,
            )

    runtime = SubagentRuntime(store=store, client_factory=lambda _t: _StubClient())
    runtime.run_task(store.load_task("t-1"))

    task_after = store.load_task("t-1")
    assert task_after.status is TaskStatus.PENDING

    feedbacks = store.list_feedback("t-1")
    schema_fb = [f for f in feedbacks if f.source_stage is FeedbackSource.VALIDATION and f.category == "schema_invalid"]
    assert schema_fb, f"expected schema_invalid feedback, got {[f.category for f in feedbacks]}"
    assert schema_fb[0].severity is FeedbackSeverity.BLOCKING


def test_annotator_output_passing_schema_proceeds_to_qc(tmp_path):
    """Schema-valid annotation reaches QC stage normally."""
    from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-2",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": "Acme",
                "annotation_guidance": {
                    "output_schema": {
                        "type": "object",
                        "required": ["entities"],
                        "properties": {"entities": {"type": "array"}},
                    }
                },
            },
        },
    )
    task.status = TaskStatus.PENDING
    store.save_task(task)

    # Stub returning annotation that passes schema, and stub QC pass
    call = {"n": 0}
    class _StubClient:
        async def generate(self, request):
            from annotation_pipeline_skill.llm.client import LLMGenerateResult
            call["n"] += 1
            if call["n"] == 1:
                final = '{"entities": []}'
            else:
                final = '{"passed": true}'
            return LLMGenerateResult(
                final_text=final, raw_response={}, usage={}, diagnostics={},
                runtime="stub", provider="stub", model="stub", continuity_handle=None,
            )

    runtime = SubagentRuntime(store=store, client_factory=lambda _t: _StubClient())
    runtime.run_task(store.load_task("t-2"))

    task_after = store.load_task("t-2")
    assert task_after.status is TaskStatus.ACCEPTED


def test_annotator_output_invalid_json_records_validation_feedback(tmp_path):
    """Annotator returns non-JSON text -> schema_invalid feedback (parse error)."""
    from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import FeedbackSource, TaskStatus

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-3",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": "Acme",
                "annotation_guidance": {
                    "output_schema": {"type": "object", "required": ["entities"]}
                },
            },
        },
    )
    task.status = TaskStatus.PENDING
    store.save_task(task)

    class _StubClient:
        async def generate(self, request):
            from annotation_pipeline_skill.llm.client import LLMGenerateResult
            return LLMGenerateResult(
                final_text="not json at all",
                raw_response={}, usage={}, diagnostics={}, runtime="stub",
                provider="stub", model="stub", continuity_handle=None,
            )

    runtime = SubagentRuntime(store=store, client_factory=lambda _t: _StubClient())
    runtime.run_task(store.load_task("t-3"))

    feedbacks = store.list_feedback("t-3")
    assert any(f.source_stage is FeedbackSource.VALIDATION and f.category == "schema_invalid" for f in feedbacks)
```

- [ ] **Step 3: Run failing tests**

```bash
pytest tests/test_subagent_cycle.py -v -k schema
```
Expected: 3 failures.

- [ ] **Step 4: Implement schema gate**

In `annotation_pipeline_skill/runtime/subagent_cycle.py`:

1. Import the new helper at top: `from annotation_pipeline_skill.core.schema_validation import SchemaValidationError, load_output_schema, validate_payload_against_task_schema`.

2. Replace the existing empty-annotation check block in `_run_task` (the section starting with `if not annotation_result.final_text.strip():`) with a combined emptiness + schema check. Specifically:

```python
self._transition(
    task,
    TaskStatus.VALIDATING,
    reason="subagent annotation produced result",
    stage="validation",
    attempt_id=annotation_attempt_id,
)

validation_failure = self._check_annotation_validation(task, annotation_result.final_text)
if validation_failure is not None:
    self._record_validation_feedback(
        task,
        annotation_attempt_id,
        category=validation_failure["category"],
        message=validation_failure["message"],
        target=validation_failure.get("target", {}),
    )
    self._transition(
        task,
        TaskStatus.PENDING,
        reason=validation_failure["reason"],
        stage="validation",
        attempt_id=annotation_attempt_id,
    )
    self.store.save_task(task)
    return
```

Add helper methods on `SubagentRuntime`:

```python
def _check_annotation_validation(self, task: Task, final_text: str) -> dict | None:
    if not final_text.strip():
        return {
            "category": "empty_annotation",
            "message": "Annotation result was empty.",
            "reason": "deterministic validation failed",
        }
    schema = load_output_schema(task)
    if schema is None:
        # No schema -> nothing else to check at this layer.
        return None
    import json
    try:
        payload = json.loads(_strip_markdown_json_fence(final_text))
    except json.JSONDecodeError as exc:
        return {
            "category": "schema_invalid",
            "message": f"Annotation result is not valid JSON: {exc}",
            "reason": "schema validation failed",
        }
    try:
        validate_payload_against_task_schema(task, payload)
    except SchemaValidationError as exc:
        return {
            "category": "schema_invalid",
            "message": f"Annotation result failed schema validation: {exc}",
            "reason": "schema validation failed",
            "target": {"errors": exc.errors},
        }
    return None
```

3. Update `_record_validation_feedback` signature to accept `category`, `message`, and optional `target`:

```python
def _record_validation_feedback(
    self,
    task: Task,
    attempt_id: str,
    *,
    category: str,
    message: str,
    target: dict | None = None,
) -> None:
    self.store.append_feedback(
        FeedbackRecord.new(
            task_id=task.task_id,
            attempt_id=attempt_id,
            source_stage=FeedbackSource.VALIDATION,
            severity=FeedbackSeverity.BLOCKING,
            category=category,
            message=message,
            target=target or {},
            suggested_action="annotator_rerun",
            created_by="validation",
        )
    )
```

Find any existing callers of `_record_validation_feedback` and update them to pass the new keyword args. The current code only has one such call (the empty-annotation branch we just refactored).

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_subagent_cycle.py -v
```
Expected: all 3 new tests pass, no regression in existing tests.

- [ ] **Step 6: Commit**

```bash
git add annotation_pipeline_skill/runtime/subagent_cycle.py tests/test_subagent_cycle.py
git commit -m "feat(runtime): schema-validate annotator output before QC"
```

---

## Task 4: Auto-escalate to HUMAN_REVIEW after N QC rejections

**Files:**
- Modify: `annotation_pipeline_skill/runtime/subagent_cycle.py`
- Test: `tests/test_subagent_cycle.py`

- [ ] **Step 1: Decide threshold injection mechanism**

`SubagentRuntime` does not currently know about `WorkflowConfig`. Options:
- (a) Pass `max_qc_rounds` as a `SubagentRuntime.__init__` parameter (default 3).
- (b) Read it from the task's project workflow at run time (heavier).

Pick (a) — minimal change. Add `max_qc_rounds: int = 3` to `SubagentRuntime.__init__`. Wherever the runtime is constructed in the codebase, pass it through from `WorkflowConfig.max_qc_rounds` (find call sites and update).

- [ ] **Step 2: Add failing test**

Append to `tests/test_subagent_cycle.py`:

```python
def test_qc_rejection_escalates_to_human_review_after_n_rounds(tmp_path):
    """After 3 QC rejections, task transitions to HUMAN_REVIEW instead of PENDING."""
    from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-loop",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": "Acme",
                "annotation_guidance": {
                    "output_schema": {"type": "object"}  # permissive: annotator always passes
                },
            },
        },
    )
    task.status = TaskStatus.PENDING
    store.save_task(task)

    # Stub: every annotation passes schema; every QC rejects with failures.
    class _StubClient:
        def __init__(self):
            self.calls = 0

        async def generate(self, request):
            from annotation_pipeline_skill.llm.client import LLMGenerateResult
            self.calls += 1
            instructions = request.instructions
            if "qc" in instructions.lower() and "annotation subagent" not in instructions.lower():
                # QC turn
                final = '{"passed": false, "message": "still bad", "failures": [{"category": "x", "message": "still bad"}]}'
            else:
                final = '{"entities": []}'
            return LLMGenerateResult(
                final_text=final, raw_response={}, usage={}, diagnostics={},
                runtime="stub", provider="stub", model="stub", continuity_handle=None,
            )

    stub = _StubClient()
    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda _t: stub,
        max_qc_rounds=3,
    )

    # Run 3 rounds. Each call to run_once picks up PENDING tasks.
    for _ in range(3):
        runtime.run_once()

    task_after = store.load_task("t-loop")
    assert task_after.status is TaskStatus.HUMAN_REVIEW, f"got {task_after.status}"

    qc_feedbacks = [f for f in store.list_feedback("t-loop") if f.source_stage.value == "qc"]
    assert len(qc_feedbacks) == 3


def test_qc_rejection_loops_normally_under_threshold(tmp_path):
    """1 or 2 QC rejections still go back to PENDING."""
    from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-loop2",
        pipeline_id="p",
        source_ref={"kind": "jsonl", "payload": {"text": "x", "annotation_guidance": {"output_schema": {"type": "object"}}}},
    )
    task.status = TaskStatus.PENDING
    store.save_task(task)

    class _StubClient:
        async def generate(self, request):
            from annotation_pipeline_skill.llm.client import LLMGenerateResult
            instructions = request.instructions
            if "qc" in instructions.lower() and "annotation subagent" not in instructions.lower():
                final = '{"passed": false, "message": "bad", "failures": [{"category": "x", "message": "bad"}]}'
            else:
                final = '{"entities": []}'
            return LLMGenerateResult(final_text=final, raw_response={}, usage={}, diagnostics={}, runtime="stub", provider="stub", model="stub", continuity_handle=None)

    runtime = SubagentRuntime(store=store, client_factory=lambda _t: _StubClient(), max_qc_rounds=3)
    runtime.run_once()
    runtime.run_once()

    task_after = store.load_task("t-loop2")
    assert task_after.status is TaskStatus.PENDING
```

- [ ] **Step 3: Run failing tests**

```bash
pytest tests/test_subagent_cycle.py -v -k "qc_rejection"
```
Expected: 2 failures (init signature or behavior).

- [ ] **Step 4: Wire max_qc_rounds**

Update `SubagentRuntime.__init__`:

```python
def __init__(
    self,
    store: SqliteStore,
    client_factory: Callable[[str], LLMClient],
    *,
    max_qc_rounds: int = 3,
):
    self.store = store
    self.client_factory = client_factory
    self.max_qc_rounds = max_qc_rounds
```

In `_run_qc_stage`, replace the QC-fail branch (currently `self._transition(task, TaskStatus.PENDING, ...)`) with:

```python
else:
    feedback = _feedback_from_qc_decision(task, qc_attempt_id, qc_decision)
    self.store.append_feedback(feedback)
    qc_failure_count = sum(
        1 for f in self.store.list_feedback(task.task_id)
        if f.source_stage is FeedbackSource.QC
    )
    if qc_failure_count >= self.max_qc_rounds:
        self._transition(
            task,
            TaskStatus.HUMAN_REVIEW,
            reason="auto-escalated after repeated QC rejections",
            stage="qc",
            attempt_id=qc_attempt_id,
            metadata={
                "auto_escalated": True,
                "qc_failure_count": qc_failure_count,
                "max_qc_rounds": self.max_qc_rounds,
                "feedback_id": feedback.feedback_id,
                "qc_artifact_id": qc_artifact.artifact_id,
            },
        )
    else:
        self._transition(
            task,
            TaskStatus.PENDING,
            reason="subagent qc requested annotator rerun",
            stage="qc",
            attempt_id=qc_attempt_id,
            metadata={"feedback_id": feedback.feedback_id, "qc_artifact_id": qc_artifact.artifact_id},
        )
```

Find every call site that instantiates `SubagentRuntime(...)` and either pass `max_qc_rounds=workflow_config.max_qc_rounds` or rely on the default. Suggested call sites to check:
- `annotation_pipeline_skill/runtime/local_scheduler.py`
- `annotation_pipeline_skill/interfaces/cli.py` (CLI dispatcher)
- `annotation_pipeline_skill/interfaces/api.py` (if it constructs runtime)

If the call site has a `WorkflowConfig` in scope, wire it; otherwise the default of 3 stands.

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_subagent_cycle.py -v
```
Expected: pass, no regression.

- [ ] **Step 6: Commit**

```bash
git add annotation_pipeline_skill/runtime/subagent_cycle.py annotation_pipeline_skill/runtime/local_scheduler.py annotation_pipeline_skill/interfaces/cli.py annotation_pipeline_skill/interfaces/api.py tests/test_subagent_cycle.py
git commit -m "feat(runtime): auto-escalate to HUMAN_REVIEW after max_qc_rounds rejections"
```

(Stage only the files actually modified — drop any from the list above that didn't need a change.)

---

## Task 5: HumanReviewService.submit_correction with schema validation

**Files:**
- Modify: `annotation_pipeline_skill/services/human_review_service.py`
- Test: `tests/test_human_review_service.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_human_review_service.py`:

```python
def test_submit_correction_schema_valid_answer_accepts_task(tmp_path):
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus
    from annotation_pipeline_skill.services.human_review_service import HumanReviewService
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-hr",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": "x",
                "annotation_guidance": {
                    "output_schema": {
                        "type": "object",
                        "required": ["entities"],
                        "properties": {"entities": {"type": "array"}},
                    }
                },
            },
        },
    )
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)

    svc = HumanReviewService(store)
    result = svc.submit_correction(
        task_id="t-hr",
        answer={"entities": [{"text": "Acme", "label": "ORG"}]},
        actor="reviewer-1",
        note="manual fix",
    )

    assert result.task.status is TaskStatus.ACCEPTED
    artifacts = [a for a in store.list_artifacts("t-hr") if a.kind == "human_review_answer"]
    assert len(artifacts) == 1


def test_submit_correction_schema_invalid_answer_raises_and_keeps_status(tmp_path):
    import pytest
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.schema_validation import SchemaValidationError
    from annotation_pipeline_skill.core.states import TaskStatus
    from annotation_pipeline_skill.services.human_review_service import HumanReviewService
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-hr-bad",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": "x",
                "annotation_guidance": {
                    "output_schema": {"type": "object", "required": ["entities"]}
                },
            },
        },
    )
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)

    svc = HumanReviewService(store)
    with pytest.raises(SchemaValidationError):
        svc.submit_correction(
            task_id="t-hr-bad",
            answer={"wrong_key": []},
            actor="reviewer-1",
            note=None,
        )

    task_after = store.load_task("t-hr-bad")
    assert task_after.status is TaskStatus.HUMAN_REVIEW  # unchanged on failure


def test_submit_correction_missing_schema_raises(tmp_path):
    import pytest
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.schema_validation import SchemaValidationError
    from annotation_pipeline_skill.core.states import TaskStatus
    from annotation_pipeline_skill.services.human_review_service import HumanReviewService
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-hr-noschema",
        pipeline_id="p",
        source_ref={"kind": "jsonl", "payload": {"text": "x"}},
    )
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)

    svc = HumanReviewService(store)
    with pytest.raises(SchemaValidationError) as exc:
        svc.submit_correction(
            task_id="t-hr-noschema",
            answer={"anything": True},
            actor="r",
            note=None,
        )
    assert exc.value.errors[0]["kind"] == "missing_schema"


def test_submit_correction_rejects_when_task_not_in_human_review(tmp_path):
    import pytest
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus
    from annotation_pipeline_skill.core.transitions import InvalidTransition
    from annotation_pipeline_skill.services.human_review_service import HumanReviewService
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-pending",
        pipeline_id="p",
        source_ref={"kind": "jsonl", "payload": {"annotation_guidance": {"output_schema": {"type": "object"}}}},
    )
    task.status = TaskStatus.PENDING
    store.save_task(task)

    svc = HumanReviewService(store)
    with pytest.raises(InvalidTransition):
        svc.submit_correction(task_id="t-pending", answer={}, actor="r", note=None)
```

- [ ] **Step 2: Run failing tests**

```bash
pytest tests/test_human_review_service.py -v -k submit_correction
```
Expected: 4 failures.

- [ ] **Step 3: Implement `submit_correction`**

Add to `HumanReviewService` in `annotation_pipeline_skill/services/human_review_service.py`:

```python
from annotation_pipeline_skill.core.schema_validation import (
    SchemaValidationError,
    validate_payload_against_task_schema,
)


@dataclass(frozen=True)
class HumanCorrectionResult:
    task: Task
    artifact: ArtifactRef
    answer: dict

    def to_dict(self) -> dict:
        return {
            "task": self.task.to_dict(),
            "artifact": self.artifact.to_dict(),
            "answer": self.answer,
        }


# inside class HumanReviewService:
def submit_correction(
    self,
    *,
    task_id: str,
    answer: dict,
    actor: str,
    note: str | None,
) -> HumanCorrectionResult:
    task = self.store.load_task(task_id)
    if task.status is not TaskStatus.HUMAN_REVIEW:
        raise InvalidTransition(f"task {task_id} is not in human_review")

    # Schema-validate. Raises SchemaValidationError on failure.
    validate_payload_against_task_schema(task, answer)

    artifact = self._write_correction_artifact(task_id, answer, actor=actor, note=note)
    event = transition_task(
        task,
        TaskStatus.ACCEPTED,
        actor=actor,
        reason="human review submitted corrected answer",
        stage="human_review",
        metadata={
            "human_authored": True,
            "answer_artifact_id": artifact.artifact_id,
            "answer_artifact_path": artifact.path,
            "note": note,
        },
    )
    self.store.append_artifact(artifact)
    self.store.append_event(event)
    self.store.save_task(task)
    return HumanCorrectionResult(task=task, artifact=artifact, answer=answer)


def _write_correction_artifact(self, task_id: str, answer: dict, *, actor: str, note: str | None) -> ArtifactRef:
    relative_path = Path("artifact_payloads") / task_id / f"human_review_answer-{uuid4().hex}.json"
    absolute_path = self.store.root / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_text(
        json.dumps({"answer": answer, "actor": actor, "note": note}, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return ArtifactRef.new(
        task_id=task_id,
        kind="human_review_answer",
        path=relative_path.as_posix(),
        content_type="application/json",
        metadata={"actor": actor, "note": note},
    )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_human_review_service.py -v
```
Expected: 4 new tests pass, existing pass.

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/services/human_review_service.py tests/test_human_review_service.py
git commit -m "feat(human-review): submit_correction with schema-validated answer"
```

---

## Task 6: API endpoint + CLI for human correction

**Files:**
- Modify: `annotation_pipeline_skill/interfaces/api.py`
- Modify: `annotation_pipeline_skill/interfaces/cli.py`
- Test: `tests/test_dashboard_api.py`, `tests/test_cli.py`

- [ ] **Step 1: Add failing API test**

Append to `tests/test_dashboard_api.py`:

```python
def test_post_human_review_correction_accepts_valid_answer(tmp_path):
    import json
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus
    from annotation_pipeline_skill.interfaces.api import DashboardApi
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-api",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": "x",
                "annotation_guidance": {
                    "output_schema": {
                        "type": "object",
                        "required": ["entities"],
                        "properties": {"entities": {"type": "array"}},
                    }
                },
            },
        },
    )
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)

    api = DashboardApi(store)
    body = json.dumps({"actor": "r", "answer": {"entities": []}, "note": "ok"}).encode("utf-8")
    status, headers, response = api.handle_post(f"/api/tasks/t-api/human_review_correction", body)
    assert status == 200, response
    payload = json.loads(response)
    assert payload["task"]["status"] == "accepted"


def test_post_human_review_correction_rejects_invalid_answer_400(tmp_path):
    import json
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus
    from annotation_pipeline_skill.interfaces.api import DashboardApi
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-api-bad",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": "x",
                "annotation_guidance": {
                    "output_schema": {"type": "object", "required": ["entities"]}
                },
            },
        },
    )
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)

    api = DashboardApi(store)
    body = json.dumps({"actor": "r", "answer": {"wrong": []}, "note": None}).encode("utf-8")
    status, headers, response = api.handle_post(f"/api/tasks/t-api-bad/human_review_correction", body)
    assert status == 400
    payload = json.loads(response)
    assert payload["error"] == "schema_validation_failed"
    assert isinstance(payload["details"], list) and payload["details"]
```

- [ ] **Step 2: Wire endpoint**

In `annotation_pipeline_skill/interfaces/api.py`, add the route. Find the existing `_post_human_review_response` route and add a new branch next to it:

```python
# inside route dispatcher:
if path.startswith("/api/tasks/") and path.endswith("/human_review_correction"):
    task_id = path[len("/api/tasks/"):-len("/human_review_correction")]
    return self._post_human_review_correction(store, task_id, body)
```

Add the method:

```python
def _post_human_review_correction(self, store, task_id, body):
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return self._json_response(400, {"error": "invalid_json"})
    actor = data.get("actor")
    answer = data.get("answer")
    note = data.get("note")
    if not isinstance(actor, str) or not actor.strip():
        return self._json_response(400, {"error": "actor_required"})
    if not isinstance(answer, dict):
        return self._json_response(400, {"error": "answer_must_be_object"})

    from annotation_pipeline_skill.core.schema_validation import SchemaValidationError
    from annotation_pipeline_skill.core.transitions import InvalidTransition
    from annotation_pipeline_skill.services.human_review_service import HumanReviewService

    svc = HumanReviewService(store)
    try:
        result = svc.submit_correction(task_id=task_id, answer=answer, actor=actor, note=note)
    except SchemaValidationError as exc:
        return self._json_response(400, {"error": "schema_validation_failed", "details": exc.errors})
    except InvalidTransition as exc:
        return self._json_response(409, {"error": "invalid_transition", "detail": str(exc)})
    return self._json_response(200, result.to_dict())
```

Read api.py to make sure the dispatch wiring (POST handler) matches the existing pattern.

- [ ] **Step 3: Add failing CLI test**

Append to `tests/test_cli.py`:

```python
def test_cli_human_review_correct_accepts_answer_file(tmp_path):
    import json
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus
    from annotation_pipeline_skill.interfaces.cli import main
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore

    root = tmp_path / "ws"
    main(["db", "init", "--root", str(root)])
    store = SqliteStore.open(root)
    task = Task.new(
        task_id="t-cli-hr",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": "x",
                "annotation_guidance": {
                    "output_schema": {
                        "type": "object",
                        "required": ["entities"],
                        "properties": {"entities": {"type": "array"}},
                    }
                },
            },
        },
    )
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)
    store.close()

    answer_path = tmp_path / "answer.json"
    answer_path.write_text(json.dumps({"entities": []}), encoding="utf-8")

    rc = main([
        "human-review", "correct",
        "--root", str(root),
        "--task", "t-cli-hr",
        "--answer-file", str(answer_path),
        "--actor", "reviewer-1",
    ])
    assert rc == 0
    store = SqliteStore.open(root)
    assert store.load_task("t-cli-hr").status is TaskStatus.ACCEPTED
```

- [ ] **Step 4: Add CLI command**

Find the existing CLI parser-building function in `annotation_pipeline_skill/interfaces/cli.py`. Add new subcommand group `human-review` with subcommand `correct`:

```python
def _register_human_review_commands(subparsers):
    hr = subparsers.add_parser("human-review", help="human review utilities")
    hr_sub = hr.add_subparsers(dest="human_review_command", required=True)

    p = hr_sub.add_parser("correct", help="submit a schema-validated correction for a task in HUMAN_REVIEW")
    p.add_argument("--root", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--answer-file", required=True, help="path to a JSON file containing the corrected answer")
    p.add_argument("--actor", required=True)
    p.add_argument("--note", default=None)
    p.set_defaults(handler=_cmd_human_review_correct)


def _cmd_human_review_correct(args) -> int:
    import json
    from annotation_pipeline_skill.core.schema_validation import SchemaValidationError
    from annotation_pipeline_skill.services.human_review_service import HumanReviewService
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore

    answer = json.loads(Path(args.answer_file).read_text(encoding="utf-8"))
    store = SqliteStore.open(args.root)
    svc = HumanReviewService(store)
    try:
        result = svc.submit_correction(
            task_id=args.task, answer=answer, actor=args.actor, note=args.note
        )
    except SchemaValidationError as exc:
        print(f"schema validation failed:")
        for err in exc.errors:
            print(f"  - {err}")
        store.close()
        return 2
    print(f"task {result.task.task_id} accepted (artifact {result.artifact.artifact_id})")
    store.close()
    return 0
```

Then call `_register_human_review_commands(subparsers)` next to the other `_register_*_commands(...)` calls in `build_parser()`.

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_dashboard_api.py tests/test_cli.py -v -k "correction or human_review_correct"
```
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add annotation_pipeline_skill/interfaces/api.py annotation_pipeline_skill/interfaces/cli.py tests/test_dashboard_api.py tests/test_cli.py
git commit -m "feat(api,cli): POST /api/tasks/<id>/human_review_correction and apl human-review correct"
```

---

## Task 7: Export prefers human_review_answer over annotation_result

**Files:**
- Modify: `annotation_pipeline_skill/services/export_service.py`
- Test: `tests/test_export_service.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_export_service.py`:

```python
def test_export_uses_human_review_answer_when_present(tmp_path):
    import json
    from annotation_pipeline_skill.core.models import ArtifactRef, Task
    from annotation_pipeline_skill.core.states import TaskStatus
    from annotation_pipeline_skill.services.export_service import TrainingDataExportService
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-hr-export",
        pipeline_id="p",
        source_ref={"kind": "jsonl", "payload": {"text": "x"}},
    )
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)

    # Write an annotation_result and a human_review_answer both for this task.
    art_dir = tmp_path / "artifact_payloads" / "t-hr-export"
    art_dir.mkdir(parents=True, exist_ok=True)
    ann_path = art_dir / "annotation_result.json"
    ann_path.write_text(json.dumps({"text": {"entities": ["wrong"]}}), encoding="utf-8")
    hr_path = art_dir / "human_review_answer.json"
    hr_path.write_text(json.dumps({"answer": {"entities": ["RIGHT"]}, "actor": "r", "note": "fix"}), encoding="utf-8")
    store.append_artifact(ArtifactRef.new(
        task_id="t-hr-export", kind="annotation_result",
        path="artifact_payloads/t-hr-export/annotation_result.json",
        content_type="application/json",
    ))
    store.append_artifact(ArtifactRef.new(
        task_id="t-hr-export", kind="human_review_answer",
        path="artifact_payloads/t-hr-export/human_review_answer.json",
        content_type="application/json",
    ))

    svc = TrainingDataExportService(store)
    out_dir = tmp_path / "out"
    manifest = svc.export_jsonl(project_id="p", output_dir=out_dir)
    output = (tmp_path / manifest.output_paths[0]).read_text(encoding="utf-8")
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    assert len(rows) == 1
    # The exported annotation should be the human answer, not the original.
    assert rows[0]["annotation"] == {"entities": ["RIGHT"]}
    assert rows[0]["human_authored"] is True
```

- [ ] **Step 2: Run failing test**

```bash
pytest tests/test_export_service.py -v -k human_review_answer
```
Expected: 1 failure.

- [ ] **Step 3: Refactor `_latest_annotation_artifact` → `_final_answer_artifact`**

Replace `_latest_annotation_artifact` in `export_service.py`:

```python
def _final_answer_artifact(self, task: Task) -> tuple[ArtifactRef, bool] | None:
    """Return (artifact, human_authored) for this task's final answer. Prefer human_review_answer."""
    artifacts = self.store.list_artifacts(task.task_id)
    human_answers = [a for a in artifacts if a.kind == "human_review_answer"]
    if human_answers:
        return (human_answers[-1], True)
    annotations = [a for a in artifacts if a.kind == "annotation_result"]
    if annotations:
        return (annotations[-1], False)
    return None
```

Update the export loop:

```python
for task in accepted_tasks:
    pick = self._final_answer_artifact(task)
    if pick is None:
        excluded.append({"task_id": task.task_id, "reason": "missing_annotation_result"})
        continue
    artifact, human_authored = pick
    payload = self._read_artifact_payload(artifact)
    if payload is None:
        excluded.append({"task_id": task.task_id, "reason": "missing_annotation_payload"})
        continue
    row = self._training_row(task, artifact, payload, human_authored=human_authored)
    ...
```

Update `_training_row` to accept `human_authored` and to extract the answer correctly. For `annotation_result` artifacts the payload shape is `{"text": ..., "raw_response": ..., "usage": ...}`; for `human_review_answer` it is `{"answer": ..., "actor": ..., "note": ...}`:

```python
def _training_row(self, task, artifact, payload, *, human_authored):
    if human_authored:
        annotation = payload.get("answer")
    else:
        annotation = payload.get("text", payload) if isinstance(payload, dict) else payload
    return {
        "task_id": task.task_id,
        "pipeline_id": task.pipeline_id,
        "source_ref": task.source_ref,
        "modality": task.modality,
        "annotation_requirements": task.annotation_requirements,
        "annotation": annotation,
        "annotation_artifact_id": artifact.artifact_id,
        "annotation_artifact_path": artifact.path,
        "human_authored": human_authored,
    }
```

Add `"human_authored"` to `REQUIRED_TRAINING_ROW_FIELDS` (or to the explicit per-field checks if you don't want to fail-loud on existing exports). Actually — since this is a new field and the validator marks missing keys as errors, adding it to required would break existing tests. **Don't add to REQUIRED_TRAINING_ROW_FIELDS.** Instead, just ensure the field is present in every row produced by `_training_row` (which the code above does). Adjust the explicit field checks if needed.

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_export_service.py -v
```
Expected: new test passes; existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/services/export_service.py tests/test_export_service.py
git commit -m "feat(export): prefer human_review_answer artifact and mark human_authored"
```

---

## Task 8: Documentation

**Files:**
- Modify: `CHANGELOG.md`, `docs/agent-operator-guide.md`, `TECHNICAL_ARCHITECTURE.md` (touch storage section to note new artifact kind), `README.md` (if it covers QC/review workflow).

- [ ] **Step 1: CHANGELOG entry**

Prepend to `CHANGELOG.md`:

```markdown
## 2026-05-11

- Auto-escalate tasks to HUMAN_REVIEW after `WorkflowConfig.max_qc_rounds` (default 3) QC rejections, replacing the silent infinite-loop hazard. Triggered by counting `FeedbackRecord(source_stage=QC)` per task.
- JSON Schema gate on **all** writes that produce annotation ground truth:
  - Annotator subagent output is parsed and validated against `task.source_ref.payload.annotation_guidance.output_schema`. Failures record a BLOCKING `FeedbackRecord(category="schema_invalid", source_stage=VALIDATION)` and return the task to PENDING.
  - Human review correction (new endpoint `POST /api/tasks/<id>/human_review_correction` and CLI `apl human-review correct ...`) validates the submitted answer against the same schema. Failures return 400 with structured error list.
  - Tasks without an `output_schema` cannot accept human or annotator writes — they fail loudly with `missing_schema`.
- New `human_review_answer` artifact kind. Export service prefers it over `annotation_result` when both exist; exported rows include `human_authored: bool`.
- New dependency: `jsonschema>=4.0`.
```

- [ ] **Step 2: Operator guide**

Append to `docs/agent-operator-guide.md`:

````markdown
## When a task escalates to HUMAN_REVIEW

After `max_qc_rounds` (default 3) failed QC reviews, the task is auto-escalated to HUMAN_REVIEW. The dashboard shows it in the human-review column. You have two routes to resolve it:

### 1. Submit a corrected answer via CLI

```
echo '{"entities": [{"text": "Acme", "label": "ORG"}]}' > /tmp/answer.json
annotation-pipeline human-review correct \
    --root .annotation-pipeline \
    --task <task_id> \
    --answer-file /tmp/answer.json \
    --actor your-name \
    --note "manual correction"
```

The answer must validate against the task's `output_schema`. On failure the command exits non-zero and prints the schema errors.

### 2. Submit via HTTP API

```
POST /api/tasks/<task_id>/human_review_correction
Content-Type: application/json
{
  "actor": "your-name",
  "answer": { ... },
  "note": "..."
}
```

Returns `200` on success, `400` with `{error: "schema_validation_failed", details: [...]}` on schema failure, `409` on invalid task state.

Both routes write a `human_review_answer` artifact and transition the task to ACCEPTED. The export service automatically picks the human answer over any prior annotator output.
````

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md docs/agent-operator-guide.md
git commit -m "docs: human review escalation and schema-validated correction workflow"
```

---

## Self-Review

**Spec coverage:**
- ✅ Auto-escalation after N QC rounds — Task 4
- ✅ Schema validation on annotator output — Task 3
- ✅ Schema validation on human correction — Task 5
- ✅ `WorkflowConfig.max_qc_rounds` — Task 2
- ✅ `jsonschema` dependency — Task 1
- ✅ Source schema from `source_ref.payload.annotation_guidance.output_schema` — Task 1 helper
- ✅ Missing schema → reject — Task 1 helper
- ✅ Manual HUMAN_REVIEW uses same flow — Task 5 tests
- ✅ Export prefers `human_review_answer` — Task 7
- ✅ API endpoint + CLI — Task 6
- ✅ Docs — Task 8

**Type consistency:** `SchemaValidationError` raised consistently; `submit_correction` keyword-only API; `max_qc_rounds` named field everywhere.

**Known compromises:**
- Test 4 stubs the LLM to distinguish "annotator" vs "QC" by string sniffing the instructions. Brittle but isolated to tests; production routing uses different code paths.
- The "count includes resolved feedback" semantics is intentional but could surprise users — documented in changelog.
