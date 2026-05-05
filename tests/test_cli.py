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
