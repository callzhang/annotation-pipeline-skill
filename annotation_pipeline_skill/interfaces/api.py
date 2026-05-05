from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import yaml

from annotation_pipeline_skill.core.models import FeedbackDiscussionEntry
from annotation_pipeline_skill.core.runtime import RuntimeConfig, RuntimeSnapshot
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.core.transitions import transition_task
from annotation_pipeline_skill.services.feedback_service import build_feedback_consensus_summary
from annotation_pipeline_skill.services.dashboard_service import build_kanban_snapshot, build_project_summaries
from annotation_pipeline_skill.services.outbox_dispatch_service import build_outbox_summary
from annotation_pipeline_skill.runtime.monitor import validate_runtime_snapshot
from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot
from annotation_pipeline_skill.services.provider_config_service import build_provider_config_snapshot, save_provider_config
from annotation_pipeline_skill.services.readiness_service import build_readiness_report
from annotation_pipeline_skill.store.file_store import FileStore
from annotation_pipeline_skill.llm.profiles import ProfileValidationError


CONFIG_FILE_DEFINITIONS: dict[str, str] = {
    "annotation_rules.yaml": "Annotation Rules",
    "annotators.yaml": "Annotation Agents",
    "llm_profiles.yaml": "Subagent Providers",
    "workflow.yaml": "Workflow",
    "external_tasks.yaml": "External Task API",
    "callbacks.yaml": "Callbacks",
}


