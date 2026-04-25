from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.core.transitions import InvalidTransition, transition_task
import pytest


def test_task_defaults_start_as_draft():
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})

    assert task.task_id == "task-1"
    assert task.status is TaskStatus.DRAFT
    assert task.current_attempt == 0
    assert task.external_ref is None
    assert task.metadata == {}


def test_transition_task_updates_state_and_returns_audit_event():
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})

    event = transition_task(
        task,
        TaskStatus.PENDING,
        actor="tester",
        reason="source slice created",
        stage="prepare",
    )

    assert task.status is TaskStatus.PENDING
    assert event.previous_status == TaskStatus.DRAFT
    assert event.next_status == TaskStatus.PENDING
    assert event.actor == "tester"
    assert event.reason == "source slice created"


def test_invalid_transition_is_rejected():
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})

    with pytest.raises(InvalidTransition):
        transition_task(task, TaskStatus.ACCEPTED, actor="tester", reason="bad jump", stage="accept")
