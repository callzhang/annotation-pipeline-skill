from __future__ import annotations

from hashlib import sha256

from annotation_pipeline_skill.core.models import ExternalTaskRef, OutboxRecord, Task
from annotation_pipeline_skill.core.states import OutboxKind
from annotation_pipeline_skill.store.file_store import FileStore


class ExternalTaskService:
    def __init__(self, store: FileStore):
        self.store = store

    def upsert_pulled_task(
        self,
        pipeline_id: str,
        system_id: str,
        external_task_id: str,
        payload: dict,
        source_url: str | None = None,
    ) -> Task:
        idempotency_key = f"{system_id}:{external_task_id}"
        task_id = self._task_id_for_external(idempotency_key)
        existing = self._load_existing_task(task_id)
        if existing:
            return existing

        task = Task.new(
            task_id=task_id,
            pipeline_id=pipeline_id,
            source_ref={"kind": "external_task", "payload": payload},
            external_ref=ExternalTaskRef(
                system_id=system_id,
                external_task_id=external_task_id,
                source_url=source_url,
                idempotency_key=idempotency_key,
            ),
        )
        self.store.save_task(task)
        return task

    def enqueue_status(self, task: Task, status: str) -> OutboxRecord:
        record = OutboxRecord.new(
            task_id=task.task_id,
            kind=OutboxKind.STATUS,
            payload={
                "task_id": task.task_id,
                "external_ref": task.external_ref.to_dict() if task.external_ref else None,
                "status": status,
            },
        )
        self.store.save_outbox(record)
        return record

    def enqueue_submit(self, task: Task, payload: dict) -> OutboxRecord:
        record = OutboxRecord.new(
            task_id=task.task_id,
            kind=OutboxKind.SUBMIT,
            payload={
                "task_id": task.task_id,
                "external_ref": task.external_ref.to_dict() if task.external_ref else None,
                "result": payload,
            },
        )
        self.store.save_outbox(record)
        return record

    def _load_existing_task(self, task_id: str) -> Task | None:
        path = self.store.tasks_dir / f"{task_id}.json"
        if not path.exists():
            return None
        return self.store.load_task(task_id)

    def _task_id_for_external(self, idempotency_key: str) -> str:
        digest = sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
        return f"external-{digest}"
