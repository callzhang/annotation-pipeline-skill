import json

import annotation_pipeline_skill.config.loader as config_loader
import annotation_pipeline_skill.interfaces.cli as cli
from annotation_pipeline_skill.interfaces.cli import main
from annotation_pipeline_skill.store.file_store import FileStore


def test_cli_init_creates_project_layout(tmp_path):
    exit_code = main(["init", "--project-root", str(tmp_path)])

    assert exit_code == 0
    config_root = tmp_path / ".annotation-pipeline"
    assert not (config_root / "providers.yaml").exists()
    assert not (config_root / "stage_routes.yaml").exists()
    assert (config_root / "workflow.yaml").exists()
    assert (config_root / "llm_profiles.yaml").exists()
    assert (config_root / "annotators.yaml").exists()
    assert (config_root / "tasks").is_dir()
    assert (config_root / "exports").is_dir()


def test_cli_init_writes_runtime_config(tmp_path):
    main(["init", "--project-root", str(tmp_path)])

    workflow = (tmp_path / ".annotation-pipeline" / "workflow.yaml").read_text(encoding="utf-8")

    assert "runtime:" in workflow
    assert "max_concurrent_tasks: 4" in workflow


def test_cli_doctor_succeeds_after_init(tmp_path):
    main(["init", "--project-root", str(tmp_path)])

    exit_code = main(["doctor", "--project-root", str(tmp_path)])

    assert exit_code == 0


