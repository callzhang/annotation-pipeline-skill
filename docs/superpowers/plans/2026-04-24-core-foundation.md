# Core Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first local-first backend foundation for durable annotation tasks, transitions, events, feedback records, external outbox records, and dashboard snapshots.

**Architecture:** Keep the core domain as dataclasses and enums with no provider or HTTP logic. Put state transition validation in `core/transitions.py`, persistence in `store/file_store.py`, orchestration helpers in `services/`, and monitor/dashboard shaping in focused modules. Tests drive every behavior before implementation.

**Tech Stack:** Python 3.11+, standard library dataclasses/json/pathlib, pytest.

---

## Scope

This plan implements the first vertical slice of the spec. It does not implement the Vite React UI, real external HTTP clients, real provider clients, or real multimodal renderers. It creates the backend contracts those later components need.

The workspace is not a git repository, so commit steps are replaced by a local status note.

## File Structure

- Create `pyproject.toml`: package metadata and pytest configuration.
- Create `annotation_pipeline_skill/__init__.py`: package version export.
- Create `annotation_pipeline_skill/core/states.py`: task, attempt, feedback, and outbox enums.
- Create `annotation_pipeline_skill/core/models.py`: dataclasses for `Task`, `Attempt`, `ArtifactRef`, `FeedbackRecord`, `ExternalTaskRef`, `OutboxRecord`, and `AuditEvent`.
- Create `annotation_pipeline_skill/core/transitions.py`: allowed task transitions and transition helper.
- Create `annotation_pipeline_skill/store/file_store.py`: JSON persistence for tasks, events, feedback, attempts, artifacts, and outbox records.
- Create `annotation_pipeline_skill/services/feedback_service.py`: compact feedback bundle construction.
- Create `annotation_pipeline_skill/services/external_task_service.py`: idempotent external task creation and outbox creation.
- Create `annotation_pipeline_skill/services/dashboard_service.py`: Kanban snapshot construction.
- Create `tests/test_models_and_transitions.py`: state transition tests.
- Create `tests/test_file_store.py`: save/load and append tests.
- Create `tests/test_feedback_and_external.py`: feedback bundle and external outbox tests.
- Create `tests/test_dashboard_snapshot.py`: Kanban snapshot tests.

## Task 1: Package Skeleton And Domain Models

**Files:**
- Create: `pyproject.toml`
- Create: `annotation_pipeline_skill/__init__.py`
- Create: `annotation_pipeline_skill/core/__init__.py`
- Create: `annotation_pipeline_skill/core/states.py`
- Create: `annotation_pipeline_skill/core/models.py`
- Test: `tests/test_models_and_transitions.py`

- [ ] **Step 1: Write the failing model test**

```python
from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus


def test_task_defaults_start_as_draft():
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})

    assert task.task_id == "task-1"
    assert task.status is TaskStatus.DRAFT
    assert task.current_attempt == 0
    assert task.external_ref is None
    assert task.metadata == {}
```

- [ ] **Step 2: Run the test and verify it fails because the package is missing**

Run: `pytest tests/test_models_and_transitions.py::test_task_defaults_start_as_draft -v`

Expected: FAIL with `ModuleNotFoundError` or missing `Task`.

- [ ] **Step 3: Implement minimal domain models**

Create enums for task states, attempt states, feedback severity/source, and outbox status. Create dataclasses with `to_dict()` and `from_dict()` helpers so persistence can remain structured and deterministic.

- [ ] **Step 4: Run the test and verify it passes**

Run: `pytest tests/test_models_and_transitions.py::test_task_defaults_start_as_draft -v`

Expected: PASS.

## Task 2: Validated State Transitions And Audit Events

**Files:**
- Modify: `annotation_pipeline_skill/core/transitions.py`
- Modify: `annotation_pipeline_skill/core/models.py`
- Test: `tests/test_models_and_transitions.py`

- [ ] **Step 1: Add failing transition tests**

```python
import pytest

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.core.transitions import InvalidTransition, transition_task


def test_transition_task_updates_state_and_returns_audit_event():
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})

    event = transition_task(
        task,
        TaskStatus.READY,
        actor="tester",
        reason="source slice created",
        stage="prepare",
    )

    assert task.status is TaskStatus.READY
    assert event.previous_status == TaskStatus.DRAFT
    assert event.next_status == TaskStatus.READY
    assert event.actor == "tester"
    assert event.reason == "source slice created"


def test_invalid_transition_is_rejected():
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})

    with pytest.raises(InvalidTransition):
        transition_task(task, TaskStatus.MERGED, actor="tester", reason="bad jump", stage="merge")
```

