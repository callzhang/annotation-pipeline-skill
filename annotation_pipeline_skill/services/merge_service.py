from __future__ import annotations

from dataclasses import dataclass

from annotation_pipeline_skill.core.models import OutboxRecord, Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.core.transitions import transition_task
from annotation_pipeline_skill.services.external_task_service import ExternalTaskService
from annotation_pipeline_skill.store.file_store import FileStore


@dataclass(frozen=True)
class MergeResult:
    scanned: int
    merged: int


class MergeService:
    def __init__(self, store: FileStore):
        self.store = store
        self.external_tasks = ExternalTaskService(store)

    def merge_accepted(self, limit: int | None = None, actor: str = "merge-service") -> MergeResult:
        tasks = [task for task in self.store.list_tasks() if task.status is TaskStatus.ACCEPTED]
        if limit is not None:
            tasks = tasks[:limit]

        for task in tasks:
            self.merge_task(task, actor=actor)

        return MergeResult(scanned=len(tasks), merged=len(tasks))

    def merge_task(self, task: Task, actor: str = "merge-service") -> OutboxRecord:
        event = transition_task(
            task,
            TaskStatus.MERGED,
            actor=actor,
            reason="accepted annotation submitted to merge sink",
            stage="merge",
            metadata={"outbox_kind": "submit"},
        )
        outbox_record = self.external_tasks.enqueue_submit(task, self._merge_payload(task))
        self.store.append_event(event)
        self.store.save_task(task)
        return outbox_record

    def _merge_payload(self, task: Task) -> dict:
        return {
            "task_id": task.task_id,
            "pipeline_id": task.pipeline_id,
            "status": TaskStatus.MERGED.value,
            "source_ref": task.source_ref,
            "annotation_requirements": task.annotation_requirements,
            "artifacts": [artifact.to_dict() for artifact in self.store.list_artifacts(task.task_id)],
            "attempts": [attempt.to_dict() for attempt in self.store.list_attempts(task.task_id)],
            "metadata": task.metadata,
        }
