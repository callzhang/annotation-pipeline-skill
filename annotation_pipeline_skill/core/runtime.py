from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt_from_str(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True)
class RuntimeConfig:
    max_concurrent_tasks: int = 4
    max_starts_per_cycle: int = 2
    stale_after_seconds: int = 600
    retry_delay_seconds: int = 3600
    loop_interval_seconds: int = 5

    def to_dict(self) -> dict:
        return {
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "max_starts_per_cycle": self.max_starts_per_cycle,
            "stale_after_seconds": self.stale_after_seconds,
            "retry_delay_seconds": self.retry_delay_seconds,
            "loop_interval_seconds": self.loop_interval_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RuntimeConfig:
        return cls(
            max_concurrent_tasks=data.get("max_concurrent_tasks", 4),
            max_starts_per_cycle=data.get("max_starts_per_cycle", 2),
            stale_after_seconds=data.get("stale_after_seconds", 600),
            retry_delay_seconds=data.get("retry_delay_seconds", 3600),
            loop_interval_seconds=data.get("loop_interval_seconds", 5),
        )


@dataclass(frozen=True)
class ActiveRun:
    run_id: str
    task_id: str
    stage: str
    attempt_id: str
    provider_target: str
    started_at: datetime
    heartbeat_at: datetime
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "stage": self.stage,
            "attempt_id": self.attempt_id,
            "provider_target": self.provider_target,
            "started_at": _dt_to_str(self.started_at),
            "heartbeat_at": _dt_to_str(self.heartbeat_at),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ActiveRun:
        return cls(
            run_id=data["run_id"],
            task_id=data["task_id"],
            stage=data["stage"],
            attempt_id=data["attempt_id"],
            provider_target=data["provider_target"],
            started_at=_dt_from_str(data["started_at"]),
            heartbeat_at=_dt_from_str(data["heartbeat_at"]),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class RuntimeCycleStats:
    cycle_id: str
    started_at: datetime
    finished_at: datetime
    started: int
    accepted: int
    failed: int
    capacity_available: int
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "cycle_id": self.cycle_id,
            "started_at": _dt_to_str(self.started_at),
            "finished_at": _dt_to_str(self.finished_at),
            "started": self.started,
            "accepted": self.accepted,
            "failed": self.failed,
            "capacity_available": self.capacity_available,
            "errors": self.errors,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RuntimeCycleStats:
        return cls(
            cycle_id=data["cycle_id"],
            started_at=_dt_from_str(data["started_at"]),
            finished_at=_dt_from_str(data["finished_at"]),
            started=data["started"],
            accepted=data["accepted"],
            failed=data["failed"],
            capacity_available=data["capacity_available"],
            errors=data.get("errors", []),
        )


@dataclass(frozen=True)
class RuntimeStatus:
    healthy: bool
    heartbeat_at: datetime | None
    heartbeat_age_seconds: int | None
    active: bool
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "heartbeat_at": _dt_to_str(self.heartbeat_at),
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "active": self.active,
            "errors": self.errors,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RuntimeStatus:
        return cls(
            healthy=data["healthy"],
            heartbeat_at=_dt_from_str(data.get("heartbeat_at")),
            heartbeat_age_seconds=data.get("heartbeat_age_seconds"),
            active=data["active"],
            errors=data.get("errors", []),
        )


@dataclass(frozen=True)
class QueueCounts:
    pending: int
    annotating: int
    validating: int
    qc: int
    human_review: int
    accepted: int
    rejected: int
    draft: int = 0
    blocked: int = 0
    cancelled: int = 0

    def to_dict(self) -> dict:
        return {
            "draft": self.draft,
            "pending": self.pending,
            "annotating": self.annotating,
            "validating": self.validating,
            "qc": self.qc,
            "human_review": self.human_review,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "blocked": self.blocked,
            "cancelled": self.cancelled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> QueueCounts:
        return cls(
            draft=data["draft"],
            pending=data["pending"],
            annotating=data["annotating"],
            validating=data["validating"],
            qc=data["qc"],
            human_review=data["human_review"],
            accepted=data["accepted"],
            rejected=data["rejected"],
            blocked=data["blocked"],
            cancelled=data["cancelled"],
        )


@dataclass(frozen=True)
class CapacitySnapshot:
    max_concurrent_tasks: int
    max_starts_per_cycle: int
    active_count: int
    available_slots: int

    def to_dict(self) -> dict:
        return {
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "max_starts_per_cycle": self.max_starts_per_cycle,
            "active_count": self.active_count,
            "available_slots": self.available_slots,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CapacitySnapshot:
        return cls(
            max_concurrent_tasks=data["max_concurrent_tasks"],
            max_starts_per_cycle=data["max_starts_per_cycle"],
            active_count=data["active_count"],
            available_slots=data["available_slots"],
        )


@dataclass(frozen=True)
class RuntimeSnapshot:
    generated_at: datetime
    runtime_status: RuntimeStatus
    queue_counts: QueueCounts
    active_runs: list[ActiveRun]
    capacity: CapacitySnapshot
    stale_tasks: list[str]
    due_retries: list[str]
    project_summaries: list[dict]
    cycle_stats: list[RuntimeCycleStats]

    def to_dict(self) -> dict:
        return {
            "generated_at": _dt_to_str(self.generated_at),
            "runtime_status": self.runtime_status.to_dict(),
            "queue_counts": self.queue_counts.to_dict(),
            "active_runs": [run.to_dict() for run in self.active_runs],
            "capacity": self.capacity.to_dict(),
            "stale_tasks": self.stale_tasks,
            "due_retries": self.due_retries,
            "project_summaries": self.project_summaries,
            "cycle_stats": [stats.to_dict() for stats in self.cycle_stats],
        }

    @classmethod
    def from_dict(cls, data: dict) -> RuntimeSnapshot:
        return cls(
            generated_at=_dt_from_str(data["generated_at"]),
            runtime_status=RuntimeStatus.from_dict(data["runtime_status"]),
            queue_counts=QueueCounts.from_dict(data["queue_counts"]),
            active_runs=[ActiveRun.from_dict(item) for item in data.get("active_runs", [])],
            capacity=CapacitySnapshot.from_dict(data["capacity"]),
            stale_tasks=list(data.get("stale_tasks", [])),
            due_retries=list(data.get("due_retries", [])),
            project_summaries=list(data.get("project_summaries", [])),
            cycle_stats=[RuntimeCycleStats.from_dict(item) for item in data.get("cycle_stats", [])],
        )
