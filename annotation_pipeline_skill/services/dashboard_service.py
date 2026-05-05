from datetime import datetime, timezone

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.store.file_store import FileStore


KANBAN_COLUMNS: list[tuple[str, str, TaskStatus]] = [
    ("pending", "Pending", TaskStatus.PENDING),
    ("annotating", "Annotating", TaskStatus.ANNOTATING),
    ("validating", "Validating", TaskStatus.VALIDATING),
    ("qc", "QC", TaskStatus.QC),
    ("human_review", "Human Review", TaskStatus.HUMAN_REVIEW),
    ("accepted", "Accepted", TaskStatus.ACCEPTED),
    ("rejected", "Rejected", TaskStatus.REJECTED),
]


def build_kanban_snapshot(store: FileStore, project_id: str | None = None) -> dict:
    tasks = sorted(store.list_tasks(), key=lambda task: task.created_at)
    if project_id is not None:
        tasks = [task for task in tasks if task.pipeline_id == project_id]
    return {
        "project_id": project_id,
        "columns": [
            {
                "id": column_id,
                "title": title,
                "cards": [_task_card(store, task) for task in tasks if task.status is status],
            }
            for column_id, title, status in KANBAN_COLUMNS
        ]
    }


def build_project_summaries(store: FileStore) -> dict:
    summaries: dict[str, dict] = {}
    for task in store.list_tasks():
        summary = summaries.setdefault(
            task.pipeline_id,
            {"project_id": task.pipeline_id, "task_count": 0, "status_counts": {}},
        )
        summary["task_count"] += 1
        status_counts = summary["status_counts"]
        status_counts[task.status.value] = status_counts.get(task.status.value, 0) + 1

    return {
        "projects": [
            {
                "project_id": summary["project_id"],
                "task_count": summary["task_count"],
                "status_counts": dict(sorted(summary["status_counts"].items())),
            }
            for summary in sorted(summaries.values(), key=lambda item: item["project_id"])
        ]
    }


def _task_card(store: FileStore, task: Task) -> dict:
    attempts = store.list_attempts(task.task_id)
    latest_attempt = attempts[-1] if attempts else None
    feedback_count = len(store.list_feedback(task.task_id))
    outbox_records = [record for record in store.list_outbox() if record.task_id == task.task_id]
    annotation_types = task.annotation_requirements.get("annotation_types", [])
    return {
        "task_id": task.task_id,
        "status": task.status.value,
        "modality": task.modality,
        "annotation_types": annotation_types,
        "selected_annotator_id": task.selected_annotator_id,
        "status_age_seconds": int((datetime.now(timezone.utc) - task.updated_at).total_seconds()),
        "latest_attempt_status": latest_attempt.status.value if latest_attempt else None,
        "feedback_count": feedback_count,
        "retry_pending": task.next_retry_at is not None,
        "blocked": task.status is TaskStatus.BLOCKED,
        "external_sync_pending": any(record.status.value == "pending" for record in outbox_records),
    }
