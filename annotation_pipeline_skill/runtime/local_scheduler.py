from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.runtime import ActiveRun, RuntimeConfig, RuntimeCycleStats, RuntimeLease, RuntimeSnapshot
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.llm.client import LLMClient
from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot
from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
from annotation_pipeline_skill.store.file_store import FileStore


class LocalRuntimeScheduler:
    def __init__(
        self,
        store: FileStore,
        client_factory: Callable[[str], LLMClient],
        config: RuntimeConfig,
    ):
        self.store = store
        self.client_factory = client_factory
        self.config = config

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

        runtime = SubagentRuntime(store=self.store, client_factory=self.client_factory)
        started = 0
        accepted = 0
        failed = 0
        errors = []

        for task in selected_tasks:
            lease = self._lease_for(task, cycle_started_at)
            if not self.store.save_runtime_lease(lease):
                continue
            run = self._active_run_for(task, stage_target, cycle_started_at, lease.lease_id)
            self.store.save_active_run(run)
            started += 1
            try:
                runtime.run_task(task, stage_target=stage_target)
                if self.store.load_task(task.task_id).status is TaskStatus.ACCEPTED:
                    accepted += 1
            except Exception as exc:
                failed += 1
                diagnostics = getattr(exc, "diagnostics", None)
                error = {
                    "task_id": task.task_id,
                    "stage": run.stage,
                    "provider_target": run.provider_target,
                    "error_kind": self._error_kind(exc, diagnostics),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
                if isinstance(diagnostics, dict):
                    error["diagnostics"] = diagnostics
                errors.append(error)
            finally:
                self.store.delete_active_run(run.run_id)
                self.store.delete_runtime_lease(lease.lease_id)

        cycle_finished_at = now or datetime.now(timezone.utc)
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
