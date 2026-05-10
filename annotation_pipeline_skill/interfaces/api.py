from __future__ import annotations

import argparse
import json
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import yaml

from annotation_pipeline_skill.core.models import AuditEvent, FeedbackDiscussionEntry, Task, utc_now
from annotation_pipeline_skill.core.qc_policy import build_qc_policy, validate_qc_sample_options
from annotation_pipeline_skill.core.runtime import RuntimeConfig, RuntimeSnapshot
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.core.transitions import InvalidTransition, transition_task
from annotation_pipeline_skill.services.coordinator_service import CoordinatorService
from annotation_pipeline_skill.services.feedback_service import build_feedback_consensus_summary
from annotation_pipeline_skill.services.dashboard_service import build_kanban_snapshot, build_project_summaries
from annotation_pipeline_skill.services.human_review_service import HumanReviewService
from annotation_pipeline_skill.services.outbox_dispatch_service import build_outbox_summary
from annotation_pipeline_skill.runtime.monitor import validate_runtime_snapshot
from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot
from annotation_pipeline_skill.services.provider_config_service import build_provider_config_snapshot, save_provider_config
from annotation_pipeline_skill.services.readiness_service import build_readiness_report
from annotation_pipeline_skill.store.sqlite_store import SqliteStore
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
        store: SqliteStore,
        *,
        stores: dict[str, SqliteStore] | None = None,
        default_store_key: str | None = None,
        runtime_once: Callable[[], RuntimeSnapshot] | None = None,
        runtime_config: RuntimeConfig | None = None,
    ):
        self.store = store
        self._stores = stores or {}
        self._default_store_key = default_store_key
        self.runtime_once = runtime_once
        self.runtime_config = runtime_config or RuntimeConfig()

    def _resolve_store(self, query: dict[str, list[str]]) -> SqliteStore:
        key = query.get("store", [None])[0]
        if key and key in self._stores:
            return self._stores[key]
        if self._default_store_key and self._default_store_key in self._stores:
            return self._stores[self._default_store_key]
        return self.store

    def handle_get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        parsed_path = urlparse(path)
        route = parsed_path.path
        query = parse_qs(parsed_path.query)
        store = self._resolve_store(query)
        project_id = query.get("project", [None])[0]
        stage_view = query.get("stage_view", ["internal"])[0]
        if route == "/api/health":
            return self._json_response(200, {"ok": True})
        if route == "/api/stores":
            return self._json_response(200, {"stores": self._stores_list()})
        if route == "/api/projects":
            return self._json_response(200, build_project_summaries(store))
        if route == "/api/kanban":
            return self._json_response(200, build_kanban_snapshot(store, project_id=project_id, stage_view=stage_view))
        if route == "/api/config":
            return self._json_response(200, {"files": self._config_files(store)})
        if route == "/api/providers":
            return self._provider_config_response(store)
        if route == "/api/coordinator":
            return self._json_response(200, CoordinatorService(store).build_report(project_id=project_id))
        if route == "/api/events":
            return self._json_response(200, {"events": self._event_log(store, project_id=project_id)})
        if route == "/api/readiness":
            if not project_id:
                return self._json_response(400, {"error": "project_required"})
            return self._json_response(200, build_readiness_report(store, project_id))
        if route == "/api/outbox":
            return self._json_response(200, build_outbox_summary(store, project_id=project_id))
        if route == "/api/runtime":
            return self._json_response(200, self._runtime_snapshot(store).to_dict())
        if route == "/api/runtime/monitor":
            return self._json_response(200, validate_runtime_snapshot(self._runtime_snapshot(store)))
        if route == "/api/runtime/cycles":
            return self._json_response(
                200,
                {"cycles": [stats.to_dict() for stats in store.list_runtime_cycle_stats()]},
            )
        if route == "/api/documents":
            return self._json_response(200, {"documents": [doc.to_dict() for doc in store.list_documents()]})
        if route.startswith("/api/documents/"):
            remainder = route.removeprefix("/api/documents/")
            parts = remainder.split("/")
            if len(parts) == 1 and parts[0]:
                return self._document_detail_response(store, parts[0])
            if len(parts) == 2 and parts[1] == "versions":
                return self._json_response(200, {"versions": [v.to_dict() for v in store.list_document_versions(parts[0])]})
            if len(parts) == 3 and parts[1] == "versions" and parts[2]:
                try:
                    ver = store.load_document_version(parts[2])
                except FileNotFoundError:
                    return self._json_response(404, {"error": "version_not_found"})
                return self._json_response(200, ver.to_dict())
        if route.startswith("/api/tasks/"):
            task_id = route.removeprefix("/api/tasks/")
            if not task_id:
                return self._json_response(404, {"error": "not_found"})
            return self._task_detail_response(store, task_id)
        return self._json_response(404, {"error": "not_found"})

    def handle_put(self, path: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        parsed_path = urlparse(path)
        route = parsed_path.path
        query = parse_qs(parsed_path.query)
        store = self._resolve_store(query)
        if route.startswith("/api/tasks/") and route.endswith("/qc-policy"):
            task_id = route.removeprefix("/api/tasks/").removesuffix("/qc-policy").strip("/")
            return self._update_task_qc_policy_response(store, task_id, body)
        if route.startswith("/api/config/"):
            config_id = route.removeprefix("/api/config/")
            return self._update_config_response(store, config_id, body)
        if route == "/api/providers":
            return self._update_provider_config_response(store, body)
        return self._json_response(404, {"error": "not_found"})

    def handle_post(self, path: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        parsed_path = urlparse(path)
        route = parsed_path.path
        query = parse_qs(parsed_path.query)
        store = self._resolve_store(query)
        if route == "/api/runtime/run-once":
            return self._runtime_run_once_response()
        if route == "/api/coordinator/rule-updates":
            return self._post_coordinator_rule_update_response(store, body)
        if route == "/api/coordinator/long-tail-issues":
            return self._post_coordinator_long_tail_issue_response(store, body)
        if route == "/api/documents":
            return self._post_document_response(store, body)
        if route.startswith("/api/documents/") and route.endswith("/versions"):
            doc_id = route.removeprefix("/api/documents/").removesuffix("/versions").strip("/")
            return self._post_document_version_response(store, doc_id, body)
        if route.startswith("/api/tasks/") and route.endswith("/human-review"):
            task_id = route.removeprefix("/api/tasks/").removesuffix("/human-review").strip("/")
            return self._post_human_review_response(store, task_id, body)
        if route.startswith("/api/tasks/") and route.endswith("/feedback-discussions"):
            task_id = route.removeprefix("/api/tasks/").removesuffix("/feedback-discussions").strip("/")
            return self._post_feedback_discussion_response(store, task_id, body)
        return self._json_response(404, {"error": "not_found"})

    def _stores_list(self) -> list[dict]:
        result = []
        for key, s in self._stores.items():
            result.append({
                "key": key,
                "name": s.root.parent.name,
                "path": str(s.root.parent),
                "pipeline_count": len({task.pipeline_id for task in s.list_tasks()}),
            })
        return result

    def _runtime_snapshot(self, store: SqliteStore) -> RuntimeSnapshot:
        snapshot = store.load_runtime_snapshot()
        if snapshot is not None:
            return snapshot
        rebuilt = build_runtime_snapshot(store, self.runtime_config)
        status = replace(
            rebuilt.runtime_status,
            healthy=False,
            active=False,
            errors=sorted(set([*rebuilt.runtime_status.errors, "runtime_snapshot_missing"])),
        )
        return replace(rebuilt, runtime_status=status)

    def _runtime_run_once_response(self) -> tuple[int, dict[str, str], bytes]:
        if self.runtime_once is None:
            return self._json_response(409, {"error": "runtime_runner_unavailable"})
        snapshot = self.runtime_once()
        return self._json_response(200, {"ok": True, "snapshot": snapshot.to_dict()})

    def _provider_config_response(self, store: SqliteStore) -> tuple[int, dict[str, str], bytes]:
        try:
            return self._json_response(200, build_provider_config_snapshot(store.root))
        except (OSError, ProfileValidationError) as exc:
            return self._json_response(
                400,
                {
                    "config_valid": False,
                    "error": "invalid_provider_config",
                    "detail": str(exc),
                },
            )

    def _update_provider_config_response(self, store: SqliteStore, body: bytes) -> tuple[int, dict[str, str], bytes]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            return self._json_response(400, {"error": "invalid_json", "detail": str(exc)})
        if not isinstance(payload, dict):
            return self._json_response(400, {"error": "invalid_payload"})
        try:
            snapshot = save_provider_config(store.root, payload)
        except (OSError, ProfileValidationError) as exc:
            return self._json_response(400, {"error": "invalid_provider_config", "detail": str(exc)})
        return self._json_response(200, snapshot)

    def _json_response(self, status: int, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        return status, {"content-type": "application/json"}, body

    def _document_detail_response(self, store: SqliteStore, document_id: str) -> tuple[int, dict[str, str], bytes]:
        try:
            doc = store.load_document(document_id)
        except FileNotFoundError:
            return self._json_response(404, {"error": "document_not_found"})
        versions = store.list_document_versions(document_id)
        return self._json_response(200, {"document": doc.to_dict(), "versions": [v.to_dict() for v in versions]})

    def _post_document_response(self, store: SqliteStore, body: bytes) -> tuple[int, dict[str, str], bytes]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            return self._json_response(400, {"error": "invalid_json", "detail": str(exc)})
        if not isinstance(payload, dict):
            return self._json_response(400, {"error": "invalid_payload"})
        from annotation_pipeline_skill.core.models import AnnotationDocument
        doc = AnnotationDocument.new(
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            created_by=str(payload.get("created_by") or "operator"),
            metadata=dict(payload.get("metadata") or {}),
        )
        store.save_document(doc)
        return self._json_response(200, doc.to_dict())

    def _post_document_version_response(self, store: SqliteStore, doc_id: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        try:
            store.load_document(doc_id)
        except FileNotFoundError:
            return self._json_response(404, {"error": "document_not_found"})
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            return self._json_response(400, {"error": "invalid_json", "detail": str(exc)})
        if not isinstance(payload, dict):
            return self._json_response(400, {"error": "invalid_payload"})
        from annotation_pipeline_skill.core.models import AnnotationDocumentVersion
        ver = AnnotationDocumentVersion.new(
            document_id=doc_id,
            version=str(payload.get("version") or "v1"),
            content=str(payload.get("content") or ""),
            changelog=str(payload.get("changelog") or ""),
            created_by=str(payload.get("created_by") or "operator"),
            metadata=dict(payload.get("metadata") or {}),
        )
        store.save_document_version(ver)
        return self._json_response(200, ver.to_dict())

    def _task_detail_response(self, store: SqliteStore, task_id: str) -> tuple[int, dict[str, str], bytes]:
        try:
            task = store.load_task(task_id)
        except FileNotFoundError:
            return self._json_response(404, {"error": "task_not_found"})

        artifacts = [
            {**artifact.to_dict(), "payload": self._read_artifact_payload(store, artifact.path)}
            for artifact in store.list_artifacts(task_id)
        ]
        return self._json_response(
            200,
            {
                "task": task.to_dict(),
                "attempts": [attempt.to_dict() for attempt in store.list_attempts(task_id)],
                "artifacts": artifacts,
                "events": [event.to_dict() for event in store.list_events(task_id)],
                "feedback": [feedback.to_dict() for feedback in store.list_feedback(task_id)],
                "feedback_discussions": [
                    entry.to_dict()
                    for entry in store.list_feedback_discussions(task_id)
                ],
                "feedback_consensus": build_feedback_consensus_summary(store, task_id),
            },
        )

    def _post_feedback_discussion_response(self, store: SqliteStore, task_id: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        try:
            task = store.load_task(task_id)
        except FileNotFoundError:
            return self._json_response(404, {"error": "task_not_found"})
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            return self._json_response(400, {"error": "invalid_json", "detail": str(exc)})
        if not isinstance(payload, dict):
            return self._json_response(400, {"error": "invalid_payload"})

        feedback_id = str(payload.get("feedback_id") or "")
        if feedback_id not in {feedback.feedback_id for feedback in store.list_feedback(task_id)}:
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
        store.append_feedback_discussion(entry)

        consensus = build_feedback_consensus_summary(store, task_id)
        if consensus["can_accept_by_consensus"] and task.status in {TaskStatus.QC, TaskStatus.HUMAN_REVIEW}:
            event = transition_task(
                task,
                TaskStatus.ACCEPTED,
                actor=entry.created_by,
                reason="feedback consensus accepted by annotator and qc",
                stage="qc",
                metadata={"feedback_id": feedback_id, "discussion_entry_id": entry.entry_id},
            )
            store.append_event(event)
            store.save_task(task)

        return self._json_response(
            200,
            {
                "entry": entry.to_dict(),
                "feedback_consensus": consensus,
                "task": store.load_task(task_id).to_dict(),
            },
        )

    def _post_human_review_response(self, store: SqliteStore, task_id: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            return self._json_response(400, {"error": "invalid_json", "detail": str(exc)})
        if not isinstance(payload, dict):
            return self._json_response(400, {"error": "invalid_payload"})
        try:
            result = HumanReviewService(store).decide(
                task_id=task_id,
                action=str(payload.get("action") or ""),
                actor=str(payload.get("actor") or "human-reviewer"),
                feedback=str(payload.get("feedback") or ""),
                correction_mode=str(payload.get("correction_mode") or "manual_annotation"),
            )
        except FileNotFoundError:
            return self._json_response(404, {"error": "task_not_found"})
        except (InvalidTransition, ValueError) as exc:
            return self._json_response(400, {"error": "invalid_human_review_decision", "detail": str(exc)})
        return self._json_response(200, result.to_dict())

    def _update_task_qc_policy_response(self, store: SqliteStore, task_id: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        try:
            task = store.load_task(task_id)
        except FileNotFoundError:
            return self._json_response(404, {"error": "task_not_found"})
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            return self._json_response(400, {"error": "invalid_json", "detail": str(exc)})
        if not isinstance(payload, dict):
            return self._json_response(400, {"error": "invalid_payload"})

        try:
            policy = self._build_task_qc_policy(task, payload)
        except ValueError as exc:
            return self._json_response(400, {"error": "invalid_qc_policy", "detail": str(exc)})

        previous_policy = dict(task.metadata.get("qc_policy") or {})
        task.metadata["row_count"] = self._task_row_count(task)
        task.metadata["qc_policy"] = policy
        task.updated_at = utc_now()
        store.save_task(task)
        store.append_event(
            AuditEvent.new(
                task_id=task.task_id,
                previous_status=task.status,
                next_status=task.status,
                actor=str(payload.get("actor") or "algorithm-engineer"),
                reason="qc policy updated",
                stage="qc",
                metadata={"previous_qc_policy": previous_policy, "qc_policy": policy},
            )
        )
        return self._task_detail_response(store, task_id)

    def _build_task_qc_policy(self, task: Task, payload: dict) -> dict:
        row_count = self._task_row_count(task)
        mode = str(payload.get("mode") or "")
        if mode == "all_rows":
            return build_qc_policy(row_count=row_count)
        if mode == "sample_count":
            sample_count = payload.get("sample_count")
            if isinstance(sample_count, bool) or not isinstance(sample_count, int):
                raise ValueError("sample_count must be an integer")
            validate_qc_sample_options(sample_count, None)
            return build_qc_policy(row_count=row_count, qc_sample_count=sample_count)
        if mode == "sample_ratio":
            sample_ratio = payload.get("sample_ratio")
            if isinstance(sample_ratio, bool) or not isinstance(sample_ratio, (int, float)):
                raise ValueError("sample_ratio must be a number")
            validate_qc_sample_options(None, float(sample_ratio))
            return build_qc_policy(row_count=row_count, qc_sample_ratio=float(sample_ratio))
        raise ValueError("mode must be all_rows, sample_count, or sample_ratio")

    def _task_row_count(self, task: Task) -> int:
        metadata_row_count = task.metadata.get("row_count")
        if isinstance(metadata_row_count, int) and metadata_row_count >= 0:
            return metadata_row_count
        payload = task.source_ref.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return len(payload["rows"])
        return 1

    def _post_coordinator_rule_update_response(self, store: SqliteStore, body: bytes) -> tuple[int, dict[str, str], bytes]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            return self._json_response(400, {"error": "invalid_json", "detail": str(exc)})
        if not isinstance(payload, dict):
            return self._json_response(400, {"error": "invalid_payload"})
        try:
            record = CoordinatorService(store).record_rule_update(
                project_id=str(payload.get("project_id") or ""),
                source=str(payload.get("source") or ""),
                summary=str(payload.get("summary") or ""),
                action=str(payload.get("action") or ""),
                created_by=str(payload.get("created_by") or "coordinator-agent"),
                task_ids=[str(task_id) for task_id in payload.get("task_ids") or []],
                status=str(payload.get("status") or "open"),
                metadata=dict(payload.get("metadata") or {}),
            )
        except ValueError as exc:
            return self._json_response(400, {"error": "invalid_coordinator_record", "detail": str(exc)})
        return self._json_response(200, record)

    def _post_coordinator_long_tail_issue_response(self, store: SqliteStore, body: bytes) -> tuple[int, dict[str, str], bytes]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            return self._json_response(400, {"error": "invalid_json", "detail": str(exc)})
        if not isinstance(payload, dict):
            return self._json_response(400, {"error": "invalid_payload"})
        try:
            record = CoordinatorService(store).record_long_tail_issue(
                project_id=str(payload.get("project_id") or ""),
                category=str(payload.get("category") or ""),
                summary=str(payload.get("summary") or ""),
                recommended_action=str(payload.get("recommended_action") or ""),
                severity=str(payload.get("severity") or "medium"),
                created_by=str(payload.get("created_by") or "coordinator-agent"),
                task_ids=[str(task_id) for task_id in payload.get("task_ids") or []],
                status=str(payload.get("status") or "open"),
                metadata=dict(payload.get("metadata") or {}),
            )
        except ValueError as exc:
            return self._json_response(400, {"error": "invalid_coordinator_record", "detail": str(exc)})
        return self._json_response(200, record)

    def _read_artifact_payload(self, store: SqliteStore, relative_path: str) -> Any:
        path = store.root / relative_path
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _config_files(self, store: SqliteStore) -> list[dict[str, Any]]:
        files = []
        for config_id, title in CONFIG_FILE_DEFINITIONS.items():
            path = store.root / config_id
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

    def _update_config_response(self, store: SqliteStore, config_id: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        if config_id not in CONFIG_FILE_DEFINITIONS:
            return self._json_response(404, {"error": "config_not_found"})
        content = body.decode("utf-8")
        try:
            yaml.safe_load(content) if content.strip() else None
        except yaml.YAMLError as exc:
            return self._json_response(400, {"error": "invalid_yaml", "detail": str(exc)})
        path = store.root / config_id
        path.write_text(content, encoding="utf-8")
        return self._json_response(200, {"ok": True, "id": config_id})

    def _event_log(self, store: SqliteStore, project_id: str | None = None) -> list[dict[str, Any]]:
        events = []
        for task in store.list_tasks():
            if project_id is not None and task.pipeline_id != project_id:
                continue
            events.extend(event.to_dict() for event in store.list_events(task.task_id))
        return sorted(events, key=lambda event: event["created_at"], reverse=True)


MIME_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
}

_STATIC_ROOT: Path | None = None


def _find_static_root() -> Path | None:
    candidates = [
        Path(__file__).parent.parent.parent / "web" / "dist",
    ]
    for path in candidates:
        if (path / "index.html").exists():
            return path
    return None


def make_handler(api: DashboardApi, static_root: Path | None = None) -> type[BaseHTTPRequestHandler]:
    resolved_static = static_root or _find_static_root()

    class DashboardRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = self.path.split("?", 1)[0]
            if route.startswith("/api/"):
                status, headers, body = api.handle_get(self.path)
                self._send(status, headers, body)
                return
            if resolved_static is not None:
                self._serve_static(route)
                return
            status, headers, body = api.handle_get(self.path)
            self._send(status, headers, body)

        def _serve_static(self, route: str) -> None:
            assert resolved_static is not None
            rel = route.lstrip("/") or "index.html"
            candidate = (resolved_static / rel).resolve()
            if not str(candidate).startswith(str(resolved_static.resolve())):
                self._send(403, {}, b"Forbidden")
                return
            if not candidate.exists() or candidate.is_dir():
                candidate = resolved_static / "index.html"
            suffix = candidate.suffix.lower()
            content_type = MIME_TYPES.get(suffix, "application/octet-stream")
            body = candidate.read_bytes()
            self._send(200, {"content-type": content_type}, body)

        def do_PUT(self) -> None:
            content_length = int(self.headers.get("content-length", "0"))
            request_body = self.rfile.read(content_length)
            status, headers, body = api.handle_put(self.path, request_body)
            self._send(status, headers, body)

        def do_POST(self) -> None:
            content_length = int(self.headers.get("content-length", "0"))
            request_body = self.rfile.read(content_length)
            status, headers, body = api.handle_post(self.path, request_body)
            self._send(status, headers, body)

        def _send(self, status: int, headers: dict[str, str], body: bytes) -> None:
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
    store: SqliteStore,
    host: str,
    port: int,
    *,
    stores: dict[str, SqliteStore] | None = None,
    default_store_key: str | None = None,
    runtime_once: Callable[[], RuntimeSnapshot] | None = None,
    runtime_config: RuntimeConfig | None = None,
) -> None:
    server = ThreadingHTTPServer(
        (host, port),
        make_handler(DashboardApi(
            store,
            stores=stores,
            default_store_key=default_store_key,
            runtime_once=runtime_once,
            runtime_config=runtime_config,
        )),
    )
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local annotation dashboard API.")
    parser.add_argument("store_root", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve_dashboard_api(SqliteStore.open(args.store_root), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
