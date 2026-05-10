from datetime import datetime, timedelta, timezone

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.runtime import ActiveRun, RuntimeConfig
from annotation_pipeline_skill.core.runtime import RuntimeLease
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_runtime_snapshot_counts_queues_capacity_projects_and_due_retries(tmp_path):
    store = SqliteStore.open(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    pending = Task.new(task_id="alpha-1", pipeline_id="alpha", source_ref={"kind": "jsonl"})
    pending.status = TaskStatus.PENDING
    retry = Task.new(task_id="beta-1", pipeline_id="beta", source_ref={"kind": "jsonl"})
    retry.status = TaskStatus.ANNOTATING
    retry.next_retry_at = now - timedelta(seconds=1)
    accepted = Task.new(task_id="alpha-2", pipeline_id="alpha", source_ref={"kind": "jsonl"})
    accepted.status = TaskStatus.ACCEPTED
    store.save_task(pending)
    store.save_task(retry)
    store.save_task(accepted)
    store.save_active_run(
        ActiveRun(
            run_id="run-1",
            task_id="beta-1",
            stage="annotation",
            attempt_id="attempt-1",
            provider_target="annotation",
            started_at=now,
            heartbeat_at=now,
        )
    )
    store.save_runtime_heartbeat(now)

    snapshot = build_runtime_snapshot(
        store,
        RuntimeConfig(max_concurrent_tasks=4, max_starts_per_cycle=2),
        now=now,
    )

    assert snapshot.runtime_status.healthy is True
    assert snapshot.queue_counts.pending == 1
    assert snapshot.queue_counts.annotating == 1
    assert snapshot.queue_counts.accepted == 1
    assert snapshot.capacity.active_count == 1
    assert snapshot.capacity.available_slots == 3
    assert snapshot.due_retries == ["beta-1"]
    assert snapshot.project_summaries == [
        {"project_id": "alpha", "status_counts": {"accepted": 1, "pending": 1}, "task_count": 2},
        {"project_id": "beta", "status_counts": {"annotating": 1}, "task_count": 1},
    ]


def test_runtime_snapshot_marks_missing_heartbeat_unhealthy(tmp_path):
    store = SqliteStore.open(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)

    snapshot = build_runtime_snapshot(store, RuntimeConfig(), now=now)

    assert snapshot.runtime_status.healthy is False
    assert "heartbeat_missing" in snapshot.runtime_status.errors


def test_runtime_snapshot_surfaces_draft_blocked_and_cancelled_counts(tmp_path):
    store = SqliteStore.open(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    store.save_runtime_heartbeat(now)
    statuses = [
        ("draft-1", TaskStatus.DRAFT),
        ("blocked-1", TaskStatus.BLOCKED),
        ("cancelled-1", TaskStatus.CANCELLED),
    ]
    for task_id, status in statuses:
        task = Task.new(task_id=task_id, pipeline_id="alpha", source_ref={"kind": "jsonl"})
        task.status = status
        store.save_task(task)

    snapshot = build_runtime_snapshot(store, RuntimeConfig(), now=now)

    assert snapshot.queue_counts.draft == 1
    assert snapshot.queue_counts.blocked == 1
    assert snapshot.queue_counts.cancelled == 1


def test_runtime_snapshot_detects_stale_active_runs(tmp_path):
    store = SqliteStore.open(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    stale_at = now - timedelta(seconds=601)
    store.save_runtime_heartbeat(now)
    store.save_active_run(
        ActiveRun(
            run_id="run-stale",
            task_id="task-stale",
            stage="annotation",
            attempt_id="attempt-1",
            provider_target="annotation",
            started_at=stale_at,
            heartbeat_at=stale_at,
        )
    )

    snapshot = build_runtime_snapshot(
        store,
        RuntimeConfig(stale_after_seconds=600),
        now=now,
    )

    assert snapshot.runtime_status.healthy is False
    assert snapshot.stale_tasks == ["task-stale"]
    assert "stale_active_runs" in snapshot.runtime_status.errors


def test_runtime_snapshot_counts_leases_as_capacity_truth(tmp_path):
    store = SqliteStore.open(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    store.save_runtime_heartbeat(now)
    task = Task.new(task_id="task-1", pipeline_id="alpha", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    store.save_runtime_lease(
        RuntimeLease(
            lease_id="lease-1",
            task_id="task-1",
            stage="annotation",
            acquired_at=now,
            heartbeat_at=now,
            expires_at=now + timedelta(seconds=600),
            owner="test",
        )
    )

    snapshot = build_runtime_snapshot(store, RuntimeConfig(max_concurrent_tasks=4), now=now)

    assert snapshot.capacity.active_count == 1
    assert snapshot.capacity.available_slots == 3
    assert snapshot.leases[0].lease_id == "lease-1"


def test_runtime_snapshot_detects_stale_leases(tmp_path):
    store = SqliteStore.open(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    store.save_runtime_heartbeat(now)
    store.save_runtime_lease(
        RuntimeLease(
            lease_id="lease-stale",
            task_id="task-1",
            stage="annotation",
            acquired_at=now - timedelta(seconds=700),
            heartbeat_at=now - timedelta(seconds=700),
            expires_at=now - timedelta(seconds=100),
            owner="test",
        )
    )

    snapshot = build_runtime_snapshot(store, RuntimeConfig(), now=now)

    assert snapshot.runtime_status.healthy is False
    assert snapshot.stale_leases == ["lease-stale"]
    assert "stale_runtime_leases" in snapshot.runtime_status.errors
