import json

import pytest

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.core.transitions import InvalidTransition
from annotation_pipeline_skill.services.human_review_service import HumanReviewService
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def _human_review_task(store: SqliteStore) -> Task:
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)
    return task


def test_human_review_accepts_task_and_records_decision_artifact(tmp_path):
    store = SqliteStore.open(tmp_path)
    _human_review_task(store)

    result = HumanReviewService(store).decide(
        task_id="task-1",
        action="accept",
        actor="algorithm-engineer",
        feedback="Labels are usable for training.",
        correction_mode="manual_annotation",
    )

    artifacts = store.list_artifacts("task-1")
    payload = json.loads((store.root / artifacts[0].path).read_text(encoding="utf-8"))
    event = store.list_events("task-1")[0]
    assert result.task.status is TaskStatus.ACCEPTED
    assert artifacts[0].kind == "human_review_decision"
    assert payload["action"] == "accept"
    assert payload["feedback"] == "Labels are usable for training."
    assert event.reason == "human review accepted task"
    assert event.metadata["decision_artifact_id"] == artifacts[0].artifact_id


def test_human_review_request_changes_returns_task_to_annotator_with_feedback(tmp_path):
    store = SqliteStore.open(tmp_path)
    _human_review_task(store)

    result = HumanReviewService(store).decide(
        task_id="task-1",
        action="request_changes",
        actor="algorithm-engineer",
        feedback="Apply the new boundary rule to all rows.",
        correction_mode="batch_code_update",
    )

    payload = json.loads((store.root / store.list_artifacts("task-1")[0].path).read_text(encoding="utf-8"))
    event = store.list_events("task-1")[0]
    assert result.task.status is TaskStatus.ANNOTATING
    assert payload["correction_mode"] == "batch_code_update"
    assert event.reason == "human review requested annotator changes"
    assert event.metadata["correction_mode"] == "batch_code_update"


def test_human_review_rejects_task(tmp_path):
    store = SqliteStore.open(tmp_path)
    _human_review_task(store)

    result = HumanReviewService(store).decide(
        task_id="task-1",
        action="reject",
        actor="algorithm-engineer",
        feedback="Source data is unusable.",
        correction_mode="manual_annotation",
    )

    assert result.task.status is TaskStatus.REJECTED
    assert store.list_events("task-1")[0].reason == "human review rejected task"


def test_human_review_rejects_actions_outside_human_review(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.QC
    store.save_task(task)

    with pytest.raises(InvalidTransition):
        HumanReviewService(store).decide(
            task_id="task-1",
            action="accept",
            actor="algorithm-engineer",
            feedback="Accept",
            correction_mode="manual_annotation",
        )


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
    assert task_after.status is TaskStatus.HUMAN_REVIEW


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