- [ ] **Step 2: Run the tests and verify the new cases fail**

Run: `pytest tests/test_models_and_transitions.py -v`

Expected: FAIL because transition logic is missing.

- [ ] **Step 3: Implement transition validation**

Define the allowed transition graph from the design spec. `transition_task()` mutates the task status, refreshes `updated_at`, and returns an `AuditEvent`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `pytest tests/test_models_and_transitions.py -v`

Expected: PASS.

## Task 3: File Store Persistence

**Files:**
- Create: `annotation_pipeline_skill/store/__init__.py`
- Create: `annotation_pipeline_skill/store/file_store.py`
- Test: `tests/test_file_store.py`

- [ ] **Step 1: Write failing persistence tests**

```python
from annotation_pipeline_skill.core.models import FeedbackRecord, Task
from annotation_pipeline_skill.core.states import FeedbackSeverity, FeedbackSource
from annotation_pipeline_skill.store.file_store import FileStore


def test_file_store_saves_and_loads_tasks(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})

    store.save_task(task)
    loaded = store.load_task("task-1")

    assert loaded == task


def test_file_store_appends_feedback_records(tmp_path):
    store = FileStore(tmp_path)
    record = FeedbackRecord.new(
        task_id="task-1",
        attempt_id="attempt-1",
        source_stage=FeedbackSource.QC,
        severity=FeedbackSeverity.ERROR,
        category="missing_entity",
        message="Missing required entity",
        target={"field": "entities"},
        suggested_action="annotator_rerun",
        created_by="qc-policy",
    )

    store.append_feedback(record)

    assert store.list_feedback("task-1") == [record]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_file_store.py -v`

Expected: FAIL because `FileStore` is missing.

- [ ] **Step 3: Implement JSON file store**

Use deterministic paths under the store root:

- `tasks/<task_id>.json`
- `events/<task_id>.jsonl`
- `feedback/<task_id>.jsonl`
- `attempts/<task_id>.jsonl`
- `artifacts/<task_id>.jsonl`
- `outbox/<record_id>.json`

- [ ] **Step 4: Run the tests and verify they pass**

Run: `pytest tests/test_file_store.py -v`

Expected: PASS.

## Task 4: Feedback Bundles And External Outbox

**Files:**
- Create: `annotation_pipeline_skill/services/__init__.py`
- Create: `annotation_pipeline_skill/services/feedback_service.py`
- Create: `annotation_pipeline_skill/services/external_task_service.py`
- Test: `tests/test_feedback_and_external.py`

- [ ] **Step 1: Write failing service tests**

```python
from annotation_pipeline_skill.core.models import FeedbackRecord, Task
from annotation_pipeline_skill.core.states import FeedbackSeverity, FeedbackSource, OutboxKind
from annotation_pipeline_skill.services.external_task_service import ExternalTaskService
from annotation_pipeline_skill.services.feedback_service import build_feedback_bundle
from annotation_pipeline_skill.store.file_store import FileStore


def test_feedback_bundle_orders_records_by_creation_time(tmp_path):
    store = FileStore(tmp_path)
    first = FeedbackRecord.new(
        task_id="task-1",
        attempt_id="attempt-1",
        source_stage=FeedbackSource.QC,
        severity=FeedbackSeverity.ERROR,
        category="format",
        message="Bad JSON shape",
        target={"path": "$"},
        suggested_action="bulk_code_repair",
        created_by="validator",
    )
    second = FeedbackRecord.new(
        task_id="task-1",
        attempt_id="attempt-2",
        source_stage=FeedbackSource.HUMAN_REVIEW,
        severity=FeedbackSeverity.WARNING,
        category="boundary",
        message="Box is too loose",
        target={"box_id": "b1"},
        suggested_action="manual_annotation",
        created_by="reviewer",
    )
    store.append_feedback(second)
    store.append_feedback(first)

    bundle = build_feedback_bundle(store, "task-1")

    assert [item["message"] for item in bundle["items"]] == ["Bad JSON shape", "Box is too loose"]


def test_external_task_pull_is_idempotent_and_creates_status_outbox(tmp_path):
    store = FileStore(tmp_path)
    service = ExternalTaskService(store)

    first = service.upsert_pulled_task(
        pipeline_id="pipe",
        system_id="external",
        external_task_id="42",
        payload={"text": "hello"},
    )
    second = service.upsert_pulled_task(
        pipeline_id="pipe",
        system_id="external",
        external_task_id="42",
        payload={"text": "hello again"},
    )
    record = service.enqueue_status(first, status="ready")

    assert first.task_id == second.task_id
    assert record.kind is OutboxKind.STATUS
    assert store.list_outbox() == [record]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_feedback_and_external.py -v`