def test_cli_runtime_status_returns_snapshot_after_init(tmp_path, capsys):
    main(["init", "--project-root", str(tmp_path)])

    exit_code = main(["runtime", "status", "--project-root", str(tmp_path)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert "runtime_status" in payload
    assert payload["capacity"]["max_concurrent_tasks"] == 4


def test_cli_runtime_status_does_not_load_llm_registry(tmp_path, capsys, monkeypatch):
    main(["init", "--project-root", str(tmp_path)])

    def fail_load_llm_registry(path):
        raise AssertionError("runtime status should not load llm registry")

    monkeypatch.setattr(config_loader, "load_llm_registry", fail_load_llm_registry)

    exit_code = main(["runtime", "status", "--project-root", str(tmp_path)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["capacity"]["max_concurrent_tasks"] == 4


def test_cli_runtime_context_reuses_loaded_llm_registry(tmp_path, monkeypatch):
    main(["init", "--project-root", str(tmp_path)])
    calls = []
    real_load_llm_registry = cli.load_llm_registry

    def counted_load_llm_registry(path):
        calls.append(path)
        return real_load_llm_registry(path)

    monkeypatch.setattr(cli, "load_llm_registry", counted_load_llm_registry)

    context = cli._runtime_context(tmp_path)
    cli._build_runtime_scheduler(context)

    assert len(calls) == 1


def test_cli_create_tasks_from_jsonl(tmp_path):
    main(["init", "--project-root", str(tmp_path)])
    source = tmp_path / "input.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps({"text": "alpha"}),
                json.dumps({"text": "beta", "modality": "text", "annotation_types": ["entity_span"]}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "create-tasks",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--pipeline-id",
            "demo",
        ]
    )

    store = FileStore(tmp_path / ".annotation-pipeline")
    tasks = store.list_tasks()
    assert exit_code == 0
    assert [task.task_id for task in tasks] == ["demo-000001", "demo-000002"]
    assert tasks[1].annotation_requirements == {"annotation_types": ["entity_span"]}


def test_cli_create_batched_jsonl_tasks(tmp_path):
    main(["init", "--project-root", str(tmp_path)])
    source = tmp_path / "input.jsonl"
    source.write_text(
        "\n".join(
            json.dumps({"text": f"row {index}", "source_dataset": "demo_source"})
            for index in range(1, 6)
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "create-tasks",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--pipeline-id",
            "v2",
            "--task-prefix",
            "memory-ner-v2",
            "--batch-size",
            "2",
            "--annotation-type",
            "entity_span",
            "--annotation-type",
            "structured_json",
        ]
    )

    store = FileStore(tmp_path / ".annotation-pipeline")
    tasks = store.list_tasks()
    assert exit_code == 0
    assert [task.task_id for task in tasks] == [
        "memory-ner-v2-000001",
        "memory-ner-v2-000002",
        "memory-ner-v2-000003",
    ]
    assert [task.source_ref["row_count"] for task in tasks] == [2, 2, 1]
    assert tasks[0].source_ref["line_start"] == 1
    assert tasks[0].source_ref["line_end"] == 2
    assert len(tasks[0].source_ref["payload"]["rows"]) == 2
    assert tasks[0].annotation_requirements == {"annotation_types": ["entity_span", "structured_json"]}
    assert tasks[0].metadata["qc_policy"]["mode"] == "all_rows"
    assert tasks[0].metadata["qc_policy"]["required_correct_rows"] == 2
    assert tasks[0].metadata["sources"] == ["demo_source"]


def test_cli_create_batched_jsonl_tasks_does_not_cross_group_boundaries(tmp_path):
    main(["init", "--project-root", str(tmp_path)])
    source = tmp_path / "input.jsonl"
    rows = [
        {"text": "a1", "source_dataset": "a"},
        {"text": "a2", "source_dataset": "a"},
        {"text": "a3", "source_dataset": "a"},
        {"text": "b1", "source_dataset": "b"},
        {"text": "b2", "source_dataset": "b"},
    ]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    exit_code = main(
        [
            "create-tasks",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--pipeline-id",
            "v2",
            "--batch-size",
            "2",
            "--group-by",
            "source_dataset",
        ]
    )

    store = FileStore(tmp_path / ".annotation-pipeline")
    tasks = store.list_tasks()
    assert exit_code == 0
    assert [task.source_ref["row_count"] for task in tasks] == [2, 1, 2]
    assert [task.metadata["sources"] for task in tasks] == [["a"], ["a"], ["b"]]


def test_cli_export_training_data_writes_manifest(tmp_path, capsys):
    from annotation_pipeline_skill.core.models import ArtifactRef
    from annotation_pipeline_skill.core.states import TaskStatus

    main(["init", "--project-root", str(tmp_path)])
    store = FileStore(tmp_path / ".annotation-pipeline")
    source = tmp_path / "input.jsonl"
    source.write_text(json.dumps({"text": "alpha"}) + "\n", encoding="utf-8")
    main(
        [
            "create-tasks",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--pipeline-id",
            "pipe",
        ]
    )
    task = store.load_task("pipe-000001")
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    payload_path = store.root / "artifact_payloads/pipe-000001/pipe-000001-attempt-1_annotation_result.json"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_text(json.dumps({"text": '{"labels":[]}'}), encoding="utf-8")
    store.append_artifact(
        ArtifactRef.new(
            task_id="pipe-000001",
            kind="annotation_result",
            path="artifact_payloads/pipe-000001/pipe-000001-attempt-1_annotation_result.json",
            content_type="application/json",
        )
    )

    exit_code = main(
        [
            "export",
            "training-data",
            "--project-root",
            str(tmp_path),
            "--project-id",
            "pipe",
            "--export-id",
            "export-1",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["export_id"] == "export-1"
    assert payload["task_ids_included"] == ["pipe-000001"]
    assert (tmp_path / ".annotation-pipeline" / "exports" / "export-1" / "training_data.jsonl").exists()


def test_cli_report_readiness_returns_project_action(tmp_path, capsys):
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus

    main(["init", "--project-root", str(tmp_path)])
    store = FileStore(tmp_path / ".annotation-pipeline")
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)

    exit_code = main(["report", "readiness", "--project-root", str(tmp_path), "--project-id", "pipe"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["project_id"] == "pipe"
    assert payload["accepted_count"] == 1
    assert payload["recommended_next_action"] == "repair_export_blockers"


def test_cli_outbox_status_reports_counts(tmp_path, capsys):
    from annotation_pipeline_skill.core.models import OutboxRecord
    from annotation_pipeline_skill.core.states import OutboxKind

    main(["init", "--project-root", str(tmp_path)])
    store = FileStore(tmp_path / ".annotation-pipeline")
    store.save_outbox(OutboxRecord.new(task_id="task-1", kind=OutboxKind.SUBMIT, payload={}))

    exit_code = main(["outbox", "status", "--project-root", str(tmp_path)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["counts"] == {"dead_letter": 0, "pending": 1, "sent": 0}
    assert payload["records"][0]["kind"] == "submit"


def test_cli_human_review_request_changes_returns_task_to_annotating(tmp_path, capsys):
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus

    main(["init", "--project-root", str(tmp_path)])
    store = FileStore(tmp_path / ".annotation-pipeline")
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)

    exit_code = main(
        [
            "human-review",
            "decide",
            "--project-root",
            str(tmp_path),
            "--task-id",
            "task-1",
            "--action",
            "request_changes",
            "--actor",
            "algorithm-engineer",
            "--feedback",
            "Run the batch boundary update.",
            "--correction-mode",
            "batch_code_update",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["task"]["status"] == "annotating"
    assert payload["decision"]["correction_mode"] == "batch_code_update"
    assert store.load_task("task-1").status is TaskStatus.ANNOTATING
