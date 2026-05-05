from annotation_pipeline_skill.core.models import FeedbackRecord, Task
from annotation_pipeline_skill.core.states import FeedbackSeverity, FeedbackSource, TaskStatus
from annotation_pipeline_skill.services.dashboard_service import build_kanban_snapshot, build_project_summaries
from annotation_pipeline_skill.store.file_store import FileStore


def test_dashboard_snapshot_groups_tasks_into_operational_columns(tmp_path):
    store = FileStore(tmp_path)
    pending = Task.new(task_id="task-pending", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    pending.status = TaskStatus.PENDING
    review = Task.new(task_id="task-review", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    review.status = TaskStatus.HUMAN_REVIEW
    review.modality = "image"
    review.annotation_requirements = {"annotation_types": ["bounding_box"]}
    store.save_task(pending)
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
        "pending",
        "annotating",
        "validating",
        "qc",
        "human_review",
        "accepted",
        "rejected",
    ]
    assert snapshot["columns"][0]["title"] == "Pending"
    assert snapshot["columns"][0]["cards"][0]["task_id"] == "task-pending"
    assert snapshot["columns"][4]["cards"][0]["feedback_count"] == 1
    assert snapshot["columns"][4]["cards"][0]["modality"] == "image"


def test_dashboard_snapshot_filters_tasks_by_project_id(tmp_path):
    store = FileStore(tmp_path)
    alpha = Task.new(task_id="alpha-1", pipeline_id="project-alpha", source_ref={"kind": "jsonl"})
    beta = Task.new(task_id="beta-1", pipeline_id="project-beta", source_ref={"kind": "jsonl"})
    alpha.status = TaskStatus.PENDING
    beta.status = TaskStatus.PENDING
    store.save_task(alpha)
    store.save_task(beta)

    snapshot = build_kanban_snapshot(store, project_id="project-alpha")

    visible_task_ids = [
        card["task_id"]
        for column in snapshot["columns"]
        for card in column["cards"]
    ]
    assert snapshot["project_id"] == "project-alpha"
    assert visible_task_ids == ["alpha-1"]


def test_dashboard_project_summaries_group_tasks_by_pipeline_id(tmp_path):
    store = FileStore(tmp_path)
    alpha_pending = Task.new(task_id="alpha-1", pipeline_id="project-alpha", source_ref={"kind": "jsonl"})
    alpha_accepted = Task.new(task_id="alpha-2", pipeline_id="project-alpha", source_ref={"kind": "jsonl"})
    alpha_pending.status = TaskStatus.PENDING
    alpha_accepted.status = TaskStatus.ACCEPTED
    beta = Task.new(task_id="beta-1", pipeline_id="project-beta", source_ref={"kind": "jsonl"})
    beta.status = TaskStatus.PENDING
    store.save_task(alpha_pending)
    store.save_task(alpha_accepted)
    store.save_task(beta)

    snapshot = build_project_summaries(store)

    assert snapshot["projects"] == [
        {
            "project_id": "project-alpha",
            "task_count": 2,
            "status_counts": {"accepted": 1, "pending": 1},
        },
        {
            "project_id": "project-beta",
            "task_count": 1,
            "status_counts": {"pending": 1},
        },
    ]
