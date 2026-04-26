import json

from annotation_pipeline_skill.interfaces.cli import main
from annotation_pipeline_skill.store.file_store import FileStore


def test_cli_init_creates_project_layout(tmp_path):
    exit_code = main(["init", "--project-root", str(tmp_path)])

    assert exit_code == 0
    config_root = tmp_path / ".annotation-pipeline"
    assert (config_root / "providers.yaml").exists()
    assert (config_root / "stage_routes.yaml").exists()
    assert (config_root / "annotators.yaml").exists()
    assert (config_root / "tasks").is_dir()


def test_cli_doctor_succeeds_after_init(tmp_path):
    main(["init", "--project-root", str(tmp_path)])

    exit_code = main(["doctor", "--project-root", str(tmp_path)])

    assert exit_code == 0


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
