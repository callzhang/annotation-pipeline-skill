from annotation_pipeline_skill.core.models import FeedbackRecord, Task
from annotation_pipeline_skill.core.states import FeedbackSeverity, FeedbackSource
from annotation_pipeline_skill.store.file_store import FileStore


def test_file_store_saves_and_loads_tasks(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})

    store.save_task(task)
    loaded = store.load_task("task-1")

    assert loaded == task


def test_file_store_appends_feedback_records(tmp_path):
    store = FileStore(tmp_path)
    record = FeedbackRecord.new(
        task_id="task-1",
        attempt_id="attempt-1",
        source_stage=FeedbackSource.QC,
        severity=FeedbackSeverity.ERROR,
        category="missing_entity",
        message="Missing required entity",
        target={"field": "entities"},
        suggested_action="annotator_rerun",
        created_by="qc-policy",
    )

    store.append_feedback(record)

    assert store.list_feedback("task-1") == [record]
