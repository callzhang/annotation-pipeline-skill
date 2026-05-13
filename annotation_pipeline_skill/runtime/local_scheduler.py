from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.runtime import ActiveRun, RuntimeConfig, RuntimeLease, RuntimeSnapshot
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.llm.client import LLMClient
from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot
from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


class LocalRuntimeScheduler:
    """Worker-pool runtime.

    ``max_concurrent_tasks`` worker coroutines run in parallel. Each worker
    claims one PENDING (or QC-resume) task at a time, runs the full
    annotation→validation→QC pipeline through ``SubagentRuntime``, releases
    its lease, then immediately claims the next task. A separate observer
    coroutine snapshots the runtime state every
    ``snapshot_interval_seconds``. There are no cycles, no batches, and no
    drain barriers — a slow task only ties up one worker slot.
    """

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
        """Drop leases / active_runs whose heartbeat is older than the stale window.

        Called at construction so a freshly-restarted scheduler doesn't count
        leftover rows from a previously-killed instance toward in-flight
        capacity. Fresh rows from a still-live scheduler are left alone.
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

    async def run_forever(
        self,
        *,
        stage_target: str = "annotation",
        stop_event: asyncio.Event | None = None,
        max_tasks: int | None = None,
        stop_when_idle: bool = False,
    ) -> int:
        """Spin up the worker pool and run until ``stop_event`` is set.

        - ``max_tasks``: optional ceiling — stop after that many task
          completions (useful for sized smoke runs).
        - ``stop_when_idle``: stop once no PENDING tasks remain and no worker
          is busy (used by tests and one-shot CLI helpers).

        Returns the number of tasks processed.
        """
        stop = stop_event or asyncio.Event()
        runtime = SubagentRuntime(
            store=self.store,
            client_factory=self.client_factory,
            max_qc_rounds=self.config.max_qc_rounds,
            config=self.config,
        )

        completed = 0
        busy_workers = 0

        async def worker() -> None:
            nonlocal completed, busy_workers
            while not stop.is_set():
                claim = self._try_claim_task(stage_target)
                if claim is None:
                    if stop_when_idle and busy_workers == 0:
                        stop.set()
                        return
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                    continue
                task, lease, run = claim
                busy_workers += 1
                try:
                    await runtime.run_task_async(task, stage_target=stage_target)
                except Exception:
                    # SubagentRuntime captures errors on the attempt record; the
                    # worker only needs to release records and keep going.
                    pass
                finally:
                    self.store.delete_active_run(run.run_id)
                    self.store.delete_runtime_lease(lease.lease_id)
                    busy_workers -= 1
                    completed += 1
                    if max_tasks is not None and completed >= max_tasks:
                        stop.set()

        async def observer() -> None:
            self._write_snapshot()
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.config.snapshot_interval_seconds)
                except asyncio.TimeoutError:
                    pass
                self._write_snapshot()

        worker_tasks = [
            asyncio.create_task(worker()) for _ in range(self.config.max_concurrent_tasks)
        ]
        observer_task = asyncio.create_task(observer())
        try:
            await asyncio.gather(*worker_tasks, observer_task)
        except asyncio.CancelledError:
            stop.set()
            await asyncio.gather(*worker_tasks, observer_task, return_exceptions=True)
            raise
        return completed

    def run_until_idle(self, stage_target: str = "annotation", *, max_tasks: int | None = None) -> RuntimeSnapshot:
        """Synchronous helper: run the pool until PENDING is drained.

        Convenience for tests and the ``run-cycle`` / ``runtime once`` CLI
        commands. Equivalent to ``run_forever(stop_when_idle=True)`` plus a
        final snapshot write.
        """
        asyncio.run(self.run_forever(stage_target=stage_target, stop_when_idle=True, max_tasks=max_tasks))
        return self._write_snapshot()

    def _try_claim_task(self, stage_target: str) -> tuple[Task, RuntimeLease, ActiveRun] | None:
        """Pick the next runnable task and reserve it.

        Returns ``None`` when no task is runnable. Workers are all in the
        same asyncio event loop with a synchronous SQLite store, so this
        method does not need a lock — only one worker runs at a time
        between awaits.

        Queries only PENDING / QC / ARBITRATING tasks (covered by
        ``idx_tasks_status_created``) so the worker pool stays cheap to poll
        even at 41k+ task volumes. ARBITRATING tasks are queued by humans
        dragging REJECTED / HR cards into the Arbitration column (re-arbitrate
        flow) — the worker calls into SubagentRuntime to run the arbiter on
        them.
        """
        candidates = self.store.list_tasks_by_status(
            {TaskStatus.PENDING, TaskStatus.QC, TaskStatus.ARBITRATING}
        )
        for candidate in candidates:
            if candidate.status is TaskStatus.QC and candidate.metadata.get("runtime_next_stage") != "qc":
                continue
            acquired_at = self._now_fn()
            lease = self._lease_for(candidate, acquired_at)
            if not self.store.save_runtime_lease(lease):
                continue
            run = self._active_run_for(candidate, stage_target, acquired_at, lease.lease_id)
            self.store.save_active_run(run)
            return candidate, lease, run
        return None

    def _write_snapshot(self) -> RuntimeSnapshot:
        now = self._now_fn()
        self.store.save_runtime_heartbeat(now)
        snapshot = build_runtime_snapshot(self.store, self.config, now=now)
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