class DashboardApi:
    def __init__(
        self,
        store: FileStore,
        *,
        runtime_once: Callable[[], RuntimeSnapshot] | None = None,
        runtime_config: RuntimeConfig | None = None,
    ):
        self.store = store
        self.runtime_once = runtime_once
        self.runtime_config = runtime_config or RuntimeConfig()

    def handle_get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        parsed_path = urlparse(path)
        route = parsed_path.path
        query = parse_qs(parsed_path.query)
        project_id = query.get("project", [None])[0]
        if route == "/api/health":
            return self._json_response(200, {"ok": True})
        if route == "/api/projects":
            return self._json_response(200, build_project_summaries(self.store))
        if route == "/api/kanban":
            return self._json_response(200, build_kanban_snapshot(self.store, project_id=project_id))
        if route == "/api/config":
            return self._json_response(200, {"files": self._config_files()})
        if route == "/api/providers":
            return self._provider_config_response()
        if route == "/api/events":
            return self._json_response(200, {"events": self._event_log(project_id=project_id)})
        if route == "/api/readiness":
            if not project_id:
                return self._json_response(400, {"error": "project_required"})
            return self._json_response(200, build_readiness_report(self.store, project_id))
        if route == "/api/outbox":
            return self._json_response(200, build_outbox_summary(self.store))
        if route == "/api/runtime":
            return self._json_response(200, self._runtime_snapshot().to_dict())
        if route == "/api/runtime/monitor":
            return self._json_response(200, validate_runtime_snapshot(self._runtime_snapshot()))
        if route == "/api/runtime/cycles":
            return self._json_response(
                200,
                {"cycles": [stats.to_dict() for stats in self.store.list_runtime_cycle_stats()]},
            )
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
        if route == "/api/providers":
            return self._update_provider_config_response(body)
        return self._json_response(404, {"error": "not_found"})

    def handle_post(self, path: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        route = path.split("?", 1)[0]
        if route == "/api/runtime/run-once":
            return self._runtime_run_once_response()
        if route.startswith("/api/tasks/") and route.endswith("/feedback-discussions"):
            task_id = route.removeprefix("/api/tasks/").removesuffix("/feedback-discussions").strip("/")
            return self._post_feedback_discussion_response(task_id, body)
        return self._json_response(404, {"error": "not_found"})

    def _runtime_snapshot(self) -> RuntimeSnapshot:
        return self.store.load_runtime_snapshot() or build_runtime_snapshot(self.store, self.runtime_config)

    def _runtime_run_once_response(self) -> tuple[int, dict[str, str], bytes]:
        if self.runtime_once is None:
            return self._json_response(409, {"error": "runtime_runner_unavailable"})
        snapshot = self.runtime_once()
        return self._json_response(200, {"ok": True, "snapshot": snapshot.to_dict()})

    def _provider_config_response(self) -> tuple[int, dict[str, str], bytes]:
        try:
            return self._json_response(200, build_provider_config_snapshot(self.store.root))
        except (OSError, ProfileValidationError) as exc:
            return self._json_response(
                400,
                {
                    "config_valid": False,
                    "error": "invalid_provider_config",
                    "detail": str(exc),
                },
            )

    def _update_provider_config_response(self, body: bytes) -> tuple[int, dict[str, str], bytes]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            return self._json_response(400, {"error": "invalid_json", "detail": str(exc)})
        if not isinstance(payload, dict):
            return self._json_response(400, {"error": "invalid_payload"})
        try:
            snapshot = save_provider_config(self.store.root, payload)
        except (OSError, ProfileValidationError) as exc:
            return self._json_response(400, {"error": "invalid_provider_config", "detail": str(exc)})
        return self._json_response(200, snapshot)

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
                "feedback_discussions": [
                    entry.to_dict()
                    for entry in self.store.list_feedback_discussions(task_id)
                ],
                "feedback_consensus": build_feedback_consensus_summary(self.store, task_id),
            },
        )

    def _post_feedback_discussion_response(self, task_id: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        try:
            task = self.store.load_task(task_id)
        except FileNotFoundError:
            return self._json_response(404, {"error": "task_not_found"})
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            return self._json_response(400, {"error": "invalid_json", "detail": str(exc)})
        if not isinstance(payload, dict):
            return self._json_response(400, {"error": "invalid_payload"})

        feedback_id = str(payload.get("feedback_id") or "")
        if feedback_id not in {feedback.feedback_id for feedback in self.store.list_feedback(task_id)}:
            return self._json_response(400, {"error": "unknown_feedback_id"})
        entry = FeedbackDiscussionEntry.new(
            task_id=task_id,
            feedback_id=feedback_id,
            role=str(payload.get("role") or "annotator"),
            stance=str(payload.get("stance") or "comment"),
            message=str(payload.get("message") or ""),
            agreed_points=list(payload.get("agreed_points") or []),
            disputed_points=list(payload.get("disputed_points") or []),
            proposed_resolution=payload.get("proposed_resolution"),
            consensus=bool(payload.get("consensus", False)),
            created_by=str(payload.get("created_by") or payload.get("role") or "unknown"),
            metadata=dict(payload.get("metadata") or {}),
        )
        self.store.append_feedback_discussion(entry)

        consensus = build_feedback_consensus_summary(self.store, task_id)
        if consensus["can_accept_by_consensus"] and task.status in {TaskStatus.QC, TaskStatus.HUMAN_REVIEW}:
            event = transition_task(
                task,
                TaskStatus.ACCEPTED,
                actor=entry.created_by,
                reason="feedback consensus accepted by annotator and qc",
                stage="qc",
                metadata={"feedback_id": feedback_id, "discussion_entry_id": entry.entry_id},
            )
            self.store.append_event(event)
            self.store.save_task(task)

        return self._json_response(
            200,
            {
                "entry": entry.to_dict(),
                "feedback_consensus": consensus,
                "task": self.store.load_task(task_id).to_dict(),
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

    def _event_log(self, project_id: str | None = None) -> list[dict[str, Any]]:
        events = []
        for task in self.store.list_tasks():
            if project_id is not None and task.pipeline_id != project_id:
                continue
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

        def do_POST(self) -> None:
            content_length = int(self.headers.get("content-length", "0"))
            request_body = self.rfile.read(content_length)
            status, headers, body = api.handle_post(self.path, request_body)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return DashboardRequestHandler


def serve_dashboard_api(
    store: FileStore,
    host: str,
    port: int,
    *,
    runtime_once: Callable[[], RuntimeSnapshot] | None = None,
    runtime_config: RuntimeConfig | None = None,
) -> None:
    server = ThreadingHTTPServer(
        (host, port),
        make_handler(DashboardApi(store, runtime_once=runtime_once, runtime_config=runtime_config)),
    )
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
