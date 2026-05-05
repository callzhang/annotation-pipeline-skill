import json

from annotation_pipeline_skill.core.models import ArtifactRef, Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.services.export_service import TrainingDataExportService
from annotation_pipeline_skill.store.file_store import FileStore


def test_export_service_writes_training_jsonl_and_manifest_for_accepted_tasks(tmp_path):
    store = FileStore(tmp_path / ".annotation-pipeline")
    accepted = Task.new(
        task_id="task-1",
        pipeline_id="pipe",
        source_ref={"kind": "jsonl", "path": "input.jsonl", "payload": {"text": "alpha"}},
    )
    accepted.status = TaskStatus.ACCEPTED
    excluded = Task.new(
        task_id="task-2",
        pipeline_id="pipe",
        source_ref={"kind": "jsonl", "path": "input.jsonl", "payload": {"text": "beta"}},
    )
    excluded.status = TaskStatus.ACCEPTED
    other_project = Task.new(task_id="task-3", pipeline_id="other", source_ref={"kind": "jsonl"})
    other_project.status = TaskStatus.ACCEPTED
    store.save_task(accepted)
    store.save_task(excluded)
    store.save_task(other_project)

    payload_path = store.root / "artifact_payloads/task-1/task-1-attempt-1_annotation_result.json"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_text(
        json.dumps({"task_id": "task-1", "text": '{"labels":[{"text":"alpha"}]}'}),
        encoding="utf-8",
    )
    artifact = ArtifactRef.new(
        task_id="task-1",
        kind="annotation_result",
        path="artifact_payloads/task-1/task-1-attempt-1_annotation_result.json",
        content_type="application/json",
        metadata={"provider": "local_codex"},
    )
    store.append_artifact(artifact)

    manifest = TrainingDataExportService(store).export_jsonl(
        project_id="pipe",
        output_dir=store.root / "exports/export-1",
        export_id="export-1",
    )

    rows = [
        json.loads(line)
        for line in (store.root / "exports/export-1/training_data.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [
        {
            "task_id": "task-1",
            "pipeline_id": "pipe",
            "source_ref": accepted.source_ref,
            "modality": "text",
            "annotation_requirements": {},
            "annotation": '{"labels":[{"text":"alpha"}]}',
            "annotation_artifact_id": artifact.artifact_id,
            "annotation_artifact_path": artifact.path,
        }
    ]
    assert manifest.project_id == "pipe"
    assert manifest.task_ids_included == ["task-1"]
    assert manifest.task_ids_excluded == [{"task_id": "task-2", "reason": "missing_annotation_result"}]
    assert manifest.artifact_ids == [artifact.artifact_id]
    assert manifest.source_files == ["input.jsonl"]
    assert manifest.schema_version == "jsonl-training-v2"
    assert manifest.validator_version == "local-export-v2"
    assert manifest.validation_summary == {
        "accepted_tasks": 2,
        "included": 1,
        "excluded": 1,
        "required_fields": [
            "task_id",
            "pipeline_id",
            "source_ref",
            "modality",
            "annotation_requirements",
            "annotation",
            "annotation_artifact_id",
            "annotation_artifact_path",
        ],
        "row_errors": [],
        "errors": [{"task_id": "task-2", "reason": "missing_annotation_result"}],
    }
    assert store.list_export_manifests() == [manifest]


def test_export_service_creates_submit_outbox_for_external_included_tasks(tmp_path):
    from annotation_pipeline_skill.core.models import ExternalTaskRef
    from annotation_pipeline_skill.core.states import OutboxKind

    store = FileStore(tmp_path / ".annotation-pipeline")
    task = Task.new(
        task_id="task-1",
        pipeline_id="pipe",
        source_ref={"kind": "external_task", "payload": {"text": "alpha"}},
        external_ref=ExternalTaskRef(
            system_id="external",
            external_task_id="ext-1",
            source_url=None,
            idempotency_key="external:ext-1",
        ),
    )
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    payload_path = store.root / "artifact_payloads/task-1/task-1-attempt-1_annotation_result.json"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_text(json.dumps({"text": '{"labels":[]}'}), encoding="utf-8")
    store.append_artifact(
        ArtifactRef.new(
            task_id="task-1",
            kind="annotation_result",
            path="artifact_payloads/task-1/task-1-attempt-1_annotation_result.json",
            content_type="application/json",
        )
    )

    manifest = TrainingDataExportService(store).export_jsonl(
        project_id="pipe",
        output_dir=store.root / "exports/export-1",
        export_id="export-1",
        enqueue_external_submit=True,
    )

    outbox = store.list_outbox()
    assert manifest.task_ids_included == ["task-1"]
    assert len(outbox) == 1
    assert outbox[0].kind is OutboxKind.SUBMIT
    assert outbox[0].payload["export_id"] == "export-1"
    assert outbox[0].payload["external_ref"]["external_task_id"] == "ext-1"


def test_export_service_excludes_accepted_task_when_artifact_payload_is_missing(tmp_path):
    store = FileStore(tmp_path / ".annotation-pipeline")
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    store.append_artifact(
        ArtifactRef.new(
            task_id="task-1",
            kind="annotation_result",
            path="artifact_payloads/task-1/missing.json",
            content_type="application/json",
        )
    )

    manifest = TrainingDataExportService(store).export_jsonl(
        project_id="pipe",
        output_dir=store.root / "exports/export-1",
        export_id="export-1",
    )

    assert manifest.task_ids_included == []
    assert manifest.task_ids_excluded == [{"task_id": "task-1", "reason": "missing_annotation_payload"}]
    assert (store.root / "exports/export-1/training_data.jsonl").read_text(encoding="utf-8") == ""


def test_export_service_excludes_invalid_training_row_when_annotation_is_not_json(tmp_path):
    store = FileStore(tmp_path / ".annotation-pipeline")
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl", "payload": {"text": "alpha"}})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    payload_path = store.root / "artifact_payloads/task-1/task-1-attempt-1_annotation_result.json"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_text(json.dumps({"text": "not json"}), encoding="utf-8")
    artifact = ArtifactRef.new(
        task_id="task-1",
        kind="annotation_result",
        path="artifact_payloads/task-1/task-1-attempt-1_annotation_result.json",
        content_type="application/json",
    )
    store.append_artifact(artifact)

    manifest = TrainingDataExportService(store).export_jsonl(
        project_id="pipe",
        output_dir=store.root / "exports/export-1",
        export_id="export-1",
    )

    assert manifest.task_ids_included == []
    assert manifest.task_ids_excluded == [
        {
            "task_id": "task-1",
            "reason": "invalid_training_row",
            "errors": ["annotation_string_must_be_json"],
        }
    ]
    assert manifest.validation_summary["row_errors"] == [
        {
            "task_id": "task-1",
            "errors": ["annotation_string_must_be_json"],
        }
    ]
    assert (store.root / "exports/export-1/training_data.jsonl").read_text(encoding="utf-8") == ""
