import json

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
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
