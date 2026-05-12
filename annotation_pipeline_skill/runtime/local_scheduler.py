from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.runtime import ActiveRun, RuntimeConfig, RuntimeCycleStats, RuntimeLease, RuntimeSnapshot
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.llm.client import LLMClient
from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot
from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


@dataclass
class _CycleOutcome:
    started: int
    accepted: int
    failed: int
    errors: list[dict]


class LocalRuntimeScheduler:
    def __init__(
        self,
        store: SqliteStore,
        client_factory: Callable[[str], LLMClient],
        config: RuntimeConfig,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.client_factory = client_factory
        self.config = config
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._clear_stale_records()

    def _clear_stale_records(self) -> None:
        """Remove leases and active_runs whose heartbeat exceeds stale_after_seconds.

        Called once at construction so a freshly-restarted scheduler doesn't
        count leftover rows from a previously-killed instance toward
        existing_active_count. Only rows older than ``stale_after_seconds``
        are removed; fresh in-flight rows from another live scheduler are
        preserved.
        """
        threshold = self._now_fn() - timedelta(seconds=self.config.stale_after_seconds)
        cleared_leases = 0
        cleared_runs = 0
        for lease in self.store.list_runtime_leases():
            if lease.heartbeat_at < threshold:
                self.store.delete_runtime_lease(lease.lease_id)
                cleared_leases += 1
        for run in self.store.list_active_runs():
            if run.heartbeat_at < threshold:
                self.store.delete_active_run(run.run_id)
                cleared_runs += 1
        if cleared_leases or cleared_runs:
            import sys
            print(
                f"[scheduler] cleared {cleared_leases} stale leases, "
                f"{cleared_runs} stale active_runs",
                file=sys.stderr,
            )

    def run_once(self, stage_target: str = "annotation", now: datetime | None = None) -> RuntimeSnapshot:
        cycle_started_at = now or datetime.now(timezone.utc)
        self.store.save_runtime_heartbeat(cycle_started_at)

        existing_active_count = max(len(self.store.list_runtime_leases()), len(self.store.list_active_runs()))
        capacity_available = max(self.config.max_concurrent_tasks - existing_active_count, 0)
        start_limit = min(capacity_available, self.config.max_starts_per_cycle)
        runnable_tasks = [
            task
            for task in self.store.list_tasks()
            if task.status is TaskStatus.PENDING
            or (task.status is TaskStatus.QC and task.metadata.get("runtime_next_stage") == "qc")
        ]
        selected_tasks = runnable_tasks[:start_limit]

        runtime = SubagentRuntime(
            store=self.store,
            client_factory=self.client_factory,
            max_qc_rounds=self.config.max_qc_rounds,
            config=self.config,
        )

        cycle_outcome = asyncio.run(
            self._run_cycle_async(
                selected_tasks=selected_tasks,
                runtime=runtime,
                stage_target=stage_target,
                cycle_started_at=cycle_started_at,
            )
        )
        started = cycle_outcome.started
        accepted = cycle_outcome.accepted
        failed = cycle_outcome.failed
        errors = cycle_outcome.errors

        cycle_finished_at = datetime.now(timezone.utc)
        stats = RuntimeCycleStats(
            cycle_id=f"cycle-{uuid4().hex}",
            started_at=cycle_started_at,
            finished_at=cycle_finished_at,
            started=started,
            accepted=accepted,
            failed=failed,
            capacity_available=capacity_available,
            errors=errors,
        )
        self.store.append_runtime_cycle_stats(stats)
        self.store.save_runtime_heartbeat(cycle_finished_at)
        snapshot = build_runtime_snapshot(self.store, self.config, now=cycle_finished_at)
        self.store.save_runtime_snapshot(snapshot)
        return snapshot

    async def _run_cycle_async(
        self,
        *,
        selected_tasks: list[Task],
        runtime: SubagentRuntime,
        stage_target: str,
        cycle_started_at: datetime,
    ) -> _CycleOutcome:
        """Run all selected tasks concurrently via asyncio.gather.

        Each task acquires its lease + active_run, dispatches the async
        SubagentRuntime, and tears down its records on completion. LLM
        calls overlap; SQLite writes are serialized by the connection
        but are fast relative to the LLM round-trip.
        """

        async def run_one(task: Task) -> dict | None:
            lease = self._lease_for(task, cycle_started_at)
            if not self.store.save_runtime_lease(lease):
                return None
            run = self._active_run_for(task, stage_target, cycle_started_at, lease.lease_id)
            self.store.save_active_run(run)
            outcome: dict = {"started": True, "accepted": False, "error": None, "run": run}
            try:
                await runtime.run_task_async(task, stage_target=stage_target)
                if self.store.load_task(task.task_id).status is TaskStatus.ACCEPTED:
                    outcome["accepted"] = True
            except Exception as exc:
                diagnostics = getattr(exc, "diagnostics", None)
                error: dict = {
                    "task_id": task.task_id,
                    "stage": run.stage,
                    "provider_target": run.provider_target,
                    "error_kind": self._error_kind(exc, diagnostics),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
                if isinstance(diagnostics, dict):
                    error["diagnostics"] = diagnostics
                outcome["error"] = error
            finally:
                self.store.delete_active_run(run.run_id)
                self.store.delete_runtime_lease(lease.lease_id)
            return outcome

        results = await asyncio.gather(*(run_one(task) for task in selected_tasks))
        started = 0
        accepted = 0
        failed = 0
        errors: list[dict] = []
        for outcome in results:
            if outcome is None:
                continue
            started += 1
            if outcome["accepted"]:
                accepted += 1
            if outcome["error"] is not None:
                failed += 1
                errors.append(outcome["error"])
        return _CycleOutcome(started=started, accepted=accepted, failed=failed, errors=errors)

    def _lease_for(self, task: Task, acquired_at: datetime) -> RuntimeLease:
        lease_id = f"lease-{uuid4().hex}"
        return RuntimeLease(
            lease_id=lease_id,
            task_id=task.task_id,
            stage="qc" if task.status is TaskStatus.QC and task.metadata.get("runtime_next_stage") == "qc" else "annotation",
            acquired_at=acquired_at,
            heartbeat_at=acquired_at,
            expires_at=acquired_at + timedelta(seconds=self.config.stale_after_seconds),
            owner="local-runtime-scheduler",
            metadata={"runtime": "local_file"},
        )

    def _active_run_for(self, task: Task, stage_target: str, started_at: datetime, lease_id: str) -> ActiveRun:
        run_stage = "qc" if task.status is TaskStatus.QC and task.metadata.get("runtime_next_stage") == "qc" else "annotation"
        return ActiveRun(
            run_id=f"run-{uuid4().hex}",
            task_id=task.task_id,
            stage=run_stage,
            attempt_id=f"{task.task_id}-attempt-{task.current_attempt + 1}",
            provider_target="qc" if run_stage == "qc" else stage_target,
            started_at=started_at,
            heartbeat_at=started_at,
            metadata={"lease_id": lease_id},
        )

    def _error_kind(self, exc: Exception, diagnostics: object) -> str:
        if isinstance(diagnostics, dict) and isinstance(diagnostics.get("error_kind"), str):
            return diagnostics["error_kind"]
        if isinstance(exc, TimeoutError):
            return "timeout"
        return "provider_unavailable"
