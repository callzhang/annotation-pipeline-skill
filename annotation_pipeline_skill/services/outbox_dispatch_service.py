from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from annotation_pipeline_skill.core.models import AuditEvent, OutboxRecord
from annotation_pipeline_skill.core.states import OutboxStatus
from annotation_pipeline_skill.store.sqlite_store import SqliteStore

OutboxSender = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any] | None]


class RetryableOutboxError(RuntimeError):
    pass


class PermanentOutboxError(RuntimeError):
    pass


class OutboxDispatchService:
    def __init__(
        self,
        store: SqliteStore,
        *,
        callbacks: dict,
        sender: OutboxSender | None = None,
    ):
        self.store = store
        self.callbacks = callbacks
        self.sender = sender or post_json

    def drain(
        self,
        *,
        max_items: int,
        max_attempts: int = 3,
        retry_delay_seconds: int = 60,
        now: datetime | None = None,
    ) -> dict[str, int]:
        now = now or datetime.now(timezone.utc)
        result = {"sent": 0, "retry": 0, "dead_letter": 0, "skipped": 0}
        due_records = []
        for record in self.store.list_outbox():
            if record.status is not OutboxStatus.PENDING:
                continue
            if record.next_retry_at is not None and record.next_retry_at > now:
                result["skipped"] += 1
                continue
            due_records.append(record)

        for record in due_records[:max_items]:
            try:
                self._send(record)
            except PermanentOutboxError as exc:
                self._dead_letter(record, str(exc))
                result["dead_letter"] += 1
            except RetryableOutboxError as exc:
                record.retry_count += 1
                record.last_error = str(exc)
                if record.retry_count >= max_attempts:
                    self._dead_letter(record, str(exc))
                    result["dead_letter"] += 1
                else:
                    record.next_retry_at = now + timedelta(seconds=retry_delay_seconds)
                    self.store.save_outbox(record)
                    self._append_task_event(record, reason=f"external {record.kind.value} outbox retry scheduled")
                    result["retry"] += 1
            else:
                record.status = OutboxStatus.SENT
                record.next_retry_at = None
                record.last_error = None
                self.store.save_outbox(record)
                self._append_task_event(record, reason=f"external {record.kind.value} outbox sent")
                result["sent"] += 1

        return result

    def _send(self, record: OutboxRecord) -> None:
        callback = self._callback_for(record)
        if not callback.get("enabled", False):
            raise PermanentOutboxError(f"{record.kind.value} callback disabled")
        url = callback.get("url")
        if not isinstance(url, str) or not url:
            raise PermanentOutboxError(f"{record.kind.value} callback url missing")
        headers = self._headers_for(callback)
        self.sender(url, record.payload, headers)

    def _callback_for(self, record: OutboxRecord) -> dict:
        value = self.callbacks.get(record.kind.value)
        return value if isinstance(value, dict) else {}

    def _headers_for(self, callback: dict) -> dict[str, str]:
        headers: dict[str, str] = {}
        secret_env = callback.get("secret_env")
        if isinstance(secret_env, str) and secret_env and os.environ.get(secret_env):
            headers["authorization"] = f"Bearer {os.environ[secret_env]}"
        return headers

    def _dead_letter(self, record: OutboxRecord, error: str) -> None:
        record.status = OutboxStatus.DEAD_LETTER
        record.next_retry_at = None
        record.last_error = error
        self.store.save_outbox(record)
        self._append_task_event(record, reason=f"external {record.kind.value} outbox dead letter")

    def _append_task_event(self, record: OutboxRecord, *, reason: str) -> None:
        try:
            task = self.store.load_task(record.task_id)
        except FileNotFoundError:
            return
        self.store.append_event(
            AuditEvent.new(
                task_id=task.task_id,
                previous_status=task.status,
                next_status=task.status,
                actor="outbox-dispatcher",
                reason=reason,
                stage="external",
                metadata={"outbox_record_id": record.record_id, "outbox_kind": record.kind.value},
            )
        )


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any] | None:
    request = Request(
        url,
        data=json.dumps(payload, sort_keys=True).encode("utf-8"),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            body = response.read()
    except HTTPError as exc:
        if 400 <= exc.code < 500:
            raise PermanentOutboxError(f"http {exc.code}") from exc
        raise RetryableOutboxError(f"http {exc.code}") from exc
    except URLError as exc:
        raise RetryableOutboxError(str(exc.reason)) from exc
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def build_outbox_summary(store: SqliteStore, project_id: str | None = None) -> dict[str, Any]:
    task_ids = None
    if project_id is not None:
        task_ids = {t.task_id for t in store.list_tasks_by_pipeline(project_id)}
    records = [
        record.to_dict()
        for record in store.list_outbox()
        if task_ids is None or record.task_id in task_ids
    ]
    counts = {
        "dead_letter": sum(1 for record in records if record["status"] == "dead_letter"),
        "pending": sum(1 for record in records if record["status"] == "pending"),
        "sent": sum(1 for record in records if record["status"] == "sent"),
    }
    return {"counts": counts, "records": records}
