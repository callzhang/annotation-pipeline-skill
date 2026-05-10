"""Read-only legacy JSON/JSONL store retained ONLY for the one-shot
``scripts/migrate_filestore_to_sqlite.py`` migration script.

Production code uses ``annotation_pipeline_skill.store.sqlite_store.SqliteStore``.
This module will be removed once the migration is no longer needed.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, TypeVar

from annotation_pipeline_skill.core.models import (
    AnnotationDocument,
    AnnotationDocumentVersion,
    ArtifactRef,
    Attempt,
    AuditEvent,
    ExportManifest,
    FeedbackDiscussionEntry,
    FeedbackRecord,
    OutboxRecord,
    Task,
)
from annotation_pipeline_skill.core.runtime import ActiveRun, RuntimeCycleStats, RuntimeSnapshot
from annotation_pipeline_skill.core.runtime import RuntimeLease

T = TypeVar("T")


class FileStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.tasks_dir = self.root / "tasks"
        self.events_dir = self.root / "events"
        self.feedback_dir = self.root / "feedback"
        self.feedback_discussions_dir = self.root / "feedback_discussions"
        self.attempts_dir = self.root / "attempts"
        self.artifacts_dir = self.root / "artifacts"
        self.outbox_dir = self.root / "outbox"
        self.exports_dir = self.root / "exports"
        self.coordination_dir = self.root / "coordination"
        self.runtime_dir = self.root / "runtime"
        self.active_runs_dir = self.runtime_dir / "active_runs"
        self.leases_dir = self.runtime_dir / "leases"
        self.runtime_cycles_path = self.runtime_dir / "cycle_stats.jsonl"
        self.runtime_heartbeat_path = self.runtime_dir / "heartbeat.json"
        self.runtime_snapshot_path = self.runtime_dir / "runtime_snapshot.json"
        self.documents_dir = self.root / "documents"
        self.document_versions_dir = self.root / "document_versions"
        for directory in (
            self.tasks_dir,
            self.events_dir,
            self.feedback_dir,
            self.feedback_discussions_dir,
            self.attempts_dir,
            self.artifacts_dir,
            self.outbox_dir,
            self.exports_dir,
            self.coordination_dir,
            self.runtime_dir,
            self.active_runs_dir,
            self.leases_dir,
            self.documents_dir,
            self.document_versions_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def save_task(self, task: Task) -> None:
        self._write_json(self.tasks_dir / f"{task.task_id}.json", task.to_dict())

    def load_task(self, task_id: str) -> Task:
        return Task.from_dict(self._read_json(self.tasks_dir / f"{task_id}.json"))

    def list_tasks(self) -> list[Task]:
        return [
            Task.from_dict(self._read_json(path))
            for path in sorted(self.tasks_dir.glob("*.json"))
        ]

    def append_event(self, event: AuditEvent) -> None:
        self._append_jsonl(self.events_dir / f"{event.task_id}.jsonl", event.to_dict())

    def list_events(self, task_id: str) -> list[AuditEvent]:
        return self._read_jsonl(self.events_dir / f"{task_id}.jsonl", AuditEvent.from_dict)

    def append_feedback(self, feedback: FeedbackRecord) -> None:
        self._append_jsonl(self.feedback_dir / f"{feedback.task_id}.jsonl", feedback.to_dict())

    def list_feedback(self, task_id: str) -> list[FeedbackRecord]:
        return self._read_jsonl(self.feedback_dir / f"{task_id}.jsonl", FeedbackRecord.from_dict)

    def append_feedback_discussion(self, entry: FeedbackDiscussionEntry) -> None:
        self._append_jsonl(self.feedback_discussions_dir / f"{entry.task_id}.jsonl", entry.to_dict())

    def list_feedback_discussions(self, task_id: str) -> list[FeedbackDiscussionEntry]:
        return self._read_jsonl(
            self.feedback_discussions_dir / f"{task_id}.jsonl",
            FeedbackDiscussionEntry.from_dict,
        )

    def append_attempt(self, attempt: Attempt) -> None:
        self._append_jsonl(self.attempts_dir / f"{attempt.task_id}.jsonl", attempt.to_dict())

    def list_attempts(self, task_id: str) -> list[Attempt]:
        return self._read_jsonl(self.attempts_dir / f"{task_id}.jsonl", Attempt.from_dict)

    def append_artifact(self, artifact: ArtifactRef) -> None:
        self._append_jsonl(self.artifacts_dir / f"{artifact.task_id}.jsonl", artifact.to_dict())

    def list_artifacts(self, task_id: str) -> list[ArtifactRef]:
        return self._read_jsonl(self.artifacts_dir / f"{task_id}.jsonl", ArtifactRef.from_dict)

    def save_outbox(self, record: OutboxRecord) -> None:
        self._write_json(self.outbox_dir / f"{record.record_id}.json", record.to_dict())

    def list_outbox(self) -> list[OutboxRecord]:
        return [
            OutboxRecord.from_dict(self._read_json(path))
            for path in sorted(self.outbox_dir.glob("*.json"))
        ]

    def save_export_manifest(self, manifest: ExportManifest) -> None:
        export_dir = self.exports_dir / manifest.export_id
        export_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(export_dir / "manifest.json", manifest.to_dict())

    def list_export_manifests(self) -> list[ExportManifest]:
        return [
            ExportManifest.from_dict(self._read_json(path))
            for path in sorted(self.exports_dir.glob("*/manifest.json"))
        ]

    def append_coordination_record(self, kind: str, record: dict) -> None:
        self._append_jsonl(self.coordination_dir / f"{kind}.jsonl", record)

    def list_coordination_records(self, kind: str) -> list[dict]:
        return self._read_jsonl(self.coordination_dir / f"{kind}.jsonl", lambda item: item)

    def save_active_run(self, run: ActiveRun) -> None:
        self._write_json(self.active_runs_dir / f"{run.run_id}.json", run.to_dict())

    def list_active_runs(self) -> list[ActiveRun]:
        return [
            ActiveRun.from_dict(self._read_json(path))
            for path in sorted(self.active_runs_dir.glob("*.json"))
        ]

    def delete_active_run(self, run_id: str) -> None:
        (self.active_runs_dir / f"{run_id}.json").unlink(missing_ok=True)

    def save_runtime_lease(self, lease: RuntimeLease) -> bool:
        path = self.leases_dir / f"{lease.lease_id}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(lease.to_dict(), sort_keys=True, indent=2) + "\n")
            return True
        except FileExistsError:
            return False

    def list_runtime_leases(self) -> list[RuntimeLease]:
        return [
            RuntimeLease.from_dict(self._read_json(path))
            for path in sorted(self.leases_dir.glob("*.json"))
        ]

    def delete_runtime_lease(self, lease_id: str) -> None:
        (self.leases_dir / f"{lease_id}.json").unlink(missing_ok=True)

    def save_runtime_heartbeat(self, heartbeat_at: datetime) -> None:
        self._write_json(
            self.runtime_heartbeat_path,
            {"heartbeat_at": heartbeat_at.isoformat()},
        )

    def load_runtime_heartbeat(self) -> datetime | None:
        if not self.runtime_heartbeat_path.exists():
            return None
        payload = self._read_json(self.runtime_heartbeat_path)
        return datetime.fromisoformat(payload["heartbeat_at"])

    def append_runtime_cycle_stats(self, stats: RuntimeCycleStats) -> None:
        self._append_jsonl(self.runtime_cycles_path, stats.to_dict())

    def list_runtime_cycle_stats(self) -> list[RuntimeCycleStats]:
        return self._read_jsonl(self.runtime_cycles_path, RuntimeCycleStats.from_dict)

    def save_runtime_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        self._write_json(self.runtime_snapshot_path, snapshot.to_dict())

    def load_runtime_snapshot(self) -> RuntimeSnapshot | None:
        if not self.runtime_snapshot_path.exists():
            return None
        return RuntimeSnapshot.from_dict(self._read_json(self.runtime_snapshot_path))

    def save_document(self, doc: AnnotationDocument) -> None:
        self._write_json(self.documents_dir / f"{doc.document_id}.json", doc.to_dict())

    def load_document(self, document_id: str) -> AnnotationDocument:
        return AnnotationDocument.from_dict(self._read_json(self.documents_dir / f"{document_id}.json"))

    def list_documents(self) -> list[AnnotationDocument]:
        return [
            AnnotationDocument.from_dict(self._read_json(path))
            for path in sorted(self.documents_dir.glob("*.json"))
        ]

    def save_document_version(self, ver: AnnotationDocumentVersion) -> None:
        self._write_json(self.document_versions_dir / f"{ver.version_id}.json", ver.to_dict())

    def load_document_version(self, version_id: str) -> AnnotationDocumentVersion:
        return AnnotationDocumentVersion.from_dict(self._read_json(self.document_versions_dir / f"{version_id}.json"))

    def list_document_versions(self, document_id: str) -> list[AnnotationDocumentVersion]:
        results = []
        for path in sorted(self.document_versions_dir.glob("*.json")):
            data = self._read_json(path)
            if data.get("document_id") == document_id:
                results.append(AnnotationDocumentVersion.from_dict(data))
        return results

    def _write_json(self, path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def _read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def _append_jsonl(self, path: Path, data: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, sort_keys=True) + "\n")

    def _read_jsonl(self, path: Path, factory: Callable[[dict], T]) -> list[T]:
        if not path.exists():
            return []
        return [
            factory(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
