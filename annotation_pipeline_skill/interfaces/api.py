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
        return self._json_response(404, {"error": "not_found"})

    def _json_response(self, status: int, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        return status, {"content-type": "application/json"}, body


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
