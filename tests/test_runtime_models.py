from datetime import datetime, timedelta, timezone

from annotation_pipeline_skill.core.runtime import (
    ActiveRun,
    CapacitySnapshot,
    QueueCounts,
    RuntimeConfig,
    RuntimeSnapshot,
    RuntimeStatus,
)


def test_runtime_config_uses_safe_defaults():
    config = RuntimeConfig()

    assert config.max_concurrent_tasks == 4
    assert config.snapshot_interval_seconds == 30
    assert config.stale_after_seconds == 600
    assert config.retry_delay_seconds == 3600


def test_active_run_round_trips_through_dict():
    started_at = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    run = ActiveRun(
        run_id="run-1",
        task_id="task-1",
        stage="annotation",
        attempt_id="attempt-1",
        provider_target="annotation",
        started_at=started_at,
        heartbeat_at=started_at + timedelta(seconds=3),
        metadata={"pid": 123},
    )

    loaded = ActiveRun.from_dict(run.to_dict())

    assert loaded == run


def test_runtime_snapshot_round_trips_through_dict():
    generated_at = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    snapshot = RuntimeSnapshot(
        generated_at=generated_at,
        runtime_status=RuntimeStatus(
            healthy=True,
            heartbeat_at=generated_at,
            heartbeat_age_seconds=0,
            active=True,
            errors=[],
        ),
        queue_counts=QueueCounts(pending=2, annotating=1, validating=0, qc=0, human_review=0, accepted=3, rejected=0),
        active_runs=[
            ActiveRun(
                run_id="run-1",
                task_id="task-1",
                stage="annotation",
                attempt_id="attempt-1",
                provider_target="annotation",
                started_at=generated_at,
                heartbeat_at=generated_at,
            )
        ],
        capacity=CapacitySnapshot(max_concurrent_tasks=4, active_count=1, available_slots=3),
        stale_tasks=[],
        due_retries=["task-2"],
        project_summaries=[{"project_id": "demo", "task_count": 6}],
    )

    loaded = RuntimeSnapshot.from_dict(snapshot.to_dict())

    assert loaded == snapshot


def test_queue_counts_surfaces_all_task_status_counts():
    counts = QueueCounts(
        draft=1,
        pending=2,
        annotating=3,
        validating=4,
        qc=5,
        human_review=6,
        accepted=7,
        rejected=8,
        blocked=9,
        cancelled=10,
    )

    assert counts.to_dict() == {
        "draft": 1,
        "pending": 2,
        "annotating": 3,
        "validating": 4,
        "qc": 5,
        "human_review": 6,
        "accepted": 7,
        "rejected": 8,
        "blocked": 9,
        "cancelled": 10,
    }
    assert QueueCounts.from_dict(counts.to_dict()) == counts


def test_unhealthy_runtime_status_with_missing_heartbeat_round_trips_through_dict():
    status = RuntimeStatus.from_dict(
        {
            "healthy": False,
            "active": False,
            "errors": ["scheduler runtime heartbeat missing"],
        }
    )

    loaded = RuntimeStatus.from_dict(status.to_dict())

    assert loaded == RuntimeStatus(
        healthy=False,
        heartbeat_at=None,
        heartbeat_age_seconds=None,
        active=False,
        errors=["scheduler runtime heartbeat missing"],
    )


def test_runtime_config_parses_max_qc_rounds():
    from annotation_pipeline_skill.core.runtime import RuntimeConfig
    cfg = RuntimeConfig.from_dict({"max_qc_rounds": 5})
    assert cfg.max_qc_rounds == 5


def test_runtime_config_max_qc_rounds_defaults_to_3():
    from annotation_pipeline_skill.core.runtime import RuntimeConfig
    cfg = RuntimeConfig()
    assert cfg.max_qc_rounds == 3


def test_runtime_config_to_dict_includes_max_qc_rounds():
    from annotation_pipeline_skill.core.runtime import RuntimeConfig
    cfg = RuntimeConfig(max_qc_rounds=7)
    assert cfg.to_dict()["max_qc_rounds"] == 7


def test_runtime_snapshot_loads_with_omitted_empty_list_fields():
    generated_at = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)

    loaded = RuntimeSnapshot.from_dict(
        {
            "generated_at": generated_at.isoformat(),
            "runtime_status": RuntimeStatus(
                healthy=True,
                heartbeat_at=generated_at,
                heartbeat_age_seconds=0,
                active=True,
            ).to_dict(),
            "queue_counts": QueueCounts(
                pending=0,
                annotating=0,
                validating=0,
                qc=0,
                human_review=0,
                accepted=0,
                rejected=0,
            ).to_dict(),
            "capacity": CapacitySnapshot(
                max_concurrent_tasks=4,
                active_count=0,
                available_slots=4,
            ).to_dict(),
        }
    )

    assert loaded.active_runs == []
    assert loaded.stale_tasks == []
    assert loaded.due_retries == []
    assert loaded.project_summaries == []
