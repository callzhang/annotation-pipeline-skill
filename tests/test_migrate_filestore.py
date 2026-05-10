import json
import shutil
from pathlib import Path

from annotation_pipeline_skill.core.models import (
    AuditEvent, FeedbackRecord, OutboxRecord, Task,
)
from annotation_pipeline_skill.core.states import (
    FeedbackSeverity, FeedbackSource, OutboxKind, TaskStatus,
)
from annotation_pipeline_skill.store.file_store import FileStore
from annotation_pipeline_skill.store.sqlite_store import SqliteStore
from scripts.migrate_filestore_to_sqlite import migrate


def test_migrate_round_trips_tasks_events_feedback_outbox(tmp_path):
    src = tmp_path / "src"
    fs = FileStore(src)
    task = Task.new(task_id="t-1", pipeline_id="p", source_ref={"k": "v"})
    fs.save_task(task)
    fs.append_event(AuditEvent.new(
        "t-1", TaskStatus.DRAFT, TaskStatus.PENDING,
        actor="a", reason="r", stage="ingest",
    ))
    fs.append_feedback(FeedbackRecord.new(
        task_id="t-1", attempt_id="att-1",
        source_stage=FeedbackSource.QC, severity=FeedbackSeverity.ERROR,
        category="c", message="m", target={}, suggested_action="s", created_by="u",
    ))
    fs.save_outbox(OutboxRecord.new("t-1", OutboxKind.STATUS, {"a": 1}))

    dst = tmp_path / "dst"
    report = migrate(src, dst, archive_genesis=True)

    assert report["tasks"] == 1
    assert report["events"] == 1
    assert report["feedback"] == 1
    assert report["outbox"] == 1

    store = SqliteStore.open(dst)
    assert store.load_task("t-1") == task
    assert len(store.list_events("t-1")) == 1
    assert len(store.list_feedback("t-1")) == 1
    assert len(store.list_outbox()) == 1
    store.close()

    archived = list((dst / "backups").glob("genesis-*"))
    assert len(archived) == 1


def test_migrate_refuses_to_run_against_non_empty_db(tmp_path):
    import pytest

    src = tmp_path / "src"
    FileStore(src)  # creates empty dirs

    dst = tmp_path / "dst"
    store = SqliteStore.open(dst)
    store.save_task(Task.new(task_id="existing", pipeline_id="p", source_ref={}))
    store.close()

    with pytest.raises(RuntimeError, match="not empty"):
        migrate(src, dst, archive_genesis=False)


def test_migrate_refuses_when_dst_is_inside_src(tmp_path):
    import pytest
    src = tmp_path / "src"
    src.mkdir()
    dst = src / "child"
    with pytest.raises(RuntimeError, match="disjoint"):
        migrate(src, dst, archive_genesis=False)


def test_migrate_refuses_when_src_is_inside_dst(tmp_path):
    import pytest
    dst = tmp_path / "dst"
    dst.mkdir()
    src = dst / "child"
    src.mkdir()
    with pytest.raises(RuntimeError, match="disjoint"):
        migrate(src, dst, archive_genesis=False)
