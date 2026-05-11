import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import annotation_pipeline_skill.config.loader as config_loader
import annotation_pipeline_skill.interfaces.cli as cli
from annotation_pipeline_skill.interfaces.cli import main
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


@contextmanager
def external_pull_server(response_payload):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.dumps(response_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/pull"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_cli_init_creates_project_layout(tmp_path):
    project = tmp_path / "proj"
    exit_code = main(["init", "--project-root", str(project)])

    assert exit_code == 0
    config_root = project / ".annotation-pipeline"
    assert not (config_root / "providers.yaml").exists()
    assert not (config_root / "stage_routes.yaml").exists()
    assert (config_root / "workflow.yaml").exists()
    assert (config_root / "annotators.yaml").exists()
    assert (config_root / "tasks").is_dir()
    assert (config_root / "exports").is_dir()
    assert (config_root / "coordination").is_dir()
    # llm_profiles.yaml is workspace-global, NOT per-project.
    assert not (config_root / "llm_profiles.yaml").exists()
    assert (tmp_path / "llm_profiles.yaml").exists()


def test_cli_init_does_not_overwrite_existing_workspace_llm_profiles(tmp_path):
    workspace_profiles = tmp_path / "llm_profiles.yaml"
    workspace_profiles.write_text("existing: content\n", encoding="utf-8")

    main(["init", "--project-root", str(tmp_path / "proj")])

    assert workspace_profiles.read_text(encoding="utf-8") == "existing: content\n"


def test_cli_init_seeds_workspace_llm_profiles_when_absent(tmp_path):
    project = tmp_path / "proj"
    main(["init", "--project-root", str(project)])

    content = (tmp_path / "llm_profiles.yaml").read_text(encoding="utf-8")
    assert "profiles:" in content
    assert "targets:" in content
    assert "deepseek_flash" in content


def test_cli_init_accepts_explicit_workspace_flag(tmp_path):
    ws = tmp_path / "shared"
    main(
        [
            "init",
            "--project-root",
            str(tmp_path / "proj-a"),
            "--workspace",
            str(ws),
        ]
    )
    assert (ws / "llm_profiles.yaml").exists()
    # Per-project dir must NOT also contain it.
    assert not (tmp_path / "proj-a" / ".annotation-pipeline" / "llm_profiles.yaml").exists()


def test_cli_init_writes_runtime_config(tmp_path):
    main(["init", "--project-root", str(tmp_path)])

    workflow = (tmp_path / ".annotation-pipeline" / "workflow.yaml").read_text(encoding="utf-8")

    assert "runtime:" in workflow
    assert "max_concurrent_tasks: 4" in workflow


def test_cli_doctor_succeeds_after_init(tmp_path):
    main(["init", "--project-root", str(tmp_path)])

    exit_code = main(["doctor", "--project-root", str(tmp_path)])

    assert exit_code == 0


def test_cli_coordinator_records_rule_update_and_report(tmp_path, capsys):
    main(["init", "--project-root", str(tmp_path)])

    exit_code = main(
        [
            "coordinator",
            "rule-update",
            "--project-root",
            str(tmp_path),
            "--project-id",
            "pipe",
            "--source",
            "qc",
            "--summary",
            "Need stricter entity boundary rule.",
            "--action",
            "Update annotation_rules.yaml before rerun.",
            "--task-id",
            "task-1",
        ]
    )
    record = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert record["project_id"] == "pipe"

    exit_code = main(["coordinator", "report", "--project-root", str(tmp_path), "--project-id", "pipe"])
    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["rule_updates"][0]["summary"] == "Need stricter entity boundary rule."


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

    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
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

    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
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


def test_cli_create_batched_jsonl_tasks_with_qc_sample_count(tmp_path):
    main(["init", "--project-root", str(tmp_path)])
    source = tmp_path / "input.jsonl"
    source.write_text(
        "\n".join(json.dumps({"text": f"row {index}"}) for index in range(1, 6)) + "\n",
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
            "sample-count",
            "--batch-size",
            "5",
            "--qc-sample-count",
            "2",
        ]
    )

    task = SqliteStore.open(tmp_path / ".annotation-pipeline").load_task("sample-count-000001")
    assert exit_code == 0
    assert task.metadata["qc_policy"] == {
        "mode": "sample_count",
        "row_count": 5,
        "requested_sample_count": 2,
        "sample_count": 2,
        "required_correct_rows": 2,
        "sample_scope": "per_task",
        "selection": "deterministic_from_task_payload_order",
        "feedback_loop": "annotator_may_accept_or_dispute_qc_items",
    }


