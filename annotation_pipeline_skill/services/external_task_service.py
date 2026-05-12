from __future__ import annotations

import json
import os
from hashlib import sha256
from urllib.request import Request, urlopen

from annotation_pipeline_skill.core.models import ExternalTaskRef, OutboxRecord, Task
from annotation_pipeline_skill.core.qc_policy import validate_qc_sample_options
from annotation_pipeline_skill.core.states import OutboxKind, TaskStatus
from annotation_pipeline_skill.core.transitions import transition_task
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


class ExternalTaskService:
    def __init__(self, store: SqliteStore):
        self.store = store

    def upsert_pulled_task(
        self,
        pipeline_id: str,
        system_id: str,
        external_task_id: str,
        payload: dict,
        source_url: str | None = None,
        qc_sample_count: int | None = None,
        qc_sample_ratio: float | None = None,
    ) -> Task:
        validate_qc_sample_options(qc_sample_count, qc_sample_ratio)
        idempotency_key = f"{system_id}:{external_task_id}"
        task_id = self._task_id_for_external(idempotency_key)
        existing = self._load_existing_task(task_id)
        if existing:
            return existing

        row_count = self._payload_row_count(payload)
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
            metadata={
                "row_count": row_count,
            },
        )
        event = transition_task(
            task,
            TaskStatus.PENDING,
            actor="external_task_service",
            reason="created from external task pull",
            stage="prepare",
            metadata={"system_id": system_id, "external_task_id": external_task_id},
        )
        self.store.save_task(task)
        self.store.append_event(event)
        return task

    def pull_http_tasks(
        self,
        *,
        pipeline_id: str,
        source_id: str,
        config: dict,
        limit: int,
    ) -> dict:
        if not config.get("enabled"):
            raise ValueError(f"external task source {source_id} is disabled")
        pull_url = str(config["pull_url"])
        system_id = str(config.get("system_id") or source_id)
        qc_sample_count = config.get("qc_sample_count")
        qc_sample_ratio = config.get("qc_sample_ratio")
        validate_qc_sample_options(qc_sample_count, qc_sample_ratio)
        response = self._post_json(
            pull_url,
            {"limit": limit},
            secret_env=config.get("auth_secret_env"),
        )
        tasks_data = response["tasks"]
        created = 0
        existing = 0
        task_ids = []
        for item in tasks_data:
            external_task_id = str(item["external_task_id"])
            idempotency_key = f"{system_id}:{external_task_id}"
            existed = self._load_existing_task(self._task_id_for_external(idempotency_key)) is not None
            task = self.upsert_pulled_task(
                pipeline_id=pipeline_id,
                system_id=system_id,
                external_task_id=external_task_id,
                payload=dict(item["payload"]),
                source_url=pull_url,
                qc_sample_count=qc_sample_count,
                qc_sample_ratio=qc_sample_ratio,
            )
            task_ids.append(task.task_id)
            if existed:
                existing += 1
            else:
                created += 1
                self.enqueue_status(task, status=task.status.value)
        return {
            "source_id": source_id,
            "system_id": system_id,
            "requested_limit": limit,
            "received": len(tasks_data),
            "created": created,
            "existing": existing,
            "task_ids": task_ids,
        }

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
        try:
            return self.store.load_task(task_id)
        except KeyError:
            return None

    def _task_id_for_external(self, idempotency_key: str) -> str:
        digest = sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
        return f"external-{digest}"

    def _payload_row_count(self, payload: dict) -> int:
        rows = payload.get("rows")
        if isinstance(rows, list):
            return len(rows)
        return 1

    def _post_json(self, url: str, payload: dict, secret_env: str | None = None) -> dict:
        headers = {"content-type": "application/json", "accept": "application/json"}
        if secret_env:
            token = os.environ[secret_env]
            headers["authorization"] = f"Bearer {token}"
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            body = response.read()
        return json.loads(body.decode("utf-8"))