Expected: FAIL because services are missing.

- [ ] **Step 3: Implement feedback and external services**

`build_feedback_bundle()` reads feedback records and returns ordered compact dictionaries. `ExternalTaskService.upsert_pulled_task()` derives an internal task id from `system_id` and `external_task_id`, saves the task once, and preserves the original idempotency key. `enqueue_status()` creates an outbox record without making network calls.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `pytest tests/test_feedback_and_external.py -v`

Expected: PASS.

## Task 5: Dashboard Snapshot Shape

**Files:**
- Create: `annotation_pipeline_skill/services/dashboard_service.py`
- Test: `tests/test_dashboard_snapshot.py`

- [ ] **Step 1: Write failing dashboard snapshot test**

```python
from annotation_pipeline_skill.core.models import FeedbackRecord, Task
from annotation_pipeline_skill.core.states import FeedbackSeverity, FeedbackSource, TaskStatus
from annotation_pipeline_skill.services.dashboard_service import build_kanban_snapshot
from annotation_pipeline_skill.store.file_store import FileStore


def test_dashboard_snapshot_groups_tasks_into_operational_columns(tmp_path):
    store = FileStore(tmp_path)
    ready = Task.new(task_id="task-ready", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    ready.status = TaskStatus.READY
    review = Task.new(task_id="task-review", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    review.status = TaskStatus.HUMAN_REVIEW
    review.modality = "image"
    review.annotation_requirements = {"annotation_types": ["bounding_box"]}
    store.save_task(ready)
    store.save_task(review)
    store.append_feedback(
        FeedbackRecord.new(
            task_id="task-review",
            attempt_id="attempt-1",
            source_stage=FeedbackSource.QC,
            severity=FeedbackSeverity.WARNING,
            category="bbox",
            message="Review box boundary",
            target={"box_id": "b1"},
            suggested_action="manual_annotation",
            created_by="qc",
        )
    )

    snapshot = build_kanban_snapshot(store)

    assert [column["id"] for column in snapshot["columns"]] == [
        "ready",
        "annotating",
        "validating",
        "qc",
        "human_review",
        "repair",
        "accepted",
        "rejected",
        "merged",
    ]
    assert snapshot["columns"][0]["cards"][0]["task_id"] == "task-ready"
    assert snapshot["columns"][4]["cards"][0]["feedback_count"] == 1
    assert snapshot["columns"][4]["cards"][0]["modality"] == "image"
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `pytest tests/test_dashboard_snapshot.py -v`

Expected: FAIL because `build_kanban_snapshot` is missing.

- [ ] **Step 3: Implement dashboard snapshot builder**

Group saved tasks into the operational columns selected in the spec. Cards include task id, modality, annotation type summary, selected annotator id, status age placeholder seconds, latest attempt status, feedback count, retry state, blocked flag, and external sync flag.

- [ ] **Step 4: Run the test and verify it passes**

Run: `pytest tests/test_dashboard_snapshot.py -v`

Expected: PASS.

## Task 6: Full Test Run And Documentation Check

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the complete test suite**

Run: `pytest -v`

Expected: all tests PASS.

- [ ] **Step 2: Add a README with the implemented slice**

Document:

- local-first core status
- no Streamlit
- no frontend implementation yet
- how to run tests
- where the design spec and plan live

- [ ] **Step 3: Run the complete test suite again**

Run: `pytest -v`

Expected: all tests PASS.

## Self-Review

- Spec coverage: this plan covers domain models, state transitions, file store, feedback records, external outbox records, and dashboard snapshot shape. Frontend Kanban UI and real provider/external HTTP clients remain separate follow-up plans.
- Placeholder scan: no unresolved placeholder markers remain.
- Type consistency: task, feedback, outbox, and status names are consistent across the planned tests and modules.
