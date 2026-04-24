from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, TypeVar

from annotation_pipeline_skill.core.models import (
    ArtifactRef,
    Attempt,
    AuditEvent,
    FeedbackRecord,
    OutboxRecord,
    Task,
)

T = TypeVar("T")


class FileStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.tasks_dir = self.root / "tasks"
        self.events_dir = self.root / "events"
        self.feedback_dir = self.root / "feedback"
        self.attempts_dir = self.root / "attempts"
        self.artifacts_dir = self.root / "artifacts"
        self.outbox_dir = self.root / "outbox"
        for directory in (
            self.tasks_dir,
            self.events_dir,
            self.feedback_dir,
            self.attempts_dir,
            self.artifacts_dir,
            self.outbox_dir,
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
