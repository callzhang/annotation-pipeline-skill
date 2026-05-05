import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from annotation_pipeline_skill.core.states import OutboxKind, TaskStatus
from annotation_pipeline_skill.services.external_task_service import ExternalTaskService
from annotation_pipeline_skill.store.file_store import FileStore


@contextmanager
def pull_server(response_payload: dict):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            requests.append({"path": self.path, "body": json.loads(body.decode("utf-8"))})
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
        yield f"http://127.0.0.1:{server.server_port}/pull", requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_external_http_pull_creates_pending_tasks_status_outbox_and_events(tmp_path):
    store = FileStore(tmp_path)
    with pull_server(
        {
            "tasks": [
                {"external_task_id": "ext-1", "payload": {"text": "alpha"}},
                {"external_task_id": "ext-2", "payload": {"text": "beta"}},
            ]
        }
    ) as (pull_url, requests):
        result = ExternalTaskService(store).pull_http_tasks(
            pipeline_id="pipe",
            source_id="default",
            config={"enabled": True, "system_id": "vendor", "pull_url": pull_url},
            limit=2,
        )

    tasks = store.list_tasks()
    assert requests == [{"path": "/pull", "body": {"limit": 2}}]
    assert result["created"] == 2
    assert result["existing"] == 0
    assert [task.status for task in tasks] == [TaskStatus.PENDING, TaskStatus.PENDING]
    assert sorted(task.external_ref.external_task_id for task in tasks if task.external_ref) == ["ext-1", "ext-2"]
    assert [record.kind for record in store.list_outbox()] == [OutboxKind.STATUS, OutboxKind.STATUS]
    assert store.list_events(tasks[0].task_id)[0].reason == "created from external task pull"


def test_external_http_pull_is_idempotent_on_repeated_external_ids(tmp_path):
    store = FileStore(tmp_path)
    response = {"tasks": [{"external_task_id": "ext-1", "payload": {"text": "alpha"}}]}
    with pull_server(response) as (pull_url, _requests):
        first = ExternalTaskService(store).pull_http_tasks(
            pipeline_id="pipe",
            source_id="default",
            config={"enabled": True, "system_id": "vendor", "pull_url": pull_url},
            limit=1,
        )
        second = ExternalTaskService(store).pull_http_tasks(
            pipeline_id="pipe",
            source_id="default",
            config={"enabled": True, "system_id": "vendor", "pull_url": pull_url},
            limit=1,
        )

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["existing"] == 1
    assert len(store.list_tasks()) == 1
    assert len(store.list_outbox()) == 1
