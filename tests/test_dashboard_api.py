import json

from annotation_pipeline_skill.core.models import ArtifactRef, Attempt, FeedbackRecord, Task
from annotation_pipeline_skill.core.states import AttemptStatus, FeedbackSeverity, FeedbackSource, TaskStatus
from annotation_pipeline_skill.core.transitions import transition_task
from annotation_pipeline_skill.interfaces.api import DashboardApi
from annotation_pipeline_skill.store.file_store import FileStore


def test_dashboard_api_returns_kanban_snapshot_json(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.READY
    store.save_task(task)
    api = DashboardApi(store)

    status, headers, body = api.handle_get("/api/kanban")

    assert status == 200
    assert headers["content-type"] == "application/json"
    payload = json.loads(body.decode("utf-8"))
    assert payload["columns"][0]["id"] == "ready"
    assert payload["columns"][0]["cards"][0]["task_id"] == "task-1"


def test_dashboard_api_returns_404_for_unknown_route(tmp_path):
    api = DashboardApi(FileStore(tmp_path))

    status, headers, body = api.handle_get("/api/missing")

    assert status == 404
    assert headers["content-type"] == "application/json"
    assert json.loads(body.decode("utf-8")) == {"error": "not_found"}


def test_dashboard_api_returns_task_detail_with_source_attempts_artifacts_events_and_feedback(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(
        task_id="task-1",
        pipeline_id="pipe",
        source_ref={"kind": "jsonl", "payload": {"text": "Alice joined OpenAI."}},
    )
    task.status = TaskStatus.READY
    event = transition_task(task, TaskStatus.ANNOTATING, actor="test", reason="started", stage="annotation")
    artifact = ArtifactRef.new(
        task_id="task-1",
        kind="annotation_result",
        path="artifact_payloads/task-1/annotation_result.json",
        content_type="application/json",
        metadata={"provider": "local_codex"},
    )
    payload_path = tmp_path / artifact.path
    payload_path.parent.mkdir(parents=True)
    payload_path.write_text('{"text":"{\\"entities\\":[{\\"text\\":\\"Alice\\"}]}"}\n', encoding="utf-8")
    attempt = Attempt(
        attempt_id="attempt-1",
        task_id="task-1",
        index=1,
        stage="annotation",
        status=AttemptStatus.SUCCEEDED,
        provider_id="local_codex",
        model="gpt-5.4-mini",
        artifacts=[artifact],
    )
    feedback = FeedbackRecord.new(
        task_id="task-1",
        attempt_id="attempt-1",
        source_stage=FeedbackSource.QC,
        severity=FeedbackSeverity.WARNING,
        category="boundary",
        message="Check entity span boundary.",
        target={"entity": "Alice"},
        suggested_action="manual_edit",
        created_by="qc",
    )
    store.save_task(task)
    store.append_event(event)
    store.append_artifact(artifact)
    store.append_attempt(attempt)
    store.append_feedback(feedback)
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/tasks/task-1")

    payload = json.loads(body.decode("utf-8"))
    assert status == 200
    assert payload["task"]["source_ref"]["payload"]["text"] == "Alice joined OpenAI."
    assert payload["attempts"][0]["provider_id"] == "local_codex"
    assert payload["artifacts"][0]["payload"]["text"] == '{"entities":[{"text":"Alice"}]}'
    assert payload["events"][0]["next_status"] == "annotating"
    assert payload["feedback"][0]["message"] == "Check entity span boundary."


def test_dashboard_api_returns_config_files_and_can_update_allowed_yaml(tmp_path):
    store = FileStore(tmp_path)
    (tmp_path / "annotation_rules.yaml").write_text("rules:\n  - id: default\n", encoding="utf-8")
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/config")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert any(item["id"] == "annotation_rules.yaml" for item in payload["files"])

    status, _headers, body = api.handle_put(
        "/api/config/annotation_rules.yaml",
        b"rules:\n  - id: updated\n    instruction: Label named entities.\n",
    )

    assert status == 200
    assert json.loads(body.decode("utf-8"))["ok"] is True
    assert "updated" in (tmp_path / "annotation_rules.yaml").read_text(encoding="utf-8")


def test_dashboard_api_rejects_invalid_config_name(tmp_path):
    api = DashboardApi(FileStore(tmp_path))

    status, _headers, body = api.handle_put("/api/config/../bad.yaml", b"ok: true\n")

    assert status == 404
    assert json.loads(body.decode("utf-8")) == {"error": "config_not_found"}


def test_dashboard_api_returns_event_log_across_tasks(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.READY
    event = transition_task(task, TaskStatus.ANNOTATING, actor="test", reason="started", stage="annotation")
    store.save_task(task)
    store.append_event(event)
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/events")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert payload["events"][0]["task_id"] == "task-1"
    assert payload["events"][0]["next_status"] == "annotating"
