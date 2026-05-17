import json

from annotation_pipeline_skill.core.models import ArtifactRef, ExportManifest, FeedbackRecord, Task
from annotation_pipeline_skill.core.states import FeedbackSeverity, FeedbackSource, OutboxKind, TaskStatus
from annotation_pipeline_skill.services.readiness_service import build_readiness_report
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_readiness_report_recommends_export_when_accepted_tasks_are_exportable(tmp_path):
    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    payload_path = store.root / "artifact_payloads/task-1/task-1-attempt-1_annotation_result.json"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_text(json.dumps({"text": '{"labels":[]}'}), encoding="utf-8")
    artifact = ArtifactRef.new(
        task_id="task-1",
        kind="annotation_result",
        path="artifact_payloads/task-1/task-1-attempt-1_annotation_result.json",
        content_type="application/json",
    )
    store.append_artifact(artifact)

    report = build_readiness_report(store, project_id="pipe")

    assert report == {
        "project_id": "pipe",
        "ready_for_training": False,
        "accepted_count": 1,
        "exported_count": 0,
        "pending_export_count": 1,
        "open_feedback_count": 0,
        "resolved_feedback_count": 0,
        "closed_feedback_count": 0,
        "human_review_count": 0,
        "validation_blockers": [],
        "pending_outbox_count": 0,
        "dead_letter_outbox_count": 0,
        "latest_export": None,
        "exports": [],
        "recommended_next_action": "export_training_data",
        "next_command": "annotation-pipeline export training-data --project-id pipe",
        "export_command": f"annotation-pipeline export training-data --project-root {tmp_path} --project-id pipe",
    }


def test_readiness_report_prioritizes_feedback_and_export_blockers(tmp_path):
    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    accepted_missing = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    accepted_missing.status = TaskStatus.ACCEPTED
    review_task = Task.new(task_id="task-2", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    review_task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(accepted_missing)
    store.save_task(review_task)
    store.append_feedback(
        FeedbackRecord.new(
            task_id="task-2",
            attempt_id="attempt-1",
            source_stage=FeedbackSource.QC,
            severity=FeedbackSeverity.WARNING,
            category="span",
            message="Needs agreement.",
            target={},
            suggested_action="manual_annotation",
            created_by="qc",
        )
    )

    report = build_readiness_report(store, project_id="pipe")

    assert report["ready_for_training"] is False
    assert report["human_review_count"] == 1
    assert report["open_feedback_count"] == 1
    assert report["validation_blockers"] == [{"task_id": "task-1", "reason": "missing_annotation_result"}]
    assert report["recommended_next_action"] == "complete_human_review"


def test_readiness_report_marks_project_ready_after_export(tmp_path):
    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    store.save_export_manifest(
        ExportManifest.new(
            project_id="pipe",
            output_paths=["exports/export-1/training_data.jsonl"],
            task_ids_included=["task-1"],
            task_ids_excluded=[],
            artifact_ids=["artifact-1"],
            source_files=["input.jsonl"],
            annotation_rules_hash=None,
            schema_version="jsonl-training-v1",
            validator_version="local-export-v1",
            validation_summary={"included": 1, "excluded": 0},
            export_id="export-1",
        )
    )

    report = build_readiness_report(store, project_id="pipe")

    assert report["ready_for_training"] is True
    assert report["accepted_count"] == 1
    assert report["exported_count"] == 1
    assert report["latest_export"]["export_id"] == "export-1"
    assert report["latest_export"]["output_paths"] == ["exports/export-1/training_data.jsonl"]
    assert report["recommended_next_action"] == "deliver_training_data"


def test_readiness_report_surfaces_latest_export_invalid_row_blockers(tmp_path):
    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    store.save_export_manifest(
        ExportManifest.new(
            project_id="pipe",
            output_paths=["exports/export-1/training_data.jsonl"],
            task_ids_included=[],
            task_ids_excluded=[
                {
                    "task_id": "task-1",
                    "reason": "invalid_training_row",
                    "errors": ["annotation_string_must_be_json"],
                }
            ],
            artifact_ids=[],
            source_files=[],
            annotation_rules_hash=None,
            schema_version="jsonl-training-v2",
            validator_version="local-export-v2",
            validation_summary={"included": 0, "excluded": 1},
            export_id="export-1",
        )
    )

    report = build_readiness_report(store, project_id="pipe")

    assert report["validation_blockers"] == [
        {
            "task_id": "task-1",
            "reason": "invalid_training_row",
            "errors": ["annotation_string_must_be_json"],
        }
    ]
    assert report["recommended_next_action"] == "fix_export_blockers"


def test_readiness_report_waits_for_external_outbox_after_export(tmp_path):
    from annotation_pipeline_skill.core.models import OutboxRecord

    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    store.save_export_manifest(
        ExportManifest.new(
            project_id="pipe",
            output_paths=["exports/export-1/training_data.jsonl"],
            task_ids_included=["task-1"],
            task_ids_excluded=[],
            artifact_ids=[],
            source_files=[],
            annotation_rules_hash=None,
            schema_version="jsonl-training-v1",
            validator_version="local-export-v1",
            validation_summary={"included": 1},
            export_id="export-1",
        )
    )
    store.save_outbox(OutboxRecord.new(task_id="task-1", kind=OutboxKind.SUBMIT, payload={"export_id": "export-1"}))

    report = build_readiness_report(store, project_id="pipe")

    assert report["ready_for_training"] is False
    assert report["pending_outbox_count"] == 1
    assert report["recommended_next_action"] == "drain_external_outbox"


def test_readiness_report_blocks_on_dead_letter_outbox(tmp_path):
    from annotation_pipeline_skill.core.models import OutboxRecord
    from annotation_pipeline_skill.core.states import OutboxStatus

    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    store.save_export_manifest(
        ExportManifest.new(
            project_id="pipe",
            output_paths=["exports/export-1/training_data.jsonl"],
            task_ids_included=["task-1"],
            task_ids_excluded=[],
            artifact_ids=[],
            source_files=[],
            annotation_rules_hash=None,
            schema_version="jsonl-training-v1",
            validator_version="local-export-v1",
            validation_summary={"included": 1},
            export_id="export-1",
        )
    )
    record = OutboxRecord.new(task_id="task-1", kind=OutboxKind.SUBMIT, payload={"export_id": "export-1"})
    record.status = OutboxStatus.DEAD_LETTER
    store.save_outbox(record)

    report = build_readiness_report(store, project_id="pipe")

    assert report["ready_for_training"] is False
    assert report["dead_letter_outbox_count"] == 1
    assert report["recommended_next_action"] == "inspect_dead_letter_outbox"
