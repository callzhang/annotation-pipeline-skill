from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from annotation_pipeline_skill.core.models import (
    ArtifactRef,
    Attempt,
    AuditEvent,
    FeedbackDiscussionEntry,
    FeedbackRecord,
    OutboxRecord,
    Task,
)
from annotation_pipeline_skill.core.states import TaskStatus

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task.from_dict({
        "task_id": row["task_id"],
        "pipeline_id": row["pipeline_id"],
        "source_ref": json.loads(row["source_ref_json"]),
        "external_ref": json.loads(row["external_ref_json"]) if row["external_ref_json"] else None,
        "modality": row["modality"],
        "annotation_requirements": json.loads(row["annotation_requirements_json"]),
        "selected_annotator_id": row["selected_annotator_id"],
        "status": row["status"],
        "current_attempt": row["current_attempt"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "active_run_id": row["active_run_id"],
        "next_retry_at": row["next_retry_at"],
        "metadata": json.loads(row["metadata_json"]),
        "document_version_id": row["document_version_id"],
    })


class SqliteStore:
    def __init__(self, root: Path | str, conn: sqlite3.Connection):
        self.root = Path(root)
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    @classmethod
    def open(cls, root: Path | str) -> "SqliteStore":
        root_path = Path(root)
        root_path.mkdir(parents=True, exist_ok=True)
        for sub in ("artifacts", "exports", "runtime", "documents", "document_versions", "backups"):
            (root_path / sub).mkdir(parents=True, exist_ok=True)
        db_path = root_path / "db.sqlite"
        first_time = not db_path.exists()
        conn = sqlite3.connect(db_path, isolation_level=None, timeout=5.0)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        if first_time:
            conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        return cls(root_path, conn)

    def close(self) -> None:
        self._conn.close()

    def save_task(self, task: Task) -> None:
        d = task.to_dict()
        self._conn.execute(
            """
            INSERT INTO tasks (
                task_id, pipeline_id, status, current_attempt, modality,
                selected_annotator_id, active_run_id, next_retry_at,
                created_at, updated_at, document_version_id,
                source_ref_json, external_ref_json,
                annotation_requirements_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                pipeline_id=excluded.pipeline_id,
                status=excluded.status,
                current_attempt=excluded.current_attempt,
                modality=excluded.modality,
                selected_annotator_id=excluded.selected_annotator_id,
                active_run_id=excluded.active_run_id,
                next_retry_at=excluded.next_retry_at,
                updated_at=excluded.updated_at,
                document_version_id=excluded.document_version_id,
                source_ref_json=excluded.source_ref_json,
                external_ref_json=excluded.external_ref_json,
                annotation_requirements_json=excluded.annotation_requirements_json,
                metadata_json=excluded.metadata_json
            """,
            (
                d["task_id"], d["pipeline_id"], d["status"], d["current_attempt"], d["modality"],
                d["selected_annotator_id"], d["active_run_id"], d["next_retry_at"],
                d["created_at"], d["updated_at"], d["document_version_id"],
                json.dumps(d["source_ref"], sort_keys=True),
                json.dumps(d["external_ref"], sort_keys=True) if d["external_ref"] else None,
                json.dumps(d["annotation_requirements"], sort_keys=True),
                json.dumps(d["metadata"], sort_keys=True),
            ),
        )

    def load_task(self, task_id: str) -> Task:
        """Return the task with this id; raise KeyError if it does not exist."""
        row = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return _row_to_task(row)

    def list_tasks(self) -> list[Task]:
        rows = self._conn.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
        return [_row_to_task(r) for r in rows]

    def list_tasks_by_pipeline(self, pipeline_id: str) -> list[Task]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE pipeline_id = ? ORDER BY created_at",
            (pipeline_id,),
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    def list_tasks_by_status(self, statuses: Iterable[TaskStatus]) -> list[Task]:
        values = [s.value for s in statuses]
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        rows = self._conn.execute(
            f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY created_at",
            values,
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    def append_event(self, event: AuditEvent) -> None:
        d = event.to_dict()
        self._conn.execute(
            """
            INSERT INTO audit_events (
                event_id, task_id, previous_status, next_status, actor,
                reason, stage, attempt_id, created_at, metadata_json, seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT MAX(seq) + 1 FROM audit_events WHERE task_id = ?), 1)
            )
            """,
            (
                d["event_id"], d["task_id"], d["previous_status"], d["next_status"],
                d["actor"], d["reason"], d["stage"], d["attempt_id"], d["created_at"],
                json.dumps(d["metadata"], sort_keys=True),
                d["task_id"],
            ),
        )

    def list_events(self, task_id: str) -> list[AuditEvent]:
        rows = self._conn.execute(
            "SELECT * FROM audit_events WHERE task_id = ? ORDER BY seq",
            (task_id,),
        ).fetchall()
        return [
            AuditEvent.from_dict({
                "event_id": r["event_id"],
                "task_id": r["task_id"],
                "previous_status": r["previous_status"],
                "next_status": r["next_status"],
                "actor": r["actor"],
                "reason": r["reason"],
                "stage": r["stage"],
                "attempt_id": r["attempt_id"],
                "created_at": r["created_at"],
                "metadata": json.loads(r["metadata_json"]),
            })
            for r in rows
        ]

    def append_attempt(self, attempt) -> None:
        d = attempt.to_dict()
        self._conn.execute(
            """
            INSERT INTO attempts (
                attempt_id, task_id, idx, stage, status,
                started_at, finished_at, provider_id, model, effort,
                route_role, summary, error_json, artifacts_json, seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT MAX(seq) + 1 FROM attempts WHERE task_id = ?), 1)
            )
            """,
            (
                d["attempt_id"], d["task_id"], d["index"], d["stage"], d["status"],
                d["started_at"], d["finished_at"], d["provider_id"], d["model"], d["effort"],
                d["route_role"], d["summary"],
                json.dumps(d["error"], sort_keys=True) if d["error"] else None,
                json.dumps(d["artifacts"], sort_keys=True),
                d["task_id"],
            ),
        )

    def list_attempts(self, task_id: str):
        rows = self._conn.execute(
            "SELECT * FROM attempts WHERE task_id = ? ORDER BY seq",
            (task_id,),
        ).fetchall()
        return [
            Attempt.from_dict({
                "attempt_id": r["attempt_id"], "task_id": r["task_id"],
                "index": r["idx"], "stage": r["stage"], "status": r["status"],
                "started_at": r["started_at"], "finished_at": r["finished_at"],
                "provider_id": r["provider_id"], "model": r["model"], "effort": r["effort"],
                "route_role": r["route_role"], "summary": r["summary"],
                "error": json.loads(r["error_json"]) if r["error_json"] else None,
                "artifacts": json.loads(r["artifacts_json"]),
            })
            for r in rows
        ]

    def append_feedback(self, feedback) -> None:
        d = feedback.to_dict()
        self._conn.execute(
            """
            INSERT INTO feedback_records (
                feedback_id, task_id, attempt_id, source_stage, severity,
                category, message, target_json, suggested_action,
                created_at, created_by, metadata_json, seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT MAX(seq) + 1 FROM feedback_records WHERE task_id = ?), 1)
            )
            """,
            (
                d["feedback_id"], d["task_id"], d["attempt_id"], d["source_stage"], d["severity"],
                d["category"], d["message"],
                json.dumps(d["target"], sort_keys=True),
                d["suggested_action"], d["created_at"], d["created_by"],
                json.dumps(d["metadata"], sort_keys=True),
                d["task_id"],
            ),
        )

    def list_feedback(self, task_id: str):
        rows = self._conn.execute(
            "SELECT * FROM feedback_records WHERE task_id = ? ORDER BY seq",
            (task_id,),
        ).fetchall()
        return [
            FeedbackRecord.from_dict({
                "feedback_id": r["feedback_id"], "task_id": r["task_id"],
                "attempt_id": r["attempt_id"], "source_stage": r["source_stage"],
                "severity": r["severity"], "category": r["category"], "message": r["message"],
                "target": json.loads(r["target_json"]),
                "suggested_action": r["suggested_action"],
                "created_at": r["created_at"], "created_by": r["created_by"],
                "metadata": json.loads(r["metadata_json"]),
            })
            for r in rows
        ]

    def append_feedback_discussion(self, entry) -> None:
        d = entry.to_dict()
        self._conn.execute(
            """
            INSERT INTO feedback_discussions (
                entry_id, task_id, feedback_id, role, stance, message,
                agreed_points_json, disputed_points_json, proposed_resolution,
                consensus, created_at, created_by, metadata_json, seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT MAX(seq) + 1 FROM feedback_discussions WHERE task_id = ?), 1)
            )
            """,
            (
                d["entry_id"], d["task_id"], d["feedback_id"], d["role"], d["stance"], d["message"],
                json.dumps(d["agreed_points"], sort_keys=True),
                json.dumps(d["disputed_points"], sort_keys=True),
                d["proposed_resolution"], 1 if d["consensus"] else 0,
                d["created_at"], d["created_by"],
                json.dumps(d["metadata"], sort_keys=True),
                d["task_id"],
            ),
        )

    def list_feedback_discussions(self, task_id: str):
        rows = self._conn.execute(
            "SELECT * FROM feedback_discussions WHERE task_id = ? ORDER BY seq",
            (task_id,),
        ).fetchall()
        return [
            FeedbackDiscussionEntry.from_dict({
                "entry_id": r["entry_id"], "task_id": r["task_id"],
                "feedback_id": r["feedback_id"], "role": r["role"], "stance": r["stance"],
                "message": r["message"],
                "agreed_points": json.loads(r["agreed_points_json"]),
                "disputed_points": json.loads(r["disputed_points_json"]),
                "proposed_resolution": r["proposed_resolution"],
                "consensus": bool(r["consensus"]),
                "created_at": r["created_at"], "created_by": r["created_by"],
                "metadata": json.loads(r["metadata_json"]),
            })
            for r in rows
        ]

    def append_artifact(self, artifact) -> None:
        d = artifact.to_dict()
        self._conn.execute(
            """
            INSERT INTO artifact_refs (
                artifact_id, task_id, kind, path, content_type,
                created_at, metadata_json, seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT MAX(seq) + 1 FROM artifact_refs WHERE task_id = ?), 1)
            )
            """,
            (
                d["artifact_id"], d["task_id"], d["kind"], d["path"], d["content_type"],
                d["created_at"], json.dumps(d["metadata"], sort_keys=True),
                d["task_id"],
            ),
        )

    def list_artifacts(self, task_id: str):
        rows = self._conn.execute(
            "SELECT * FROM artifact_refs WHERE task_id = ? ORDER BY seq",
            (task_id,),
        ).fetchall()
        return [
            ArtifactRef.from_dict({
                "artifact_id": r["artifact_id"], "task_id": r["task_id"],
                "kind": r["kind"], "path": r["path"], "content_type": r["content_type"],
                "created_at": r["created_at"],
                "metadata": json.loads(r["metadata_json"]),
            })
            for r in rows
        ]

    def save_outbox(self, record) -> None:
        d = record.to_dict()
        self._conn.execute(
            """
            INSERT INTO outbox_records (
                record_id, task_id, kind, payload_json, status,
                retry_count, next_retry_at, last_error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                kind=excluded.kind,
                payload_json=excluded.payload_json,
                status=excluded.status,
                retry_count=excluded.retry_count,
                next_retry_at=excluded.next_retry_at,
                last_error=excluded.last_error
            """,
            (
                d["record_id"], d["task_id"], d["kind"],
                json.dumps(d["payload"], sort_keys=True),
                d["status"], d["retry_count"], d["next_retry_at"], d["last_error"], d["created_at"],
            ),
        )

    def _row_to_outbox(self, r):
        return OutboxRecord.from_dict({
            "record_id": r["record_id"], "task_id": r["task_id"], "kind": r["kind"],
            "payload": json.loads(r["payload_json"]), "status": r["status"],
            "retry_count": r["retry_count"], "next_retry_at": r["next_retry_at"],
            "last_error": r["last_error"], "created_at": r["created_at"],
        })

    def list_outbox(self):
        rows = self._conn.execute("SELECT * FROM outbox_records ORDER BY created_at").fetchall()
        return [self._row_to_outbox(r) for r in rows]

    def list_pending_outbox(self, *, now):
        rows = self._conn.execute(
            """
            SELECT * FROM outbox_records
            WHERE status = ?
              AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY created_at
            """,
            ("pending", now.isoformat()),
        ).fetchall()
        return [self._row_to_outbox(r) for r in rows]
