from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import uuid4

from annotation_pipeline_skill.core.models import utc_now
from annotation_pipeline_skill.core.states import FeedbackSeverity, OutboxStatus, TaskStatus
from annotation_pipeline_skill.llm.profiles import ProfileValidationError
from annotation_pipeline_skill.services.feedback_service import build_feedback_consensus_summary
from annotation_pipeline_skill.services.provider_config_service import build_provider_config_snapshot
from annotation_pipeline_skill.services.readiness_service import build_readiness_report
from annotation_pipeline_skill.store.file_store import FileStore


RULE_UPDATE_KIND = "rule_updates"
LONG_TAIL_KIND = "long_tail_issues"


class CoordinatorService:
    def __init__(self, store: FileStore):
        self.store = store

    def build_report(self, project_id: str | None = None) -> dict[str, Any]:
        tasks = self._project_tasks(project_id)
        task_ids = {task.task_id for task in tasks}
        feedback = [
            item
            for task in tasks
            for item in self.store.list_feedback(task.task_id)
        ]
        open_feedback_ids = [
            feedback_id
            for task in tasks
            for feedback_id in build_feedback_consensus_summary(self.store, task.task_id)["open_feedback"]
        ]
        status_counts = Counter(task.status.value for task in tasks)
        outbox_records = [record for record in self.store.list_outbox() if record.task_id in task_ids]
        readiness = build_readiness_report(self.store, project_id) if project_id else None

        return {
            "project_id": project_id,
            "generated_at": utc_now().isoformat(),
            "task_count": len(tasks),
            "status_counts": dict(sorted(status_counts.items())),
            "human_review_task_ids": sorted(
                task.task_id for task in tasks if task.status is TaskStatus.HUMAN_REVIEW
            ),
            "blocked_task_ids": sorted(
                task.task_id for task in tasks if task.status is TaskStatus.BLOCKED
            ),
            "open_feedback_count": len(open_feedback_ids),
            "open_feedback_ids": sorted(open_feedback_ids),
            "feedback_by_category": dict(sorted(Counter(item.category for item in feedback).items())),
            "blocking_feedback_count": sum(
                1 for item in feedback if item.severity is FeedbackSeverity.BLOCKING
            ),
            "outbox_counts": _outbox_counts(outbox_records),
            "readiness": readiness,
            "provider_diagnostics": self._provider_diagnostics(),
            "rule_updates": self.list_rule_updates(project_id),
            "long_tail_issues": self.list_long_tail_issues(project_id),
            "recommended_actions": self._recommended_actions(
                project_id=project_id,
                human_review_count=sum(1 for task in tasks if task.status is TaskStatus.HUMAN_REVIEW),
                open_feedback_count=len(open_feedback_ids),
                blocked_count=sum(1 for task in tasks if task.status is TaskStatus.BLOCKED),
                pending_outbox_count=sum(1 for record in outbox_records if record.status is OutboxStatus.PENDING),
                readiness=readiness,
            ),
        }

    def record_rule_update(
        self,
        *,
        project_id: str,
        source: str,
        summary: str,
        action: str,
        created_by: str,
        task_ids: list[str] | None = None,
        status: str = "open",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "record_id": f"rule-update-{uuid4().hex}",
            "project_id": _required_text(project_id, "project_id"),
            "source": _required_text(source, "source"),
            "summary": _required_text(summary, "summary"),
            "action": _required_text(action, "action"),
            "status": _required_text(status, "status"),
            "task_ids": sorted(str(task_id) for task_id in (task_ids or [])),
            "created_at": utc_now().isoformat(),
            "created_by": _required_text(created_by, "created_by"),
            "metadata": metadata or {},
        }
        self.store.append_coordination_record(RULE_UPDATE_KIND, record)
        return record

    def record_long_tail_issue(
        self,
        *,
        project_id: str,
        category: str,
        summary: str,
        recommended_action: str,
        created_by: str,
        severity: str = "medium",
        task_ids: list[str] | None = None,
        status: str = "open",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "issue_id": f"long-tail-{uuid4().hex}",
            "project_id": _required_text(project_id, "project_id"),
            "category": _required_text(category, "category"),
            "summary": _required_text(summary, "summary"),
            "recommended_action": _required_text(recommended_action, "recommended_action"),
            "severity": _required_text(severity, "severity"),
            "status": _required_text(status, "status"),
            "task_ids": sorted(str(task_id) for task_id in (task_ids or [])),
            "created_at": utc_now().isoformat(),
            "created_by": _required_text(created_by, "created_by"),
            "metadata": metadata or {},
        }
        self.store.append_coordination_record(LONG_TAIL_KIND, record)
        return record

    def list_rule_updates(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return _filter_project_records(self.store.list_coordination_records(RULE_UPDATE_KIND), project_id)

    def list_long_tail_issues(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return _filter_project_records(self.store.list_coordination_records(LONG_TAIL_KIND), project_id)

    def _project_tasks(self, project_id: str | None):
        tasks = self.store.list_tasks()
        if project_id is None:
            return tasks
        return [task for task in tasks if task.pipeline_id == project_id]

    def _provider_diagnostics(self) -> dict[str, Any]:
        try:
            snapshot = build_provider_config_snapshot(self.store.root)
        except (OSError, ProfileValidationError) as exc:
            return {"config_valid": False, "error": str(exc), "diagnostics": {}}
        return {
            "config_valid": snapshot["config_valid"],
            "targets": snapshot["targets"],
            "diagnostics": snapshot["diagnostics"],
        }

    def _recommended_actions(
        self,
        *,
        project_id: str | None,
        human_review_count: int,
        open_feedback_count: int,
        blocked_count: int,
        pending_outbox_count: int,
        readiness: dict[str, Any] | None,
    ) -> list[str]:
        actions = []
        if human_review_count:
            actions.append("remind_user_to_complete_human_review")
        if open_feedback_count:
            actions.append("resolve_annotator_qc_feedback")
        if blocked_count:
            actions.append("inspect_blocked_tasks")
        if pending_outbox_count:
            actions.append("drain_external_outbox")
        if readiness and readiness["recommended_next_action"] not in {"inspect_project_state", "deliver_training_data"}:
            actions.append(str(readiness["recommended_next_action"]))
        if project_id and not actions:
            actions.append("export_or_deliver_training_data")
        if not actions:
            actions.append("inspect_project_state")
        return actions


def _outbox_counts(records) -> dict[str, int]:
    summary = build_outbox_summary_from_records(records)
    return summary["counts"]


def build_outbox_summary_from_records(records) -> dict[str, Any]:
    counts = {"pending": 0, "sent": 0, "dead_letter": 0}
    for record in records:
        if record.status.value in counts:
            counts[record.status.value] += 1
    return {"counts": counts}


def _filter_project_records(records: list[dict[str, Any]], project_id: str | None) -> list[dict[str, Any]]:
    filtered = records if project_id is None else [record for record in records if record.get("project_id") == project_id]
    return sorted(filtered, key=lambda record: str(record.get("created_at", "")), reverse=True)


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()
