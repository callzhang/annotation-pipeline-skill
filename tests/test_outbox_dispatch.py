from datetime import timedelta

from annotation_pipeline_skill.core.models import OutboxRecord, Task, utc_now
from annotation_pipeline_skill.core.states import OutboxKind, OutboxStatus, TaskStatus
from annotation_pipeline_skill.services.outbox_dispatch_service import (
    PermanentOutboxError,
    RetryableOutboxError,
    OutboxDispatchService,
)
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def callbacks(url: str = "http://callback.local/submit") -> dict:
    return {
        "submit": {"enabled": True, "url": url, "secret_env": None},
        "status": {"enabled": True, "url": "http://callback.local/status", "secret_env": None},
    }


def test_outbox_dispatch_marks_submit_record_sent_and_writes_event(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    record = OutboxRecord.new(task_id="task-1", kind=OutboxKind.SUBMIT, payload={"result": {"ok": True}})
    store.save_outbox(record)
    calls = []

    def sender(url, payload, headers):
        calls.append((url, payload, headers))
        return {"status": 200}

    result = OutboxDispatchService(store, callbacks=callbacks(), sender=sender).drain(max_items=10)

    saved = store.list_outbox()[0]
    assert result == {"sent": 1, "retry": 0, "dead_letter": 0, "skipped": 0}
    assert saved.status is OutboxStatus.SENT
    assert saved.last_error is None
    assert calls == [("http://callback.local/submit", record.payload, {})]
    assert store.list_events("task-1")[-1].reason == "external submit outbox sent"


def test_outbox_dispatch_retryable_failure_sets_next_retry(tmp_path):
    now = utc_now()
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    record = OutboxRecord.new(task_id="task-1", kind=OutboxKind.STATUS, payload={"status": "accepted"})
    store.save_outbox(record)

    def sender(url, payload, headers):
        raise RetryableOutboxError("temporary outage")

    result = OutboxDispatchService(store, callbacks=callbacks(), sender=sender).drain(
        max_items=10,
        max_attempts=3,
        retry_delay_seconds=30,
        now=now,
    )

    saved = store.list_outbox()[0]
    assert result == {"sent": 0, "retry": 1, "dead_letter": 0, "skipped": 0}
    assert saved.status is OutboxStatus.PENDING
    assert saved.retry_count == 1
    assert saved.next_retry_at == now + timedelta(seconds=30)
    assert saved.last_error == "temporary outage"
    assert store.list_events("task-1")[-1].reason == "external status outbox retry scheduled"


def test_outbox_dispatch_moves_retry_exhaustion_to_dead_letter(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    record = OutboxRecord.new(task_id="task-1", kind=OutboxKind.SUBMIT, payload={})
    record.retry_count = 1
    store.save_outbox(record)

    def sender(url, payload, headers):
        raise RetryableOutboxError("still down")

    result = OutboxDispatchService(store, callbacks=callbacks(), sender=sender).drain(max_items=10, max_attempts=2)

    saved = store.list_outbox()[0]
    assert result == {"sent": 0, "retry": 0, "dead_letter": 1, "skipped": 0}
    assert saved.status is OutboxStatus.DEAD_LETTER
    assert saved.retry_count == 2
    assert saved.last_error == "still down"
    assert store.list_events("task-1")[-1].reason == "external submit outbox dead letter"


def test_outbox_dispatch_permanent_failure_dead_letters_immediately(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    store.save_outbox(OutboxRecord.new(task_id="task-1", kind=OutboxKind.SUBMIT, payload={}))

    def sender(url, payload, headers):
        raise PermanentOutboxError("bad request")

    result = OutboxDispatchService(store, callbacks=callbacks(), sender=sender).drain(max_items=10)

    saved = store.list_outbox()[0]
    assert result == {"sent": 0, "retry": 0, "dead_letter": 1, "skipped": 0}
    assert saved.status is OutboxStatus.DEAD_LETTER
    assert saved.last_error == "bad request"


def test_outbox_dispatch_skips_not_due_records(tmp_path):
    now = utc_now()
    store = SqliteStore.open(tmp_path)
    record = OutboxRecord.new(task_id="task-1", kind=OutboxKind.SUBMIT, payload={})
    record.next_retry_at = now + timedelta(seconds=60)
    store.save_outbox(record)

    result = OutboxDispatchService(store, callbacks=callbacks(), sender=lambda url, payload, headers: {}).drain(
        max_items=10,
        now=now,
    )

    assert result == {"sent": 0, "retry": 0, "dead_letter": 0, "skipped": 1}
    assert store.list_outbox()[0].status is OutboxStatus.PENDING
