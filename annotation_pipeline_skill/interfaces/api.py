from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

from annotation_pipeline_skill.services.dashboard_service import build_kanban_snapshot
from annotation_pipeline_skill.store.file_store import FileStore


CONFIG_FILE_DEFINITIONS: dict[str, str] = {
    "annotation_rules.yaml": "Annotation Rules",
    "annotators.yaml": "Annotation Agents",
    "llm_profiles.yaml": "Subagent Providers",
    "stage_routes.yaml": "Stage Routing",
    "providers.yaml": "Legacy Providers",
    "external_tasks.yaml": "External Task API",
    "callbacks.yaml": "Callbacks",
}


class DashboardApi:
    def __init__(self, store: FileStore):
        self.store = store

    def handle_get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        route = path.split("?", 1)[0]
        if route == "/api/health":
            return self._json_response(200, {"ok": True})
        if route == "/api/kanban":
            return self._json_response(200, build_kanban_snapshot(self.store))
        if route == "/api/config":
            return self._json_response(200, {"files": self._config_files()})
        if route == "/api/events":
            return self._json_response(200, {"events": self._event_log()})
        if route.startswith("/api/tasks/"):
            task_id = route.removeprefix("/api/tasks/")
            if not task_id:
                return self._json_response(404, {"error": "not_found"})
            return self._task_detail_response(task_id)
        return self._json_response(404, {"error": "not_found"})

    def handle_put(self, path: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        route = path.split("?", 1)[0]
        if route.startswith("/api/config/"):
            config_id = route.removeprefix("/api/config/")
            return self._update_config_response(config_id, body)
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

    def _config_files(self) -> list[dict[str, Any]]:
        files = []
        for config_id, title in CONFIG_FILE_DEFINITIONS.items():
            path = self.store.root / config_id
            files.append(
                {
                    "id": config_id,
                    "title": title,
                    "path": str(path),
                    "exists": path.exists(),
                    "content": path.read_text(encoding="utf-8") if path.exists() else "",
                }
            )
        return files

    def _update_config_response(self, config_id: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        if config_id not in CONFIG_FILE_DEFINITIONS:
            return self._json_response(404, {"error": "config_not_found"})
        content = body.decode("utf-8")
        try:
            yaml.safe_load(content) if content.strip() else None
        except yaml.YAMLError as exc:
            return self._json_response(400, {"error": "invalid_yaml", "detail": str(exc)})
        path = self.store.root / config_id
        path.write_text(content, encoding="utf-8")
        return self._json_response(200, {"ok": True, "id": config_id})

    def _event_log(self) -> list[dict[str, Any]]:
        events = []
        for task in self.store.list_tasks():
            events.extend(event.to_dict() for event in self.store.list_events(task.task_id))
        return sorted(events, key=lambda event: event["created_at"], reverse=True)


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

        def do_PUT(self) -> None:
            content_length = int(self.headers.get("content-length", "0"))
            request_body = self.rfile.read(content_length)
            status, headers, body = api.handle_put(self.path, request_body)
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