def test_cli_create_batched_jsonl_tasks_with_qc_sample_ratio(tmp_path):
    main(["init", "--project-root", str(tmp_path)])
    source = tmp_path / "input.jsonl"
    source.write_text(
        "\n".join(json.dumps({"text": f"row {index}"}) for index in range(1, 6)) + "\n",
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
            "sample-ratio",
            "--batch-size",
            "5",
            "--qc-sample-ratio",
            "0.4",
        ]
    )

    task = SqliteStore.open(tmp_path / ".annotation-pipeline").load_task("sample-ratio-000001")
    assert exit_code == 0
    assert task.metadata["qc_policy"]["mode"] == "sample_ratio"
    assert task.metadata["qc_policy"]["sample_ratio"] == 0.4
    assert task.metadata["qc_policy"]["sample_count"] == 2
    assert task.metadata["qc_policy"]["required_correct_rows"] == 2


def test_cli_create_tasks_rejects_conflicting_qc_sample_options(tmp_path):
    main(["init", "--project-root", str(tmp_path)])
    source = tmp_path / "input.jsonl"
    source.write_text(json.dumps({"text": "alpha"}) + "\n", encoding="utf-8")

    try:
        main(
            [
                "create-tasks",
                "--project-root",
                str(tmp_path),
                "--source",
                str(source),
                "--pipeline-id",
                "bad",
                "--qc-sample-count",
                "1",
                "--qc-sample-ratio",
                "0.5",
            ]
        )
    except ValueError as exc:
        assert str(exc) == "--qc-sample-count and --qc-sample-ratio are mutually exclusive"
    else:
        raise AssertionError("expected conflicting QC sample options to fail")


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

    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    tasks = store.list_tasks()
    assert exit_code == 0
    assert [task.source_ref["row_count"] for task in tasks] == [2, 1, 2]
    assert [task.metadata["sources"] for task in tasks] == [["a"], ["a"], ["b"]]


