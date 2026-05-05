from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from annotation_pipeline_skill.core.states import (
    AttemptStatus,
    FeedbackSeverity,
    FeedbackSource,
    OutboxKind,
    OutboxStatus,
    TaskStatus,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt_from_str(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass
class ExternalTaskRef:
    system_id: str
    external_task_id: str
    source_url: str | None
    idempotency_key: str
    last_status_posted: str | None = None
    last_status_posted_at: datetime | None = None
    submit_attempts: int = 0

    def to_dict(self) -> dict:
        return {
            "system_id": self.system_id,
            "external_task_id": self.external_task_id,
            "source_url": self.source_url,
            "idempotency_key": self.idempotency_key,
            "last_status_posted": self.last_status_posted,
            "last_status_posted_at": _dt_to_str(self.last_status_posted_at),
            "submit_attempts": self.submit_attempts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExternalTaskRef:
        return cls(
            system_id=data["system_id"],
            external_task_id=data["external_task_id"],
            source_url=data.get("source_url"),
            idempotency_key=data["idempotency_key"],
            last_status_posted=data.get("last_status_posted"),
            last_status_posted_at=_dt_from_str(data.get("last_status_posted_at")),
            submit_attempts=data.get("submit_attempts", 0),
        )


@dataclass
class ArtifactRef:
    artifact_id: str
    task_id: str
    kind: str
    path: str
    content_type: str
    created_at: datetime
    metadata: dict = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        task_id: str,
        kind: str,
        path: str,
        content_type: str,
        metadata: dict | None = None,
    ) -> ArtifactRef:
        return cls(
            artifact_id=f"artifact-{uuid4().hex}",
            task_id=task_id,
            kind=kind,
            path=path,
            content_type=content_type,
            created_at=utc_now(),
            metadata=metadata or {},
        )

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "task_id": self.task_id,
            "kind": self.kind,
            "path": self.path,
            "content_type": self.content_type,
            "created_at": _dt_to_str(self.created_at),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ArtifactRef:
        return cls(
            artifact_id=data["artifact_id"],
            task_id=data["task_id"],
            kind=data["kind"],
            path=data["path"],
            content_type=data["content_type"],
            created_at=_dt_from_str(data["created_at"]),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Attempt:
    attempt_id: str
    task_id: str
    index: int
    stage: str
    status: AttemptStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    provider_id: str | None = None
    model: str | None = None
    effort: str | None = None
    route_role: str | None = None
    summary: str | None = None
    error: dict | None = None
    artifacts: list[ArtifactRef] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "index": self.index,
            "stage": self.stage,
            "status": self.status.value,
            "started_at": _dt_to_str(self.started_at),
            "finished_at": _dt_to_str(self.finished_at),
            "provider_id": self.provider_id,
            "model": self.model,
            "effort": self.effort,
            "route_role": self.route_role,
            "summary": self.summary,
            "error": self.error,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Attempt:
        return cls(
            attempt_id=data["attempt_id"],
            task_id=data["task_id"],
            index=data["index"],
            stage=data["stage"],
            status=AttemptStatus(data["status"]),
            started_at=_dt_from_str(data.get("started_at")),
            finished_at=_dt_from_str(data.get("finished_at")),
            provider_id=data.get("provider_id"),
            model=data.get("model"),
            effort=data.get("effort"),
            route_role=data.get("route_role"),
            summary=data.get("summary"),
            error=data.get("error"),
            artifacts=[ArtifactRef.from_dict(item) for item in data.get("artifacts", [])],
        )


@dataclass
class Task:
    task_id: str
    pipeline_id: str
    source_ref: dict
    external_ref: ExternalTaskRef | None
    modality: str
    annotation_requirements: dict
    selected_annotator_id: str | None
    status: TaskStatus
    current_attempt: int
    created_at: datetime
    updated_at: datetime
    active_run_id: str | None = None
    next_retry_at: datetime | None = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        task_id: str,
        pipeline_id: str,
        source_ref: dict,
        external_ref: ExternalTaskRef | None = None,
        modality: str = "text",
        annotation_requirements: dict | None = None,
        selected_annotator_id: str | None = None,
        metadata: dict | None = None,
    ) -> Task:
        now = utc_now()
        return cls(
            task_id=task_id,
            pipeline_id=pipeline_id,
            source_ref=source_ref,
            external_ref=external_ref,
            modality=modality,
            annotation_requirements=annotation_requirements or {},
            selected_annotator_id=selected_annotator_id,
            status=TaskStatus.DRAFT,
            current_attempt=0,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "pipeline_id": self.pipeline_id,
            "source_ref": self.source_ref,
            "external_ref": self.external_ref.to_dict() if self.external_ref else None,
            "modality": self.modality,
            "annotation_requirements": self.annotation_requirements,
            "selected_annotator_id": self.selected_annotator_id,
            "status": self.status.value,
            "current_attempt": self.current_attempt,
            "created_at": _dt_to_str(self.created_at),
            "updated_at": _dt_to_str(self.updated_at),
            "active_run_id": self.active_run_id,
            "next_retry_at": _dt_to_str(self.next_retry_at),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        external_data = data.get("external_ref")
        return cls(
            task_id=data["task_id"],
            pipeline_id=data["pipeline_id"],
            source_ref=data["source_ref"],
            external_ref=ExternalTaskRef.from_dict(external_data) if external_data else None,
            modality=data.get("modality", "text"),
            annotation_requirements=data.get("annotation_requirements", {}),
            selected_annotator_id=data.get("selected_annotator_id"),
            status=TaskStatus(data["status"]),
            current_attempt=data.get("current_attempt", 0),
            created_at=_dt_from_str(data["created_at"]),
            updated_at=_dt_from_str(data["updated_at"]),
            active_run_id=data.get("active_run_id"),
            next_retry_at=_dt_from_str(data.get("next_retry_at")),
            metadata=data.get("metadata", {}),
        )


@dataclass
class AuditEvent:
    event_id: str
    task_id: str
    previous_status: TaskStatus
    next_status: TaskStatus
    actor: str
    reason: str
    stage: str
    created_at: datetime
    attempt_id: str | None = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        task_id: str,
        previous_status: TaskStatus,
        next_status: TaskStatus,
        actor: str,
        reason: str,
        stage: str,
        attempt_id: str | None = None,
        metadata: dict | None = None,
    ) -> AuditEvent:
        return cls(
            event_id=f"event-{uuid4().hex}",
            task_id=task_id,
            previous_status=previous_status,
            next_status=next_status,
            actor=actor,
            reason=reason,
            stage=stage,
            attempt_id=attempt_id,
            created_at=utc_now(),
            metadata=metadata or {},
        )

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "previous_status": self.previous_status.value,
            "next_status": self.next_status.value,
            "actor": self.actor,
            "reason": self.reason,
            "stage": self.stage,
            "attempt_id": self.attempt_id,
            "created_at": _dt_to_str(self.created_at),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AuditEvent:
        return cls(
            event_id=data["event_id"],
            task_id=data["task_id"],
            previous_status=TaskStatus(data["previous_status"]),
            next_status=TaskStatus(data["next_status"]),
            actor=data["actor"],
            reason=data["reason"],
            stage=data["stage"],
            attempt_id=data.get("attempt_id"),
            created_at=_dt_from_str(data["created_at"]),
            metadata=data.get("metadata", {}),
        )


@dataclass
class FeedbackRecord:
    feedback_id: str
    task_id: str
    attempt_id: str
    source_stage: FeedbackSource
    severity: FeedbackSeverity
    category: str
    message: str
    target: dict
    suggested_action: str
    created_at: datetime
    created_by: str
    metadata: dict = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        task_id: str,
        attempt_id: str,
        source_stage: FeedbackSource,
        severity: FeedbackSeverity,
        category: str,
        message: str,
        target: dict,
        suggested_action: str,
        created_by: str,
        metadata: dict | None = None,
    ) -> FeedbackRecord:
        return cls(
            feedback_id=f"feedback-{uuid4().hex}",
            task_id=task_id,
            attempt_id=attempt_id,
            source_stage=source_stage,
            severity=severity,
            category=category,
            message=message,
            target=target,
            suggested_action=suggested_action,
            created_at=utc_now(),
            created_by=created_by,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict:
        return {
            "feedback_id": self.feedback_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "source_stage": self.source_stage.value,
            "severity": self.severity.value,
            "category": self.category,
            "message": self.message,
            "target": self.target,
            "suggested_action": self.suggested_action,
            "created_at": _dt_to_str(self.created_at),
            "created_by": self.created_by,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FeedbackRecord:
        return cls(
            feedback_id=data["feedback_id"],
            task_id=data["task_id"],
            attempt_id=data["attempt_id"],
            source_stage=FeedbackSource(data["source_stage"]),
            severity=FeedbackSeverity(data["severity"]),
            category=data["category"],
            message=data["message"],
            target=data["target"],
            suggested_action=data["suggested_action"],
            created_at=_dt_from_str(data["created_at"]),
            created_by=data["created_by"],
            metadata=data.get("metadata", {}),
        )


@dataclass
class FeedbackDiscussionEntry:
    entry_id: str
    task_id: str
    feedback_id: str
    role: str
    stance: str
    message: str
    agreed_points: list[str]
    disputed_points: list[str]
    proposed_resolution: str | None
    consensus: bool
    created_at: datetime
    created_by: str
    metadata: dict = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        task_id: str,
        feedback_id: str,
        role: str,
        stance: str,
        message: str,
        created_by: str,
        agreed_points: list[str] | None = None,
        disputed_points: list[str] | None = None,
        proposed_resolution: str | None = None,
        consensus: bool = False,
        metadata: dict | None = None,
    ) -> FeedbackDiscussionEntry:
        return cls(
            entry_id=f"discussion-{uuid4().hex}",
            task_id=task_id,
            feedback_id=feedback_id,
            role=role,
            stance=stance,
            message=message,
            agreed_points=agreed_points or [],
            disputed_points=disputed_points or [],
            proposed_resolution=proposed_resolution,
            consensus=consensus,
            created_at=utc_now(),
            created_by=created_by,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "task_id": self.task_id,
            "feedback_id": self.feedback_id,
            "role": self.role,
            "stance": self.stance,
            "message": self.message,
            "agreed_points": self.agreed_points,
            "disputed_points": self.disputed_points,
            "proposed_resolution": self.proposed_resolution,
            "consensus": self.consensus,
            "created_at": _dt_to_str(self.created_at),
            "created_by": self.created_by,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FeedbackDiscussionEntry:
        return cls(
            entry_id=data["entry_id"],
            task_id=data["task_id"],
            feedback_id=data["feedback_id"],
            role=data["role"],
            stance=data["stance"],
            message=data["message"],
            agreed_points=list(data.get("agreed_points", [])),
            disputed_points=list(data.get("disputed_points", [])),
            proposed_resolution=data.get("proposed_resolution"),
            consensus=bool(data.get("consensus", False)),
            created_at=_dt_from_str(data["created_at"]),
            created_by=data["created_by"],
            metadata=data.get("metadata", {}),
        )


@dataclass
class OutboxRecord:
    record_id: str
    task_id: str
    kind: OutboxKind
    payload: dict
    status: OutboxStatus
    retry_count: int
    created_at: datetime
    next_retry_at: datetime | None = None
    last_error: str | None = None

    @classmethod
    def new(cls, task_id: str, kind: OutboxKind, payload: dict) -> OutboxRecord:
        return cls(
            record_id=f"outbox-{uuid4().hex}",
            task_id=task_id,
            kind=kind,
            payload=payload,
            status=OutboxStatus.PENDING,
            retry_count=0,
            created_at=utc_now(),
        )

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "task_id": self.task_id,
            "kind": self.kind.value,
            "payload": self.payload,
            "status": self.status.value,
            "retry_count": self.retry_count,
            "created_at": _dt_to_str(self.created_at),
            "next_retry_at": _dt_to_str(self.next_retry_at),
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> OutboxRecord:
        return cls(
            record_id=data["record_id"],
            task_id=data["task_id"],
            kind=OutboxKind(data["kind"]),
            payload=data["payload"],
            status=OutboxStatus(data["status"]),
            retry_count=data["retry_count"],
            created_at=_dt_from_str(data["created_at"]),
            next_retry_at=_dt_from_str(data.get("next_retry_at")),
            last_error=data.get("last_error"),
        )
