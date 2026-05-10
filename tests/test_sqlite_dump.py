import json
from pathlib import Path

from annotation_pipeline_skill.core.models import (
    AuditEvent, ExportManifest, FeedbackRecord, OutboxRecord, Task,
)
from annotation_pipeline_skill.core.states import (
    FeedbackSeverity, FeedbackSource, OutboxKind, TaskStatus,
)
from annotation_pipeline_skill.store.dump import dump_to_json
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_dump_writes_tasks_and_logs(tmp_path):
    store = SqliteStore.open(tmp_path / "db_root")
    task = Task.new(task_id="t-1", pipeline_id="p", source_ref={"k": "v"})
    store.save_task(task)
    store.append_event(AuditEvent.new(
        "t-1", TaskStatus.DRAFT, TaskStatus.PENDING,
        actor="a", reason="r", stage="ingest",
    ))
    store.append_feedback(FeedbackRecord.new(
        task_id="t-1", attempt_id="a-1",
        source_stage=FeedbackSource.QC, severity=FeedbackSeverity.ERROR,
        category="c", message="m", target={}, suggested_action="s", created_by="u",
    ))
    store.save_outbox(OutboxRecord.new("t-1", OutboxKind.STATUS, {}))
    store.close()

    out = tmp_path / "out"
    store = SqliteStore.open(tmp_path / "db_root")
    dump_to_json(store, out)
    store.close()

    assert (out / "tasks" / "t-1.json").exists()
    assert json.loads((out / "tasks" / "t-1.json").read_text())["task_id"] == "t-1"
    assert (out / "events" / "t-1.jsonl").exists()
    assert (out / "feedback" / "t-1.jsonl").exists()
    assert any((out / "outbox").glob("*.json"))