def test_cli_import_annotation_manager_v2_queues_imported_annotations_for_qc(tmp_path, capsys):
    from annotation_pipeline_skill.core.states import TaskStatus

    main(["init", "--project-root", str(tmp_path)])
    source_root = tmp_path / "manager-v2" / "tasks"
    source_root.mkdir(parents=True)
    output_file = source_root / "legacy_task_001.annotated.jsonl"
    output_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "input": "Repo: nodejs/node\nReviewed-By: Ada Lovelace",
                        "output": {
                            "entities": {"organization": ["nodejs"], "person": ["Ada Lovelace"]},
                            "classifications": [],
                            "json_structures": [],
                            "relations": [],
                        },
                        "source_dataset": "github",
                        "source_path": "github.jsonl",
                    }
                ),
                json.dumps({"input": "missing output"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    task_file = source_root / "legacy_task_001.task.json"
    task_file.write_text(
        json.dumps(
            {
                "task_id": "legacy_task_001",
                "status": "merged",
                "output_file": str(output_file),
            }
        ),
        encoding="utf-8",
    )
    missing_output_task = source_root / "legacy_task_002.task.json"
    missing_output_task.write_text(
        json.dumps({"task_id": "legacy_task_002", "status": "merged", "output_file": ""}),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "import",
            "annotation-manager-v2",
            "--project-root",
            str(tmp_path),
            "--source-task-root",
            str(source_root),
            "--pipeline-id",
            "memory-ner-v2",
            "--task-prefix",
            "memory-ner-v2-review",
            "--qc-sample-count",
            "1",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    task = store.load_task("memory-ner-v2-review-000001")
    artifacts = store.list_artifacts(task.task_id)
    attempts = store.list_attempts(task.task_id)
    artifact_payload = json.loads((store.root / artifacts[0].path).read_text(encoding="utf-8"))
    events = store.list_events(task.task_id)
    assert exit_code == 0
    assert payload == {"imported": 1, "pipeline_id": "memory-ner-v2", "skipped": 1}
    assert task.status is TaskStatus.QC
    assert task.current_attempt == 1
    assert task.metadata["runtime_next_stage"] == "qc"
    assert task.metadata["source_task_id"] == "legacy_task_001"
    assert task.metadata["qc_policy"]["mode"] == "sample_count"
    assert task.source_ref["kind"] == "annotation_manager_v2"
    assert task.source_ref["payload"]["rows"][0]["text"].startswith("Repo: nodejs/node")
    assert [event.next_status.value for event in events] == ["pending", "annotating", "validating", "qc"]
    assert attempts[0].provider_id == "annotation_manager_v2"
    assert artifacts[0].kind == "annotation_result"
    assert artifact_payload["imported_annotation"]["rows"][0]["output"]["entities"]["person"] == ["Ada Lovelace"]
    assert json.loads(artifact_payload["text"])["rows"][0]["output"]["entities"]["organization"] == ["nodejs"]


def test_cli_export_training_data_writes_manifest(tmp_path, capsys):
    from annotation_pipeline_skill.core.models import ArtifactRef
    from annotation_pipeline_skill.core.states import TaskStatus

    main(["init", "--project-root", str(tmp_path)])
    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
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
    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)

    exit_code = main(["report", "readiness", "--project-root", str(tmp_path), "--project-id", "pipe"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["project_id"] == "pipe"
    assert payload["accepted_count"] == 1
    assert payload["recommended_next_action"] == "fix_export_blockers"


def test_cli_outbox_status_reports_counts(tmp_path, capsys):
    from annotation_pipeline_skill.core.models import OutboxRecord
    from annotation_pipeline_skill.core.states import OutboxKind

    main(["init", "--project-root", str(tmp_path)])
    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
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
    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
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


def test_cli_external_pull_uses_configured_http_source(tmp_path, capsys):
    main(["init", "--project-root", str(tmp_path)])
    config_path = tmp_path / ".annotation-pipeline" / "external_tasks.yaml"
    with external_pull_server({"tasks": [{"external_task_id": "ext-1", "payload": {"text": "alpha"}}]}) as pull_url:
        config_path.write_text(
            "\n".join(
                [
                    "external_tasks:",
                    "  default:",
                    "    enabled: true",
                    "    system_id: vendor",
                    f"    pull_url: {pull_url}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        exit_code = main(
            [
                "external",
                "pull",
                "--project-root",
                str(tmp_path),
                "--project-id",
                "pipe",
                "--source-id",
                "default",
                "--limit",
                "1",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    assert exit_code == 0
    assert payload["created"] == 1
    assert store.list_tasks()[0].pipeline_id == "pipe"


def test_cli_db_init_creates_db(tmp_path, monkeypatch):
    from annotation_pipeline_skill.interfaces.cli import main
    monkeypatch.chdir(tmp_path)

    rc = main(["db", "init", "--root", str(tmp_path / "ws")])

    assert rc == 0
    assert (tmp_path / "ws" / "db.sqlite").exists()


def test_cli_db_backup_creates_snapshot(tmp_path):
    from annotation_pipeline_skill.interfaces.cli import main
    rc = main(["db", "init", "--root", str(tmp_path / "ws")])
    assert rc == 0

    rc = main(["db", "backup", "--root", str(tmp_path / "ws")])
    assert rc == 0
    snaps = list((tmp_path / "ws" / "backups").glob("sqlite-*.sqlite"))
    assert len(snaps) == 1


def test_cli_db_dump_json_round_trips(tmp_path):
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.interfaces.cli import main
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore

    rc = main(["db", "init", "--root", str(tmp_path / "ws")])
    assert rc == 0
    store = SqliteStore.open(tmp_path / "ws")
    store.save_task(Task.new(task_id="t-1", pipeline_id="p", source_ref={}))
    store.close()

    rc = main(["db", "dump-json",
               "--root", str(tmp_path / "ws"),
               "--out", str(tmp_path / "out")])
    assert rc == 0
    assert (tmp_path / "out" / "tasks" / "t-1.json").exists()


def test_cli_db_status_prints_counts(tmp_path, capsys):
    from annotation_pipeline_skill.interfaces.cli import main
    rc = main(["db", "init", "--root", str(tmp_path / "ws")])
    assert rc == 0

    rc = main(["db", "status", "--root", str(tmp_path / "ws")])
    assert rc == 0
    captured = capsys.readouterr()
    assert "tasks: 0" in captured.out


def test_cli_human_review_correct_accepts_answer_file(tmp_path):
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus

    root = tmp_path / "ws"
    rc = main(["db", "init", "--root", str(root)])
    assert rc == 0
    store = SqliteStore.open(root)
    task = Task.new(
        task_id="t-cli-hr",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": "x",
                "annotation_guidance": {
                    "output_schema": {
                        "type": "object",
                        "required": ["entities"],
                        "properties": {"entities": {"type": "array"}},
                    }
                },
            },
        },
    )
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)
    store.close()

    answer_path = tmp_path / "answer.json"
    answer_path.write_text(json.dumps({"entities": []}), encoding="utf-8")

    rc = main([
        "human-review", "correct",
        "--root", str(root),
        "--task", "t-cli-hr",
        "--answer-file", str(answer_path),
        "--actor", "reviewer-1",
    ])
    assert rc == 0
    store = SqliteStore.open(root)
    assert store.load_task("t-cli-hr").status is TaskStatus.ACCEPTED


def test_cli_human_review_correct_returns_nonzero_on_schema_fail(tmp_path):
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus

    root = tmp_path / "ws"
    main(["db", "init", "--root", str(root)])
    store = SqliteStore.open(root)
    task = Task.new(
        task_id="t-cli-bad",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {"annotation_guidance": {"output_schema": {"type": "object", "required": ["entities"]}}},
        },
    )
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)
    store.close()

    answer_path = tmp_path / "bad.json"
    answer_path.write_text(json.dumps({"wrong": []}), encoding="utf-8")

    rc = main([
        "human-review", "correct",
        "--root", str(root),
        "--task", "t-cli-bad",
        "--answer-file", str(answer_path),
        "--actor", "r",
    ])
    assert rc != 0


def test_default_llm_profiles_template_covers_memory_ner_models():
    """The init template should include profiles for all models in memory-ner/providers.yaml."""
    import yaml
    from annotation_pipeline_skill.interfaces.cli import CONFIG_FILES

    template = yaml.safe_load(CONFIG_FILES["llm_profiles.yaml"])
    profile_models = {p["model"] for p in template["profiles"].values()}
    # codex
    assert "gpt-5.4" in profile_models
    assert "gpt-5.4-mini" in profile_models
    # claude (CLI shortnames matching memory-ner)
    assert "sonnet" in profile_models or "claude-sonnet-4-6" in profile_models
    # deepseek
    assert "deepseek-v4-flash" in profile_models
    assert "deepseek-v4-pro" in profile_models
    assert "deepseek-chat" in profile_models
    # glm
    assert "glm-4-flash" in profile_models
    assert "glm-4.5-air" in profile_models
    assert "glm-4.6" in profile_models
    assert "glm-5.1" in profile_models
    # minimax
    assert "MiniMax-M2.7" in profile_models


def test_default_llm_profiles_glm_coding_models_use_coding_endpoint_with_fallback_key():
    """glm-4.5-air, glm-4.6, glm-5.1 must use the coding endpoint + GLM_CODING_API_KEY fallback chain."""
    import yaml
    from annotation_pipeline_skill.interfaces.cli import CONFIG_FILES

    template = yaml.safe_load(CONFIG_FILES["llm_profiles.yaml"])
    coding_models = {"glm-4.5-air", "glm-4.6", "glm-5.1"}
    for profile_name, profile in template["profiles"].items():
        if profile.get("model") in coding_models:
            assert profile["base_url"] == "https://open.bigmodel.cn/api/coding/paas/v4", (
                f"{profile_name} should use coding endpoint, got {profile.get('base_url')}"
            )
            key_env = profile["api_key_env"]
            # Must be a list and include GLM_CODING_API_KEY first, BIGMODEL_MCP_API_KEY second
            assert isinstance(key_env, list), f"{profile_name} api_key_env should be a list"
            assert key_env[0] == "GLM_CODING_API_KEY", f"{profile_name} primary env must be GLM_CODING_API_KEY"
            assert "BIGMODEL_MCP_API_KEY" in key_env, f"{profile_name} should include BIGMODEL_MCP_API_KEY fallback"


def test_default_llm_profiles_glm_non_coding_models_use_public_endpoint():
    import yaml
    from annotation_pipeline_skill.interfaces.cli import CONFIG_FILES

    template = yaml.safe_load(CONFIG_FILES["llm_profiles.yaml"])
    for profile_name, profile in template["profiles"].items():
        if profile.get("model") == "glm-4-flash":
            assert profile["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
            return
    raise AssertionError("template missing glm-4-flash profile")


def test_default_llm_profiles_template_is_valid_yaml_and_registry():
    """Template must load cleanly through load_llm_registry."""
    from annotation_pipeline_skill.interfaces.cli import CONFIG_FILES
    from annotation_pipeline_skill.llm.profiles import load_llm_registry

    # Round-trip through file because load_llm_registry expects a path
    import tempfile
    from pathlib import Path
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(CONFIG_FILES["llm_profiles.yaml"])
        path = Path(f.name)
    try:
        registry = load_llm_registry(path)
        # All declared targets must resolve to existing profiles.
        for target in registry.targets:
            registry.resolve(target)
    finally:
        path.unlink(missing_ok=True)


def _write_prelabeled_fixture(tmp_path, row_count):
    source = tmp_path / "v3_tasks.jsonl"
    lines = []
    for i in range(row_count):
        lines.append(
            json.dumps(
                {
                    "task_id": f"row-{i:03d}",
                    "source_id": f"src-{i}",
                    "input": f"text body {i}",
                    "output": {"labels": [{"text": f"e{i}", "type": "ENTITY"}]},
                }
            )
        )
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return source


def _write_minimal_schema_file(tmp_path):
    """Write a small JSON Schema with $defs/output that contains a $ref to a spanList."""
    schema = {
        "$defs": {
            "spanList": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["text", "type"],
                    "properties": {
                        "text": {"type": "string"},
                        "type": {"type": "string"},
                    },
                },
            },
            "output": {
                "type": "object",
                "required": ["labels"],
                "properties": {"labels": {"$ref": "#/$defs/spanList"}},
            },
        }
    }
    schema_file = tmp_path / "schema.json"
    schema_file.write_text(json.dumps(schema), encoding="utf-8")
    return schema_file


def test_cli_import_jsonl_prelabeled_creates_tasks_with_prelabel_metadata(tmp_path, capsys):
    from annotation_pipeline_skill.core.states import TaskStatus

    main(["init", "--project-root", str(tmp_path)])
    source = _write_prelabeled_fixture(tmp_path, row_count=15)
    schema_file = _write_minimal_schema_file(tmp_path)

    exit_code = main(
        [
            "import",
            "jsonl-prelabeled",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--pipeline-id",
            "v3_initial_deployment",
            "--batch-size",
            "10",
            "--output-schema-file",
            str(schema_file),
            "--output-schema-pointer",
            "$defs/output",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {"imported": 2, "pipeline_id": "v3_initial_deployment", "skipped": 0}

    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    task0 = store.load_task("v3_initial_deployment-000000")
    task1 = store.load_task("v3_initial_deployment-000001")
    assert task0.status is TaskStatus.PENDING
    assert task1.status is TaskStatus.PENDING
    assert task0.current_attempt == 0
    assert task0.metadata["prelabeled"] is True
    assert task0.metadata["batch_size"] == 10
    assert task1.metadata["batch_size"] == 5
    assert len(task0.metadata["row_ids"]) == 10
    assert len(task1.metadata["row_ids"]) == 5

    rows0 = task0.source_ref["payload"]["rows"]
    assert len(rows0) == 10
    assert rows0[0] == {
        "row_index": 0,
        "row_id": "row-000",
        "source_id": "src-0",
        "input": "text body 0",
    }
    guidance = task0.source_ref["payload"]["annotation_guidance"]
    assert "output_schema" in guidance
    batched = guidance["output_schema"]
    assert batched["properties"]["rows"]["minItems"] == 10
    assert batched["properties"]["rows"]["maxItems"] == 10
    # $defs hoisted to root of batched schema so $refs in per-row output resolve.
    assert "$defs" in batched
    assert "spanList" in batched["$defs"]
    item_output = batched["properties"]["rows"]["items"]["properties"]["output"]
    assert "$defs" not in item_output  # do not duplicate at the nested level

    # End-to-end: validating the actual annotation artifact against the batched schema must pass.
    from jsonschema import Draft202012Validator
    artifact = store.list_artifacts(task0.task_id)[0]
    artifact_payload = json.loads((store.root / artifact.path).read_text(encoding="utf-8"))
    inner = json.loads(artifact_payload["text"])
    errors = list(Draft202012Validator(batched).iter_errors(inner))
    assert errors == [], f"expected no validation errors, got {[e.message for e in errors]}"


def test_cli_import_jsonl_prelabeled_writes_annotation_artifact(tmp_path):
    main(["init", "--project-root", str(tmp_path)])
    source = _write_prelabeled_fixture(tmp_path, row_count=3)
    schema_file = _write_minimal_schema_file(tmp_path)

    main(
        [
            "import",
            "jsonl-prelabeled",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--pipeline-id",
            "v3",
            "--batch-size",
            "10",
            "--output-schema-file",
            str(schema_file),
        ]
    )

    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    artifacts = store.list_artifacts("v3-000000")
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.kind == "annotation_result"
    artifact_payload = json.loads((store.root / artifact.path).read_text(encoding="utf-8"))
    assert artifact_payload["raw_response"] == {"source": "v2_prelabel"}
    inner = json.loads(artifact_payload["text"])
    assert len(inner["rows"]) == 3
    assert inner["rows"][0]["row_index"] == 0
    assert inner["rows"][0]["row_id"] == "row-000"
    assert inner["rows"][0]["output"] == {"labels": [{"text": "e0", "type": "ENTITY"}]}


def test_cli_import_jsonl_prelabeled_appends_attempt_record(tmp_path):
    from annotation_pipeline_skill.core.states import AttemptStatus

    main(["init", "--project-root", str(tmp_path)])
    source = _write_prelabeled_fixture(tmp_path, row_count=12)
    schema_file = _write_minimal_schema_file(tmp_path)

    main(
        [
            "import",
            "jsonl-prelabeled",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--pipeline-id",
            "v3",
            "--batch-size",
            "10",
            "--output-schema-file",
            str(schema_file),
        ]
    )

    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    for task_id in ("v3-000000", "v3-000001"):
        attempts = store.list_attempts(task_id)
        assert len(attempts) == 1
        attempt = attempts[0]
        assert attempt.status is AttemptStatus.SUCCEEDED
        assert attempt.provider_id == "prelabel"
        assert attempt.stage == "annotation"
        assert attempt.summary == "imported from v2 annotation"
        # attempt_id must be scoped by task_id so subsequent pipelines / re-runs
        # don't collide on the globally-unique attempts.attempt_id primary key.
        assert attempt.attempt_id == f"{task_id}-attempt-0-prelabel"


def test_cli_import_jsonl_prelabeled_two_pipelines_no_attempt_id_collision(tmp_path):
    """Importing two pipelines with overlapping batch indices must succeed.

    Regression test: previously attempt_id was `attempt-prelabel-{batch_idx}`
    which collided whenever batch_idx repeated across imports.
    """
    main(["init", "--project-root", str(tmp_path)])
    source = _write_prelabeled_fixture(tmp_path, row_count=10)
    schema_file = _write_minimal_schema_file(tmp_path)

    for pipeline in ("pipeline_a", "pipeline_b"):
        rc = main(
            [
                "import",
                "jsonl-prelabeled",
                "--project-root",
                str(tmp_path),
                "--source",
                str(source),
                "--pipeline-id",
                pipeline,
                "--batch-size",
                "10",
                "--output-schema-file",
                str(schema_file),
            ]
        )
        assert rc == 0

    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    # Both pipelines created their batch 0 task successfully — no collision.
    assert store.load_task("pipeline_a-000000").pipeline_id == "pipeline_a"
    assert store.load_task("pipeline_b-000000").pipeline_id == "pipeline_b"
    a_attempts = store.list_attempts("pipeline_a-000000")
    b_attempts = store.list_attempts("pipeline_b-000000")
    assert len(a_attempts) == 1 and len(b_attempts) == 1
    assert a_attempts[0].attempt_id != b_attempts[0].attempt_id


def test_cli_pipeline_delete_preview_then_force(tmp_path, capsys):
    main(["init", "--project-root", str(tmp_path)])
    source = _write_prelabeled_fixture(tmp_path, row_count=3)
    schema_file = _write_minimal_schema_file(tmp_path)

    rc = main(
        [
            "import",
            "jsonl-prelabeled",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--pipeline-id",
            "v3",
            "--batch-size",
            "1",
            "--output-schema-file",
            str(schema_file),
        ]
    )
    assert rc == 0
    capsys.readouterr()  # drain import output

    store = SqliteStore.open(tmp_path / ".annotation-pipeline")
    pre_tasks = [t for t in store.list_tasks() if t.pipeline_id == "v3"]
    assert len(pre_tasks) == 3

    # Preview (no --force): tasks still present.
    rc = main(
        [
            "pipeline",
            "delete",
            "--project-root",
            str(tmp_path),
            "--pipeline-id",
            "v3",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert "would_delete" in payload
    assert payload["would_delete"]["tasks"] == 3
    store2 = SqliteStore.open(tmp_path / ".annotation-pipeline")
    assert len([t for t in store2.list_tasks() if t.pipeline_id == "v3"]) == 3

    # With --force: tasks gone.
    rc = main(
        [
            "pipeline",
            "delete",
            "--project-root",
            str(tmp_path),
            "--pipeline-id",
            "v3",
            "--force",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["deleted"]["tasks"] == 3

    store3 = SqliteStore.open(tmp_path / ".annotation-pipeline")
    assert [t for t in store3.list_tasks() if t.pipeline_id == "v3"] == []


def test_cli_pipeline_delete_nonexistent_returns_1(tmp_path, capsys):
    main(["init", "--project-root", str(tmp_path)])

    rc = main(
        [
            "pipeline",
            "delete",
            "--project-root",
            str(tmp_path),
            "--pipeline-id",
            "does-not-exist",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 1
    payload = json.loads(out)
    assert payload == {"error": "pipeline_not_found", "pipeline_id": "does-not-exist"}
