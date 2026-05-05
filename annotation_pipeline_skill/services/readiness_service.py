from __future__ import annotations

from typing import Any

from annotation_pipeline_skill.core.models import ArtifactRef, ExportManifest, Task
from annotation_pipeline_skill.core.states import OutboxStatus, TaskStatus
from annotation_pipeline_skill.services.feedback_service import build_feedback_consensus_summary
from annotation_pipeline_skill.store.file_store import FileStore


def build_readiness_report(store: FileStore, project_id: str) -> dict[str, Any]:
    tasks = [task for task in store.list_tasks() if task.pipeline_id == project_id]
    accepted_tasks = [task for task in tasks if task.status is TaskStatus.ACCEPTED]
    human_review_tasks = [task for task in tasks if task.status is TaskStatus.HUMAN_REVIEW]
    manifests = [manifest for manifest in store.list_export_manifests() if manifest.project_id == project_id]
    exported_task_ids = _exported_task_ids(manifests)
    latest_manifest = _latest_export_manifest(manifests)
    latest_exclusions = _excluded_tasks_by_id(latest_manifest) if latest_manifest else {}
    validation_blockers: list[dict[str, Any]] = []
    exportable_task_ids = []

    for task in accepted_tasks:
        if task.task_id in exported_task_ids:
            continue
        if task.task_id in latest_exclusions:
            validation_blockers.append(latest_exclusions[task.task_id])
            continue
        artifact = _latest_annotation_artifact(store, task)
        if artifact is None:
            validation_blockers.append({"task_id": task.task_id, "reason": "missing_annotation_result"})
            continue
        if not (store.root / artifact.path).exists():
            validation_blockers.append({"task_id": task.task_id, "reason": "missing_annotation_payload"})
            continue
        exportable_task_ids.append(task.task_id)

    open_feedback = [
        feedback_id
        for task in tasks
        for feedback_id in build_feedback_consensus_summary(store, task.task_id)["open_feedback"]
    ]
    pending_outbox_count = sum(
        1
        for record in store.list_outbox()
        if record.task_id in {task.task_id for task in tasks} and record.status is OutboxStatus.PENDING
    )
    dead_letter_outbox_count = sum(
        1
        for record in store.list_outbox()
        if record.task_id in {task.task_id for task in tasks} and record.status is OutboxStatus.DEAD_LETTER
    )
    latest_export = _latest_export(manifests)
    ready_for_training = (
        bool(accepted_tasks)
        and len(exported_task_ids) >= len(accepted_tasks)
        and not human_review_tasks
        and not open_feedback
        and not validation_blockers
        and pending_outbox_count == 0
        and dead_letter_outbox_count == 0
    )

    recommended_next_action = _recommended_next_action(
        accepted_count=len(accepted_tasks),
        exportable_count=len(exportable_task_ids),
        validation_blockers=validation_blockers,
        human_review_count=len(human_review_tasks),
        open_feedback_count=len(open_feedback),
        pending_outbox_count=pending_outbox_count,
        dead_letter_outbox_count=dead_letter_outbox_count,
        ready_for_training=ready_for_training,
    )

    return {
        "project_id": project_id,
        "ready_for_training": ready_for_training,
        "accepted_count": len(accepted_tasks),
        "exported_count": len(exported_task_ids),
        "exportable_count": len(exportable_task_ids),
        "open_feedback_count": len(open_feedback),
        "human_review_count": len(human_review_tasks),
        "validation_blockers": validation_blockers,
        "pending_outbox_count": pending_outbox_count,
        "dead_letter_outbox_count": dead_letter_outbox_count,
        "latest_export": latest_export,
        "recommended_next_action": recommended_next_action,
        "next_command": _next_command(project_id, recommended_next_action),
    }


def _latest_annotation_artifact(store: FileStore, task: Task) -> ArtifactRef | None:
    artifacts = [artifact for artifact in store.list_artifacts(task.task_id) if artifact.kind == "annotation_result"]
    if not artifacts:
        return None
    return artifacts[-1]


def _exported_task_ids(manifests: list[ExportManifest]) -> set[str]:
    task_ids: set[str] = set()
    for manifest in manifests:
        task_ids.update(manifest.task_ids_included)
    return task_ids


def _latest_export_manifest(manifests: list[ExportManifest]) -> ExportManifest | None:
    if not manifests:
        return None
    return sorted(manifests, key=lambda item: item.created_at)[-1]


def _excluded_tasks_by_id(manifest: ExportManifest) -> dict[str, dict[str, Any]]:
    return {
        str(item["task_id"]): item
        for item in manifest.task_ids_excluded
    }


def _latest_export(manifests: list[ExportManifest]) -> dict[str, Any] | None:
    manifest = _latest_export_manifest(manifests)
    if manifest is None:
        return None
    return {
        "export_id": manifest.export_id,
        "created_at": manifest.created_at.isoformat(),
        "output_paths": manifest.output_paths,
        "included": len(manifest.task_ids_included),
        "excluded": len(manifest.task_ids_excluded),
    }


def _recommended_next_action(
    *,
    accepted_count: int,
    exportable_count: int,
    validation_blockers: list[dict[str, Any]],
    human_review_count: int,
    open_feedback_count: int,
    pending_outbox_count: int,
    dead_letter_outbox_count: int,
    ready_for_training: bool,
) -> str:
    if human_review_count:
        return "complete_human_review"
    if open_feedback_count:
        return "resolve_feedback"
    if validation_blockers:
        return "repair_export_blockers"
    if accepted_count == 0:
        return "run_annotation_runtime"
    if exportable_count:
        return "export_training_data"
    if pending_outbox_count:
        return "drain_external_outbox"
    if dead_letter_outbox_count:
        return "inspect_dead_letter_outbox"
    if ready_for_training:
        return "deliver_training_data"
    return "inspect_project_state"


def _next_command(project_id: str, action: str) -> str | None:
    if action == "export_training_data":
        return f"annotation-pipeline export training-data --project-id {project_id}"
    if action == "run_annotation_runtime":
        return "annotation-pipeline runtime once"
    return None
