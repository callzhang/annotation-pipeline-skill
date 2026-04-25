from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from annotation_pipeline_skill.services.dashboard_service import build_kanban_snapshot
from annotation_pipeline_skill.store.file_store import FileStore


class DashboardApi:
    def __init__(self, store: FileStore):
        self.store = store

    def handle_get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        route = path.split("?", 1)[0]
        if route == "/api/health":
            return self._json_response(200, {"ok": True})
        if route == "/api/kanban":
            return self._json_response(200, build_kanban_snapshot(self.store))
        if route.startswith("/api/tasks/"):
            task_id = route.removeprefix("/api/tasks/")
            if not task_id:
                return self._json_response(404, {"error": "not_found"})
            return self._task_detail_response(task_id)
        return self._json_response(404, {"error": "not_found"})

    def _json_response(self, status: int, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        return status, {"content-type": "application/json"}, body

    def _task_detail_response(self, task_id: str) -> tuple[int, dict[str, str], bytes]:
        try:
            task = self.store.load_task(task_id)
        except FileNotFoundError:
            return self._json_response(404, {"error": "task_not_found"})

        artifacts = [
            {**artifact.to_dict(), "payload": self._read_artifact_payload(artifact.path)}
            for artifact in self.store.list_artifacts(task_id)
        ]
        return self._json_response(
            200,
            {
                "task": task.to_dict(),
                "attempts": [attempt.to_dict() for attempt in self.store.list_attempts(task_id)],
                "artifacts": artifacts,
                "events": [event.to_dict() for event in self.store.list_events(task_id)],
                "feedback": [feedback.to_dict() for feedback in self.store.list_feedback(task_id)],
            },
        )

    def _read_artifact_payload(self, relative_path: str) -> Any:
        path = self.store.root / relative_path
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


def make_handler(api: DashboardApi) -> type[BaseHTTPRequestHandler]:
    class DashboardRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, headers, body = api.handle_get(self.path)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return DashboardRequestHandler


def serve_dashboard_api(store: FileStore, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(DashboardApi(store)))
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local annotation dashboard API.")
    parser.add_argument("store_root", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve_dashboard_api(FileStore(args.store_root), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
