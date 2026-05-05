from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.runtime import ActiveRun, RuntimeConfig, RuntimeCycleStats, RuntimeSnapshot
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

        existing_active_count = len(self.store.list_active_runs())
        capacity_available = max(self.config.max_concurrent_tasks - existing_active_count, 0)
        start_limit = min(capacity_available, self.config.max_starts_per_cycle)
        pending_tasks = [task for task in self.store.list_tasks() if task.status is TaskStatus.PENDING]
        selected_tasks = pending_tasks[:start_limit]

        runtime = SubagentRuntime(store=self.store, client_factory=self.client_factory)
        started = 0
        accepted = 0
        failed = 0
        errors = []

        for task in selected_tasks:
            run = self._active_run_for(task, stage_target, cycle_started_at)
            self.store.save_active_run(run)
            started += 1
            try:
                runtime.run_task(task, stage_target=stage_target)
                if self.store.load_task(task.task_id).status is TaskStatus.ACCEPTED:
                    accepted += 1
            except Exception as exc:
                failed += 1
                error = {
                    "task_id": task.task_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
                diagnostics = getattr(exc, "diagnostics", None)
                if isinstance(diagnostics, dict):
                    error["diagnostics"] = diagnostics
                errors.append(error)
            finally:
                self.store.delete_active_run(run.run_id)

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

    def _active_run_for(self, task: Task, stage_target: str, started_at: datetime) -> ActiveRun:
        return ActiveRun(
            run_id=f"run-{uuid4().hex}",
            task_id=task.task_id,
            stage="annotation",
            attempt_id=f"{task.task_id}-attempt-{task.current_attempt + 1}",
            provider_target=stage_target,
            started_at=started_at,
            heartbeat_at=started_at,
        )
