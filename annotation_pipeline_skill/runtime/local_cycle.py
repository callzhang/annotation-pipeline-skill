from dataclasses import dataclass

from annotation_pipeline_skill.config.models import ProjectConfig
from annotation_pipeline_skill.core.models import ArtifactRef, Attempt, Task
from annotation_pipeline_skill.core.states import AttemptStatus, TaskStatus
from annotation_pipeline_skill.core.transitions import transition_task
from annotation_pipeline_skill.store.file_store import FileStore


@dataclass(frozen=True)
class LocalCycleResult:
    started: int
    accepted: int
    human_review: int


def run_local_cycle(store: FileStore, config: ProjectConfig, limit: int | None = None) -> LocalCycleResult:
    ready_tasks = [task for task in store.list_tasks() if task.status is TaskStatus.READY]
    if limit is not None:
        ready_tasks = ready_tasks[:limit]

    accepted = 0
    human_review = 0
    for task in ready_tasks:
        _run_task(store, task, config)
        if task.status is TaskStatus.ACCEPTED:
            accepted += 1
        if task.status is TaskStatus.HUMAN_REVIEW:
            human_review += 1

    return LocalCycleResult(started=len(ready_tasks), accepted=accepted, human_review=human_review)


def _run_task(store: FileStore, task: Task, config: ProjectConfig) -> None:
    for next_status, stage, reason in (
        (TaskStatus.ANNOTATING, "annotation", "local cycle started annotation"),
        (TaskStatus.VALIDATING, "validation", "local cycle produced annotation result"),
        (TaskStatus.QC, "qc", "local cycle validation passed"),
    ):
        event = transition_task(task, next_status, actor="local-cycle", reason=reason, stage=stage)
        store.append_event(event)

    attempt = Attempt(
        attempt_id=f"{task.task_id}-attempt-{task.current_attempt + 1}",
        task_id=task.task_id,
        index=task.current_attempt + 1,
        stage="annotation",
        status=AttemptStatus.SUCCEEDED,
        summary="local fake annotation completed",
    )
    task.current_attempt += 1
    artifact = ArtifactRef.new(
        task_id=task.task_id,
        kind="annotation_result",
        path=f"artifacts/{task.task_id}/annotation_result.json",
        content_type="application/json",
        metadata={"runtime": "local_cycle"},
    )
    store.append_attempt(attempt)
    store.append_artifact(artifact)

    final_status = TaskStatus.HUMAN_REVIEW if config.human_review_required else TaskStatus.ACCEPTED
    event = transition_task(
        task,
        final_status,
        actor="local-cycle",
        reason="qc passed and routed by human review policy",
        stage="qc",
    )
    store.append_event(event)
    store.save_task(task)
