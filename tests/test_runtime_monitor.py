from __future__ import annotations

from datetime import datetime, timezone

from annotation_pipeline_skill.core.runtime import CapacitySnapshot, QueueCounts, RuntimeSnapshot, RuntimeStatus
from annotation_pipeline_skill.runtime.monitor import validate_runtime_snapshot


def runtime_snapshot(
    *,
    healthy: bool = True,
    errors: list[str] | None = None,
    heartbeat_age_seconds: int | None = 0,
    active: bool | None = None,
    pending: int = 0,
    active_count: int = 0,
    max_concurrent_tasks: int = 4,
    available_slots: int = 4,
    stale_tasks: list[str] | None = None,
    due_retries: list[str] | None = None,
) -> RuntimeSnapshot:
    now = datetime.now(timezone.utc)
    return RuntimeSnapshot(
        generated_at=now,
        runtime_status=RuntimeStatus(
            healthy=healthy,
            heartbeat_at=now,
            heartbeat_age_seconds=heartbeat_age_seconds,
            active=healthy if active is None else active,
            errors=errors or [],
        ),
        queue_counts=QueueCounts(
            draft=0,
            pending=pending,
            annotating=0,
            validating=0,
            qc=0,
            human_review=0,
            accepted=0,
            rejected=0,
            blocked=0,
            cancelled=0,
        ),
        active_runs=[],
        capacity=CapacitySnapshot(
            max_concurrent_tasks=max_concurrent_tasks,
            active_count=active_count,
            available_slots=available_slots,
        ),
        stale_tasks=stale_tasks or [],
        due_retries=due_retries or [],
        project_summaries=[],
    )


def test_healthy_snapshot_passes_with_empty_details() -> None:
    result = validate_runtime_snapshot(runtime_snapshot())

    assert result == {"ok": True, "failures": [], "details": {}}


def test_runtime_unhealthy_fails_snapshot_validation() -> None:
    result = validate_runtime_snapshot(
        runtime_snapshot(healthy=False, errors=["heartbeat missing"], heartbeat_age_seconds=120, active=False)
    )

    assert result["ok"] is False
    assert result["failures"] == ["runtime_unhealthy"]
    assert result["details"]["runtime_unhealthy"]["errors"] == ["heartbeat missing"]
    assert result["details"]["runtime_unhealthy"]["heartbeat_age_seconds"] == 120
    assert result["details"]["runtime_unhealthy"]["active"] is False


def test_stale_tasks_fail_snapshot_validation() -> None:
    result = validate_runtime_snapshot(runtime_snapshot(stale_tasks=["task_a"]))

    assert result["ok"] is False
    assert result["failures"] == ["stale_active_tasks"]
    assert result["details"]["stale_active_tasks"]["task_ids"] == ["task_a"]


def test_due_retries_waiting_with_available_capacity_and_no_active_work_fails() -> None:
    result = validate_runtime_snapshot(
        runtime_snapshot(due_retries=["task_a"], active_count=0, available_slots=2)
    )

    assert result["ok"] is False
    assert result["failures"] == ["due_retries_waiting"]
    assert result["details"]["due_retries_waiting"]["task_ids"] == ["task_a"]
    assert result["details"]["due_retries_waiting"]["available_slots"] == 2
    assert result["details"]["due_retries_waiting"]["active_count"] == 0


def test_capacity_exceeded_fails_snapshot_validation() -> None:
    result = validate_runtime_snapshot(runtime_snapshot(active_count=5, max_concurrent_tasks=4))

    assert result["ok"] is False
    assert result["failures"] == ["capacity_exceeded"]
    assert result["details"]["capacity_exceeded"]["active_count"] == 5
    assert result["details"]["capacity_exceeded"]["max_concurrent_tasks"] == 4


def test_runnable_backlog_waiting_with_available_capacity_and_no_active_work_fails() -> None:
    result = validate_runtime_snapshot(runtime_snapshot(pending=3, active_count=0, available_slots=1))

    assert result["ok"] is False
    assert result["failures"] == ["runnable_backlog_waiting"]
    assert result["details"]["runnable_backlog_waiting"]["pending"] == 3
    assert result["details"]["runnable_backlog_waiting"]["available_slots"] == 1
    assert result["details"]["runnable_backlog_waiting"]["active_count"] == 0
