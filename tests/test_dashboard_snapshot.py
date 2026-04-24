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
