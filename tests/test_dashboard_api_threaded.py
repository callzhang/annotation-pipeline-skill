import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.interfaces.api import DashboardApi, make_handler
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_threaded_http_server_serves_requests(tmp_path):
    """Regression test for sqlite cross-thread access in the live dashboard server.

    Before the per-thread connection refactor, hitting any DB-backed endpoint
    via the ThreadingHTTPServer raised
    ``sqlite3.ProgrammingError: SQLite objects created in a thread can only be
    used in that same thread``. We exercise the real socket so that the
    handler runs on a worker thread, not on the test thread.
    """
    store = SqliteStore.open(tmp_path)
    store.save_task(Task.new(task_id="t-1", pipeline_id="p", source_ref={}))

    # Construct the same threaded server used by `apl serve` (see
    # `serve_dashboard_api` in interfaces/api.py).
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(DashboardApi(store), static_root=None),
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # /api/kanban does store.list_tasks() under the hood; before the fix
        # this would 500 with a ProgrammingError because the worker thread
        # cannot use the connection opened on the main thread.
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/kanban", timeout=5) as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode("utf-8"))
            assert "columns" in body

        # /api/projects iterates all tasks; the project pipeline_id should be
        # visible to the worker-thread connection.
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/projects", timeout=5) as resp:
            assert resp.status == 200
            body = resp.read()
            assert b"\"p\"" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
