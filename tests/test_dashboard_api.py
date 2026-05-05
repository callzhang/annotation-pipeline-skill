import json

from annotation_pipeline_skill.core.models import ArtifactRef, Attempt, FeedbackDiscussionEntry, FeedbackRecord, Task
from annotation_pipeline_skill.core.states import AttemptStatus, FeedbackSeverity, FeedbackSource, TaskStatus
from annotation_pipeline_skill.core.transitions import transition_task
from annotation_pipeline_skill.interfaces.api import DashboardApi
from annotation_pipeline_skill.store.file_store import FileStore


def test_dashboard_api_returns_kanban_snapshot_json(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    api = DashboardApi(store)

    status, headers, body = api.handle_get("/api/kanban")

    assert status == 200
    assert headers["content-type"] == "application/json"
    payload = json.loads(body.decode("utf-8"))
    assert payload["columns"][0]["id"] == "pending"
    assert payload["columns"][0]["cards"][0]["task_id"] == "task-1"


def test_dashboard_api_returns_projects_and_filters_kanban_by_project(tmp_path):
    store = FileStore(tmp_path)
    alpha = Task.new(task_id="alpha-1", pipeline_id="project-alpha", source_ref={"kind": "jsonl"})
    beta = Task.new(task_id="beta-1", pipeline_id="project-beta", source_ref={"kind": "jsonl"})
    alpha.status = TaskStatus.PENDING
    beta.status = TaskStatus.PENDING
    store.save_task(alpha)
    store.save_task(beta)
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/projects")
    projects_payload = json.loads(body.decode("utf-8"))
    assert status == 200
    assert projects_payload["projects"] == [
        {"project_id": "project-alpha", "status_counts": {"pending": 1}, "task_count": 1},
        {"project_id": "project-beta", "status_counts": {"pending": 1}, "task_count": 1},
    ]

    status, _headers, body = api.handle_get("/api/kanban?project=project-alpha")
    kanban_payload = json.loads(body.decode("utf-8"))
    visible_task_ids = [
        card["task_id"]
        for column in kanban_payload["columns"]
        for card in column["cards"]
    ]
    assert status == 200
    assert kanban_payload["project_id"] == "project-alpha"
    assert visible_task_ids == ["alpha-1"]


def test_dashboard_api_returns_404_for_unknown_route(tmp_path):
    api = DashboardApi(FileStore(tmp_path))

    status, headers, body = api.handle_get("/api/missing")

    assert status == 404
    assert headers["content-type"] == "application/json"
    assert json.loads(body.decode("utf-8")) == {"error": "not_found"}


def test_dashboard_api_returns_runtime_snapshot(tmp_path):
    store = FileStore(tmp_path)
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/runtime")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert "runtime_status" in payload
    assert "queue_counts" in payload


def test_dashboard_api_returns_runtime_monitor_report(tmp_path):
    store = FileStore(tmp_path)
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/runtime/monitor")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert payload["ok"] is False
    assert payload["failures"] == ["runtime_unhealthy"]
    assert payload["details"]["runtime_unhealthy"]["errors"] == ["heartbeat_missing"]


def test_dashboard_api_runs_one_runtime_cycle_with_injected_runner(tmp_path):
    store = FileStore(tmp_path)
    called = {"count": 0}

    def run_once():
        called["count"] += 1
        from annotation_pipeline_skill.core.runtime import RuntimeConfig
        from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot

        snapshot = build_runtime_snapshot(store, RuntimeConfig())
        store.save_runtime_snapshot(snapshot)
        return snapshot

    api = DashboardApi(store, runtime_once=run_once)

    status, _headers, body = api.handle_post("/api/runtime/run-once", b"{}")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert called["count"] == 1
    assert payload["ok"] is True
    assert "runtime_status" in payload["snapshot"]


def test_dashboard_api_returns_409_when_runtime_runner_is_unavailable(tmp_path):
    api = DashboardApi(FileStore(tmp_path))

    status, _headers, body = api.handle_post("/api/runtime/run-once", b"{}")

    assert status == 409
    assert json.loads(body.decode("utf-8")) == {"error": "runtime_runner_unavailable"}


def test_dashboard_api_returns_runtime_cycles(tmp_path):
    from datetime import datetime, timezone
    from annotation_pipeline_skill.core.runtime import RuntimeCycleStats

    store = FileStore(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    store.append_runtime_cycle_stats(
        RuntimeCycleStats(
            cycle_id="cycle-1",
            started_at=now,
            finished_at=now,
            started=0,
            accepted=0,
            failed=0,
            capacity_available=4,
            errors=[],
        )
    )
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/runtime/cycles")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert payload["cycles"][0]["cycle_id"] == "cycle-1"


def test_dashboard_api_returns_readiness_report_for_project(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/readiness?project=pipe")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert payload["project_id"] == "pipe"
    assert payload["accepted_count"] == 1
    assert payload["recommended_next_action"] == "repair_export_blockers"


def test_dashboard_api_returns_outbox_summary(tmp_path):
    from annotation_pipeline_skill.core.models import OutboxRecord
    from annotation_pipeline_skill.core.states import OutboxKind

    store = FileStore(tmp_path)
    store.save_outbox(OutboxRecord.new(task_id="task-1", kind=OutboxKind.SUBMIT, payload={"result": {}}))
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/outbox")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert payload["counts"] == {"dead_letter": 0, "pending": 1, "sent": 0}
    assert payload["records"][0]["task_id"] == "task-1"


def test_dashboard_api_returns_task_detail_with_source_attempts_artifacts_events_and_feedback(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(
        task_id="task-1",
        pipeline_id="pipe",
        source_ref={"kind": "jsonl", "payload": {"text": "Alice joined OpenAI."}},
    )
    task.status = TaskStatus.PENDING
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
    store.append_feedback_discussion(
        FeedbackDiscussionEntry.new(
            task_id="task-1",
            feedback_id=feedback.feedback_id,
            role="annotator",
            stance="partial_agree",
            message="I agree with the boundary issue only.",
            created_by="annotator",
        )
    )
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/tasks/task-1")

    payload = json.loads(body.decode("utf-8"))
    assert status == 200
    assert payload["task"]["source_ref"]["payload"]["text"] == "Alice joined OpenAI."
    assert payload["attempts"][0]["provider_id"] == "local_codex"
    assert payload["artifacts"][0]["payload"]["text"] == '{"entities":[{"text":"Alice"}]}'
    assert payload["events"][0]["next_status"] == "annotating"
    assert payload["feedback"][0]["message"] == "Check entity span boundary."
    assert payload["feedback_discussions"][0]["stance"] == "partial_agree"
    assert payload["feedback_consensus"]["can_accept_by_consensus"] is False


def test_dashboard_api_posts_feedback_discussion_and_accepts_by_consensus(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.QC
    feedback = FeedbackRecord.new(
        task_id="task-1",
        attempt_id="attempt-1",
        source_stage=FeedbackSource.QC,
        severity=FeedbackSeverity.WARNING,
        category="span",
        message="Span boundary issue.",
        target={"entity": "OpenAI"},
        suggested_action="manual_annotation",
        created_by="qc",
    )
    store.save_task(task)
    store.append_feedback(feedback)
    api = DashboardApi(store)

    status, _headers, body = api.handle_post(
        "/api/tasks/task-1/feedback-discussions",
        json.dumps(
            {
                "feedback_id": feedback.feedback_id,
                "role": "qc",
                "stance": "agree",
                "message": "Annotator and QC agree on the final span.",
                "agreed_points": ["span corrected"],
                "consensus": True,
                "created_by": "qc",
            }
        ).encode("utf-8"),
    )

    payload = json.loads(body.decode("utf-8"))
    assert status == 200
    assert payload["feedback_consensus"]["can_accept_by_consensus"] is True
    assert store.load_task("task-1").status is TaskStatus.ACCEPTED
    assert store.list_events("task-1")[-1].reason == "feedback consensus accepted by annotator and qc"


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
    task.status = TaskStatus.PENDING
    event = transition_task(task, TaskStatus.ANNOTATING, actor="test", reason="started", stage="annotation")
    store.save_task(task)
    store.append_event(event)
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/events")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert payload["events"][0]["task_id"] == "task-1"
    assert payload["events"][0]["next_status"] == "annotating"


def test_dashboard_api_filters_event_log_by_project(tmp_path):
    store = FileStore(tmp_path)
    alpha = Task.new(task_id="alpha-1", pipeline_id="project-alpha", source_ref={"kind": "jsonl"})
    beta = Task.new(task_id="beta-1", pipeline_id="project-beta", source_ref={"kind": "jsonl"})
    alpha.status = TaskStatus.PENDING
    beta.status = TaskStatus.PENDING
    alpha_event = transition_task(alpha, TaskStatus.ANNOTATING, actor="test", reason="started", stage="annotation")
    beta_event = transition_task(beta, TaskStatus.ANNOTATING, actor="test", reason="started", stage="annotation")
    store.save_task(alpha)
    store.save_task(beta)
    store.append_event(alpha_event)
    store.append_event(beta_event)
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/events?project=project-beta")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert [event["task_id"] for event in payload["events"]] == ["beta-1"]
