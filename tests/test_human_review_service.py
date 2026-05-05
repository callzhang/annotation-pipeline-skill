import json

import pytest

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.core.transitions import InvalidTransition
from annotation_pipeline_skill.services.human_review_service import HumanReviewService
from annotation_pipeline_skill.store.file_store import FileStore


def _human_review_task(store: FileStore) -> Task:
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)
    return task


def test_human_review_accepts_task_and_records_decision_artifact(tmp_path):
    store = FileStore(tmp_path)
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
    store = FileStore(tmp_path)
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
    store = FileStore(tmp_path)
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
    store = FileStore(tmp_path)
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
