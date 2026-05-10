from datetime import datetime, timezone

import pytest

from annotation_pipeline_skill.core.runtime import (
    ActiveRun,
    CapacitySnapshot,
    QueueCounts,
    RuntimeCycleStats,
    RuntimeSnapshot,
    RuntimeStatus,
)
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_file_store_saves_loads_and_deletes_active_runs(tmp_path):
    store = SqliteStore.open(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    run = ActiveRun(
        run_id="run-1",
        task_id="task-1",
        stage="annotation",
        attempt_id="attempt-1",
        provider_target="annotation",
        started_at=now,
        heartbeat_at=now,
    )

    store.save_active_run(run)

    assert store.list_active_runs() == [run]

    store.delete_active_run("run-1")

    assert store.list_active_runs() == []


def test_file_store_saves_heartbeat_cycle_stats_and_snapshot(tmp_path):
    store = SqliteStore.open(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    stats = RuntimeCycleStats(
        cycle_id="cycle-1",
        started_at=now,
        finished_at=now,
        started=1,
        accepted=1,
        failed=0,
        capacity_available=4,
        errors=[],
    )
    snapshot = RuntimeSnapshot(
        generated_at=now,
        runtime_status=RuntimeStatus(healthy=True, heartbeat_at=now, heartbeat_age_seconds=0, active=True),
        queue_counts=QueueCounts(pending=0, annotating=0, validating=0, qc=0, human_review=0, accepted=1, rejected=0),
        active_runs=[],
        capacity=CapacitySnapshot(max_concurrent_tasks=4, max_starts_per_cycle=2, active_count=0, available_slots=4),
        stale_tasks=[],
        due_retries=[],
        project_summaries=[],
        cycle_stats=[stats],
    )

    store.save_runtime_heartbeat(now)
    store.append_runtime_cycle_stats(stats)
    store.save_runtime_snapshot(snapshot)

    assert store.load_runtime_heartbeat() == now
    assert store.list_runtime_cycle_stats() == [stats]
    assert store.load_runtime_snapshot() == snapshot


def test_file_store_raises_for_malformed_heartbeat_file(tmp_path):
    store = SqliteStore.open(tmp_path)
    heartbeat_path = store.root / "runtime" / "heartbeat.json"
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.write_text("{}", encoding="utf-8")

    with pytest.raises(KeyError):
        store.load_runtime_heartbeat()
