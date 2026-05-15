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
        # Pre-flight cleanup at init:
        # 1. Clear stale leases/active_runs from a previous (dead) scheduler.
        # 2. ARBITRATING zombies → HUMAN_REVIEW (arbiter already had a turn;
        #    re-running automatically isn't useful).
        # ANNOTATING / QC orphans are NOT reset here — _try_claim_task now
        # picks them up directly and either resumes from QC (if an
        # annotation_result exists) or transitions to PENDING (to re-run
        # annotation). A delayed sweep in the observer coroutine catches
        # anything that no worker has claimed after a settle window.
        self._clear_stale_records()
        self._recover_arbitrating_zombies()

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

    def _recover_arbitrating_zombies(self) -> None:
        """Send orphaned ARBITRATING tasks to HUMAN_REVIEW.

        Called at scheduler init. ARBITRATING means the arbiter already had
        a turn — auto-re-arbitrating without operator intent isn't useful, so
        we route to HR instead. The operator can drag back to Arbitration
        manually (re-arbitrate flow) when they want another pass.

        ANNOTATING / QC orphans are handled by ``_try_claim_task`` (resume
        path) and the delayed-sweep observer (in ``run_forever``) — those
        keep partial work instead of pre-emptively resetting.
        """
        from annotation_pipeline_skill.core.transitions import InvalidTransition, transition_task

        leased_task_ids = {lease.task_id for lease in self.store.list_runtime_leases()}
        active_run_task_ids = {run.task_id for run in self.store.list_active_runs()}
        recovered = 0
        for task in self.store.list_tasks_by_status({TaskStatus.ARBITRATING}):
            if task.task_id in leased_task_ids or task.task_id in active_run_task_ids:
                continue
            try:
                event = transition_task(
                    task, TaskStatus.HUMAN_REVIEW,
                    actor="scheduler",
                    reason="zombie recovery: stuck in arbitrating without lease; routing to human review",
                    stage="recovery",
                    metadata={"recovery": "zombie", "previous_status": "arbitrating"},
                )
            except InvalidTransition:
                continue
            self.store.save_task(task)
            self.store.append_event(event)
            recovered += 1
        if recovered:
            import sys
            print(f"[scheduler] recovered {recovered} ARBITRATING zombies → HR", file=sys.stderr)

    def _delayed_sweep_unclaimed_orphans(self) -> None:
        """Catch ANNOTATING / QC tasks that no worker claimed during the
        settle window. Called periodically by the observer coroutine.

        A task is an "unclaimed orphan" if:
          - status is ANNOTATING or QC
          - has NO runtime_lease pointing at it
          - has NO active_run pointing at it

        Such a task slipped past ``_try_claim_task`` (e.g., because its
        artifacts were in a weird state, or it lost a race) — reset to
        PENDING so the natural pipeline retries it from the top.
        """
        from annotation_pipeline_skill.core.transitions import InvalidTransition, transition_task

        leased = {l.task_id for l in self.store.list_runtime_leases()}
        active = {r.task_id for r in self.store.list_active_runs()}
        recovered = 0
        for task in self.store.list_tasks_by_status({TaskStatus.ANNOTATING, TaskStatus.QC}):
            if task.task_id in leased or task.task_id in active:
                continue
            try:
                event = transition_task(
                    task, TaskStatus.PENDING,
                    actor="scheduler",
                    reason=f"delayed sweep: still unclaimed in {task.status.value} after settle window; resetting to pending",
                    stage="recovery",
                    metadata={"recovery": "delayed_sweep", "previous_status": task.status.value},
                )
            except InvalidTransition:
                continue
            self.store.save_task(task)
            self.store.append_event(event)
            recovered += 1
        if recovered:
            import sys
            print(f"[scheduler] delayed-sweep reset {recovered} unclaimed orphans → pending", file=sys.stderr)

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
                    # Hard upper bound on a single task's run. If an LLM call
                    # (codex subprocess, HTTP stream) hangs past this, we cancel
                    # so the finally clause releases the lease/active_run and
                    # the task gets recycled instead of zombifying the worker.
                    await asyncio.wait_for(
                        runtime.run_task_async(task, stage_target=stage_target),
                        timeout=self.config.worker_task_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    import sys
                    print(
                        f"[scheduler] worker_task_timeout: task={task.task_id} "
                        f"after {self.config.worker_task_timeout_seconds}s; "
                        f"releasing lease and recycling",
                        file=sys.stderr,
                    )
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
            # Settle window: give workers time to claim ANNOTATING / QC
            # orphans (via _try_claim_task's resume logic) before sweeping
            # any leftovers back to PENDING. Tasks the workers DO claim get
            # natural pipeline progression; tasks they DON'T (artifact
            # weirdness, lost races) get reset by the sweep.
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.config.resume_settle_seconds)
            except asyncio.TimeoutError:
                pass
            if not stop.is_set():
                self._delayed_sweep_unclaimed_orphans()
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

        Claimable statuses:
          PENDING        — fresh tasks; worker runs the full pipeline
          QC (resume)    — tasks whose annotation is done; metadata flag
                           ``runtime_next_stage=qc`` directs the runtime to
                           skip back to QC. Set either by the auto pipeline
                           when transitioning ANNOTATING→QC, or by this
                           function as part of resume-on-restart.
          ANNOTATING     — orphaned mid-pipeline (after a runtime restart).
                           If the task has an annotation_result artifact, we
                           promote it back to QC with runtime_next_stage=qc
                           so the worker resumes from QC instead of re-running
                           annotation. If there's no annotation_result yet, we
                           reset to PENDING so a worker re-runs annotation.
          ARBITRATING    — human-dragged HR / REJECTED cards (re-arbitrate
                           flow) — the worker calls the arbiter on them.
        """
        candidates = self.store.list_tasks_by_status(
            {TaskStatus.PENDING, TaskStatus.QC, TaskStatus.ARBITRATING, TaskStatus.ANNOTATING}
        )
        # Skip tasks that another worker is already running. Without this,
        # the ANNOTATING resume branch below would bounce a live in-flight
        # task back to PENDING on every claim attempt, producing a flood of
        # spurious annotating→pending→annotating audit events and inflating
        # apparent throughput. Match what _delayed_sweep_unclaimed_orphans
        # already does for the same reason.
        leased = {l.task_id for l in self.store.list_runtime_leases()}
        active = {r.task_id for r in self.store.list_active_runs()}
        for candidate in candidates:
            if candidate.task_id in leased or candidate.task_id in active:
                continue
            if candidate.status is TaskStatus.QC and candidate.metadata.get("runtime_next_stage") != "qc":
                continue
            if candidate.status is TaskStatus.ANNOTATING:
                # Genuine restart-orphan path: inspect artifacts to choose entry stage.
                self._prepare_annotating_for_resume(candidate)
                # _prepare_annotating_for_resume may have transitioned the
                # task — reload to get current status.
                candidate = self.store.load_task(candidate.task_id)
            acquired_at = self._now_fn()
            lease = self._lease_for(candidate, acquired_at)
            if not self.store.save_runtime_lease(lease):
                continue
            run = self._active_run_for(candidate, stage_target, acquired_at, lease.lease_id)
            self.store.save_active_run(run)
            return candidate, lease, run
        return None

    def _prepare_annotating_for_resume(self, task: Task) -> None:
        """Decide whether an orphaned ANNOTATING task resumes from QC or
        restarts from annotation, based on which artifacts already exist.

        - Has annotation_result + no qc_result for the same attempt → set
          ``runtime_next_stage=qc`` and transition status to QC. The worker
          will pick the QC-only resume path in SubagentRuntime.
        - Otherwise → transition to PENDING so a worker re-runs annotation
          (and the prelabel-reuse fast path picks up any pre-existing
          annotation_result on attempt 0).
        """
        from annotation_pipeline_skill.core.transitions import InvalidTransition, transition_task

        artifacts = self.store.list_artifacts(task.task_id)
        # An annotation artifact exists AND no qc_result follows it in
        # insertion order → resume at QC. ``list_artifacts`` returns
        # artifacts in insertion (seq) order, so a positional walk
        # captures the temporal relationship without a dedicated seq field.
        last_annotation_idx = None
        for idx, art in enumerate(artifacts):
            if art.kind == "annotation_result":
                last_annotation_idx = idx
        resume_qc = False
        if last_annotation_idx is not None:
            seen_qc_after = any(
                a.kind == "qc_result"
                for a in artifacts[last_annotation_idx + 1:]
            )
            resume_qc = not seen_qc_after
        try:
            if resume_qc:
                task.metadata["runtime_next_stage"] = "qc"
                event = transition_task(
                    task, TaskStatus.QC,
                    actor="scheduler",
                    reason="resume on restart: annotation artifact already present, skipping to QC",
                    stage="recovery",
                    metadata={"resume": "annotating_to_qc"},
                )
            else:
                event = transition_task(
                    task, TaskStatus.PENDING,
                    actor="scheduler",
                    reason="resume on restart: no annotation artifact yet, restart from annotation",
                    stage="recovery",
                    metadata={"resume": "annotating_to_pending"},
                )
        except InvalidTransition:
            return
        self.store.save_task(task)
        self.store.append_event(event)

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
