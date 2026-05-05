from __future__ import annotations

from annotation_pipeline_skill.core.runtime import RuntimeSnapshot


def validate_runtime_snapshot(snapshot: RuntimeSnapshot) -> dict:
    failures: list[str] = []
    details: dict[str, dict] = {}

    if not snapshot.runtime_status.healthy:
        failures.append("runtime_unhealthy")
        details["runtime_unhealthy"] = {
            "errors": snapshot.runtime_status.errors,
            "heartbeat_age_seconds": snapshot.runtime_status.heartbeat_age_seconds,
            "active": snapshot.runtime_status.active,
        }
    if snapshot.stale_tasks:
        failures.append("stale_active_tasks")
        details["stale_active_tasks"] = {"task_ids": snapshot.stale_tasks}
    if snapshot.capacity.active_count > snapshot.capacity.max_concurrent_tasks:
        failures.append("capacity_exceeded")
        details["capacity_exceeded"] = {
            "active_count": snapshot.capacity.active_count,
            "max_concurrent_tasks": snapshot.capacity.max_concurrent_tasks,
        }
    if snapshot.due_retries and snapshot.capacity.available_slots > 0 and snapshot.capacity.active_count == 0:
        failures.append("due_retries_waiting")
        details["due_retries_waiting"] = {
            "task_ids": snapshot.due_retries,
            "available_slots": snapshot.capacity.available_slots,
            "active_count": snapshot.capacity.active_count,
        }
    if snapshot.queue_counts.pending > 0 and snapshot.capacity.available_slots > 0 and snapshot.capacity.active_count == 0:
        failures.append("runnable_backlog_waiting")
        details["runnable_backlog_waiting"] = {
            "pending": snapshot.queue_counts.pending,
            "available_slots": snapshot.capacity.available_slots,
            "active_count": snapshot.capacity.active_count,
        }

    return {"ok": not failures, "failures": failures, "details": details}
