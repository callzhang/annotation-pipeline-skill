import json
from datetime import datetime, timezone
from pathlib import Path

from annotation_pipeline_skill.core.models import AuditEvent, Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_open_creates_schema_and_sets_pragmas(tmp_path: Path):
    store = SqliteStore.open(tmp_path)

    assert (tmp_path / "db.sqlite").exists()
    # foreign_keys is a per-connection pragma — verify it on the store's own
    # connection. journal_mode and user_version are persisted in the database
    # file so they're visible from any connection.
    conn = store._conn
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tasks", "audit_events", "attempts", "feedback_records", "outbox_records",
            "runtime_leases", "documents", "document_versions", "export_manifests"} <= names
    store.close()


def test_open_is_idempotent_on_existing_db(tmp_path: Path):
    SqliteStore.open(tmp_path).close()
    store = SqliteStore.open(tmp_path)
    store.close()


def _make_task(task_id: str, pipeline_id: str = "pipe-1", status: TaskStatus = TaskStatus.DRAFT) -> Task:
    task = Task.new(task_id=task_id, pipeline_id=pipeline_id, source_ref={"kind": "jsonl"})
    task.status = status
    return task


def test_save_and_load_task(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = _make_task("task-1")

    store.save_task(task)
    loaded = store.load_task("task-1")

    assert loaded == task
    store.close()


def test_save_task_is_upsert(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = _make_task("task-1")
    store.save_task(task)

    task.status = TaskStatus.PENDING
    task.metadata = {"note": "updated"}
    store.save_task(task)

    loaded = store.load_task("task-1")
    assert loaded.status is TaskStatus.PENDING
    assert loaded.metadata == {"note": "updated"}
    store.close()


def test_list_tasks_returns_all(tmp_path):
    store = SqliteStore.open(tmp_path)
    store.save_task(_make_task("task-1", pipeline_id="a"))
    store.save_task(_make_task("task-2", pipeline_id="b"))

    ids = sorted(t.task_id for t in store.list_tasks())
    assert ids == ["task-1", "task-2"]
    store.close()


def test_list_tasks_by_pipeline_filters(tmp_path):
    store = SqliteStore.open(tmp_path)
    store.save_task(_make_task("a-1", pipeline_id="a"))
    store.save_task(_make_task("a-2", pipeline_id="a"))
    store.save_task(_make_task("b-1", pipeline_id="b"))

    rows = store.list_tasks_by_pipeline("a")
    assert sorted(t.task_id for t in rows) == ["a-1", "a-2"]
    store.close()


def test_list_tasks_by_status_filters(tmp_path):
    store = SqliteStore.open(tmp_path)
    store.save_task(_make_task("draft-1", status=TaskStatus.DRAFT))
    store.save_task(_make_task("pend-1", status=TaskStatus.PENDING))
    store.save_task(_make_task("pend-2", status=TaskStatus.PENDING))

    rows = store.list_tasks_by_status({TaskStatus.PENDING})
    assert sorted(t.task_id for t in rows) == ["pend-1", "pend-2"]
    store.close()


def test_save_task_roundtrips_all_fields(tmp_path):
    from annotation_pipeline_skill.core.models import ExternalTaskRef
    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="task-full",
        pipeline_id="pipe",
        source_ref={"kind": "jsonl", "path": "x.jsonl"},
        external_ref=ExternalTaskRef(
            system_id="sys-1",
            external_task_id="ext-1",
            source_url="http://example.com/1",
            idempotency_key="key-1",
        ),
        modality="text",
        annotation_requirements={"schema": "ner"},
        selected_annotator_id="annot-A",
        metadata={"note": "fully populated"},
        document_version_id="docver-1",
    )
    store.save_task(task)
    loaded = store.load_task("task-full")
    assert loaded == task
    store.close()


def test_append_and_list_events_preserves_order(tmp_path):
    store = SqliteStore.open(tmp_path)
    e1 = AuditEvent.new("task-1", TaskStatus.DRAFT, TaskStatus.PENDING, actor="a", reason="r1", stage="ingest")
    e2 = AuditEvent.new("task-1", TaskStatus.PENDING, TaskStatus.ANNOTATING, actor="a", reason="r2", stage="annotate")

    store.append_event(e1)
    store.append_event(e2)

    rows = store.list_events("task-1")
    assert [e.event_id for e in rows] == [e1.event_id, e2.event_id]
    store.close()


def test_list_events_returns_empty_for_unknown_task(tmp_path):
    store = SqliteStore.open(tmp_path)
    assert store.list_events("nope") == []
    store.close()


from annotation_pipeline_skill.core.models import (
    ArtifactRef, Attempt, FeedbackDiscussionEntry, FeedbackRecord,
)
from annotation_pipeline_skill.core.states import (
    AttemptStatus, FeedbackSeverity, FeedbackSource,
)


def test_append_and_list_attempts(tmp_path):
    store = SqliteStore.open(tmp_path)
    a = Attempt(
        attempt_id="att-1", task_id="task-1", index=0, stage="annotate",
        status=AttemptStatus.SUCCEEDED,
    )
    store.append_attempt(a)
    assert store.list_attempts("task-1") == [a]
    store.close()


def test_append_and_list_feedback(tmp_path):
    store = SqliteStore.open(tmp_path)
    f = FeedbackRecord.new(
        task_id="task-1", attempt_id="att-1",
        source_stage=FeedbackSource.QC, severity=FeedbackSeverity.ERROR,
        category="missing_entity", message="m", target={"f": "x"},
        suggested_action="rerun", created_by="qc",
    )
    store.append_feedback(f)
    assert store.list_feedback("task-1") == [f]
    store.close()


def test_append_and_list_feedback_discussion(tmp_path):
    store = SqliteStore.open(tmp_path)
    d = FeedbackDiscussionEntry.new(
        task_id="task-1", feedback_id="fb-1",
        role="annotator", stance="agree", message="ok", created_by="annotator-1",
    )
    store.append_feedback_discussion(d)
    assert store.list_feedback_discussions("task-1") == [d]
    store.close()


def test_append_and_list_artifact(tmp_path):
    store = SqliteStore.open(tmp_path)
    a = ArtifactRef.new(
        task_id="task-1", kind="annotation_result",
        path="artifacts/task-1.json", content_type="application/json",
    )
    store.append_artifact(a)
    assert store.list_artifacts("task-1") == [a]
    store.close()


from annotation_pipeline_skill.core.models import OutboxRecord
from annotation_pipeline_skill.core.states import OutboxKind, OutboxStatus


def test_save_and_list_outbox(tmp_path):
    store = SqliteStore.open(tmp_path)
    rec = OutboxRecord.new("task-1", OutboxKind.STATUS, {"foo": "bar"})
    store.save_outbox(rec)

    listed = store.list_outbox()
    assert len(listed) == 1 and listed[0] == rec
    store.close()


def test_save_outbox_is_upsert(tmp_path):
    store = SqliteStore.open(tmp_path)
    rec = OutboxRecord.new("task-1", OutboxKind.STATUS, {"foo": "bar"})
    store.save_outbox(rec)
    rec.status = OutboxStatus.SENT
    store.save_outbox(rec)

    listed = store.list_outbox()
    assert listed[0].status is OutboxStatus.SENT
    store.close()


def test_list_pending_outbox_filters_by_status_and_retry(tmp_path):
    from datetime import datetime, timedelta, timezone
    store = SqliteStore.open(tmp_path)

    a = OutboxRecord.new("t-1", OutboxKind.STATUS, {})
    b = OutboxRecord.new("t-2", OutboxKind.STATUS, {})
    b.status = OutboxStatus.SENT
    c = OutboxRecord.new("t-3", OutboxKind.STATUS, {})
    c.next_retry_at = datetime.now(timezone.utc) + timedelta(hours=1)
    for r in (a, b, c):
        store.save_outbox(r)

    pending = store.list_pending_outbox(now=datetime.now(timezone.utc))
    assert [r.record_id for r in pending] == [a.record_id]
    store.close()
