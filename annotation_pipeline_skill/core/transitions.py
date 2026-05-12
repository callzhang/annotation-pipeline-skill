from annotation_pipeline_skill.core.models import AuditEvent, Task, utc_now
from annotation_pipeline_skill.core.states import TaskStatus


class InvalidTransition(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.DRAFT: {TaskStatus.PENDING, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.PENDING: {TaskStatus.ANNOTATING, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.ANNOTATING: {TaskStatus.VALIDATING, TaskStatus.HUMAN_REVIEW, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.VALIDATING: {
        TaskStatus.PENDING,
        TaskStatus.QC,
        TaskStatus.ANNOTATING,
        TaskStatus.HUMAN_REVIEW,
        TaskStatus.REJECTED,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.QC: {
        TaskStatus.PENDING,
        TaskStatus.ACCEPTED,
        TaskStatus.HUMAN_REVIEW,
        TaskStatus.ANNOTATING,
        TaskStatus.REJECTED,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.HUMAN_REVIEW: {
        TaskStatus.ACCEPTED,
        TaskStatus.ANNOTATING,
        TaskStatus.REJECTED,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.ACCEPTED: set(),
    TaskStatus.REJECTED: set(),
    TaskStatus.BLOCKED: {TaskStatus.PENDING},
    TaskStatus.CANCELLED: set(),
}


def transition_task(
    task: Task,
    next_status: TaskStatus,
    actor: str,
    reason: str,
    stage: str,
    attempt_id: str | None = None,
    metadata: dict | None = None,
) -> AuditEvent:
    previous_status = task.status
    if next_status not in ALLOWED_TRANSITIONS[previous_status]:
        raise InvalidTransition(f"cannot transition task {task.task_id} from {previous_status.value} to {next_status.value}")

    task.status = next_status
    task.updated_at = utc_now()
    return AuditEvent.new(
        task_id=task.task_id,
        previous_status=previous_status,
        next_status=next_status,
        actor=actor,
        reason=reason,
        stage=stage,
        attempt_id=attempt_id,
        metadata=metadata,
    )
