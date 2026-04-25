from annotation_pipeline_skill.core.models import FeedbackRecord
from annotation_pipeline_skill.core.states import FeedbackSeverity, FeedbackSource, OutboxKind
from annotation_pipeline_skill.services.external_task_service import ExternalTaskService
from annotation_pipeline_skill.services.feedback_service import build_feedback_bundle
from annotation_pipeline_skill.store.file_store import FileStore


def test_feedback_bundle_orders_records_by_creation_time(tmp_path):
    store = FileStore(tmp_path)
    first = FeedbackRecord.new(
        task_id="task-1",
        attempt_id="attempt-1",
        source_stage=FeedbackSource.QC,
        severity=FeedbackSeverity.ERROR,
        category="format",
        message="Bad JSON shape",
        target={"path": "$"},
        suggested_action="batch_code_update",
        created_by="validator",
    )
    second = FeedbackRecord.new(
        task_id="task-1",
        attempt_id="attempt-2",
        source_stage=FeedbackSource.HUMAN_REVIEW,
        severity=FeedbackSeverity.WARNING,
        category="boundary",
        message="Box is too loose",
        target={"box_id": "b1"},
        suggested_action="manual_annotation",
        created_by="reviewer",
    )
    store.append_feedback(second)
    store.append_feedback(first)

    bundle = build_feedback_bundle(store, "task-1")

    assert [item["message"] for item in bundle["items"]] == ["Bad JSON shape", "Box is too loose"]


def test_external_task_pull_is_idempotent_and_creates_status_outbox(tmp_path):
    store = FileStore(tmp_path)
    service = ExternalTaskService(store)

    first = service.upsert_pulled_task(
        pipeline_id="pipe",
        system_id="external",
        external_task_id="42",
        payload={"text": "hello"},
    )
    second = service.upsert_pulled_task(
        pipeline_id="pipe",
        system_id="external",
        external_task_id="42",
        payload={"text": "hello again"},
    )
    record = service.enqueue_status(first, status="pending")

    assert first.task_id == second.task_id
    assert record.kind is OutboxKind.STATUS
    assert store.list_outbox() == [record]
