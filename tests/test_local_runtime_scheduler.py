from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.runtime import RuntimeConfig
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.llm.client import LLMGenerateResult
from annotation_pipeline_skill.runtime.local_scheduler import LocalRuntimeScheduler
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


class StubLLMClient:
    def __init__(self, final_text: str):
        self.final_text = final_text

    async def generate(self, request):
        return LLMGenerateResult(
            runtime="test_runtime",
            provider="test_provider",
            model="test-model",
            continuity_handle=None,
            final_text=self.final_text,
            usage={"total_tokens": 10},
            raw_response={"id": "test"},
            diagnostics={},
        )


def passing_client_factory(target):
    if target == "qc":
        return StubLLMClient('{"passed": true, "summary": "acceptable"}')
    return StubLLMClient('{"labels":[]}')


class FailingLLMClient:
    async def generate(self, request):
        raise RuntimeError("provider unavailable")


class DiagnosticProviderError(RuntimeError):
    def __init__(self):
        super().__init__("local CLI provider failed")
        self.diagnostics = {"stderr": "resume thread not found", "returncode": 1}


class FailingDiagnosticLLMClient:
    async def generate(self, request):
        raise DiagnosticProviderError()


def test_local_runtime_scheduler_drains_pending_within_capacity(tmp_path):
    """run_until_idle keeps recruiting PENDING tasks until the queue empties,
    bounded only by max_concurrent_tasks worker coroutines."""
    store = SqliteStore.open(tmp_path)
    for index in range(1, 4):
        task = Task.new(task_id=f"task-{index}", pipeline_id="pipe", source_ref={"kind": "jsonl"})
        task.status = TaskStatus.PENDING
        store.save_task(task)
    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=passing_client_factory,
        config=RuntimeConfig(max_concurrent_tasks=4),
    )

    snapshot = scheduler.run_until_idle(stage_target="annotation")

    assert snapshot.queue_counts.accepted == 3
    assert snapshot.queue_counts.pending == 0


def test_local_runtime_scheduler_cleans_active_run_after_success(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=passing_client_factory,
        config=RuntimeConfig(max_concurrent_tasks=1),
    )

    scheduler.run_until_idle(stage_target="annotation")

    assert store.list_active_runs() == []
    assert store.load_task("task-1").status is TaskStatus.ACCEPTED


def test_local_runtime_scheduler_cleans_records_after_failure(tmp_path):
    """A worker that crashes during the pipeline still releases its lease /
    active_run, so the worker pool stays healthy and the failed task remains
    on the queue for the next attempt."""
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=lambda target: FailingLLMClient(),
        config=RuntimeConfig(max_concurrent_tasks=1),
    )

    snapshot = scheduler.run_until_idle(stage_target="annotation", max_tasks=1)

    assert store.list_active_runs() == []
    assert store.list_runtime_leases() == []
    assert snapshot is not None
    assert store.load_runtime_snapshot() == snapshot


def test_scheduler_clears_stale_active_runs_on_construction(tmp_path):
    from datetime import datetime, timedelta, timezone
    from annotation_pipeline_skill.core.runtime import ActiveRun, RuntimeLease

    store = SqliteStore.open(tmp_path)
    fixed_now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    stale_after = 600  # seconds (default)
    # Stale heartbeat: now - (stale_after + 60s).
    stale_heartbeat = fixed_now - timedelta(seconds=stale_after + 60)
    store.save_active_run(
        ActiveRun(
            run_id="run-stale",
            task_id="ghost-task",
            stage="annotation",
            attempt_id="attempt-stale",
            provider_target="annotation",
            started_at=stale_heartbeat,
            heartbeat_at=stale_heartbeat,
        )
    )
    store.save_runtime_lease(
        RuntimeLease(
            lease_id="lease-stale",
            task_id="ghost-task",
            stage="annotation",
            acquired_at=stale_heartbeat,
            heartbeat_at=stale_heartbeat,
            expires_at=stale_heartbeat + timedelta(seconds=stale_after),
            owner="dead-scheduler",
        )
    )

    LocalRuntimeScheduler(
        store=store,
        client_factory=passing_client_factory,
        config=RuntimeConfig(stale_after_seconds=stale_after),
        now_fn=lambda: fixed_now,
    )

    assert store.list_active_runs() == []
    assert store.list_runtime_leases() == []


def test_workers_run_in_parallel(tmp_path):
    """Worker pool runs tasks concurrently. Each LLM call sleeps 0.5s — serial
    execution of 4 tasks would be ~4 * 1.0s = 4s; the pool should finish in
    ~1s (one annotation + one QC round-trip overlapping across workers)."""
    import asyncio as _asyncio
    import time

    sleep_seconds = 0.5

    class SlowClient:
        def __init__(self, final_text: str):
            self.final_text = final_text

        async def generate(self, request):
            await _asyncio.sleep(sleep_seconds)
            return LLMGenerateResult(
                runtime="test_runtime",
                provider="test_provider",
                model="test-model",
                continuity_handle=None,
                final_text=self.final_text,
                usage={"total_tokens": 1},
                raw_response={"id": "test"},
                diagnostics={},
            )

    def slow_factory(target):
        if target == "qc":
            return SlowClient('{"passed": true, "summary": "ok"}')
        return SlowClient('{"labels":[]}')

    store = SqliteStore.open(tmp_path)
    for index in range(1, 5):
        task = Task.new(
            task_id=f"task-{index}",
            pipeline_id="pipe",
            source_ref={"kind": "jsonl"},
        )
        task.status = TaskStatus.PENDING
        store.save_task(task)

    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=slow_factory,
        config=RuntimeConfig(max_concurrent_tasks=8),
    )

    t0 = time.monotonic()
    snapshot = scheduler.run_until_idle(stage_target="annotation")
    wall_seconds = time.monotonic() - t0

    assert snapshot.queue_counts.accepted == 4
    # Serial would be 4 * (0.5 + 0.5) = 4.0s; parallel ~1.0s. Allow generous slack.
    assert wall_seconds < 2.0, f"expected parallel speedup, wall={wall_seconds:.2f}s"


def test_scheduler_does_not_clear_fresh_active_runs_on_construction(tmp_path):
    from datetime import datetime, timedelta, timezone
    from annotation_pipeline_skill.core.runtime import ActiveRun, RuntimeLease

    store = SqliteStore.open(tmp_path)
    fixed_now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    stale_after = 600
    fresh_heartbeat = fixed_now - timedelta(seconds=30)  # well within threshold
    store.save_active_run(
        ActiveRun(
            run_id="run-fresh",
            task_id="live-task",
            stage="annotation",
            attempt_id="attempt-fresh",
            provider_target="annotation",
            started_at=fresh_heartbeat,
            heartbeat_at=fresh_heartbeat,
        )
    )
    store.save_runtime_lease(
        RuntimeLease(
            lease_id="lease-fresh",
            task_id="live-task",
            stage="annotation",
            acquired_at=fresh_heartbeat,
            heartbeat_at=fresh_heartbeat,
            expires_at=fresh_heartbeat + timedelta(seconds=stale_after),
            owner="live-scheduler",
        )
    )

    LocalRuntimeScheduler(
        store=store,
        client_factory=passing_client_factory,
        config=RuntimeConfig(stale_after_seconds=stale_after),
        now_fn=lambda: fixed_now,
    )

    assert len(store.list_active_runs()) == 1
    assert len(store.list_runtime_leases()) == 1


def test_workers_drain_many_tasks_with_small_pool(tmp_path):
    """A pool of just 2 workers still drains 10 PENDING tasks — each worker
    claims the next task as soon as it's free. There's no batch boundary."""
    store = SqliteStore.open(tmp_path)
    for index in range(1, 11):
        task = Task.new(task_id=f"task-{index}", pipeline_id="pipe", source_ref={"kind": "jsonl"})
        task.status = TaskStatus.PENDING
        store.save_task(task)
    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=passing_client_factory,
        config=RuntimeConfig(max_concurrent_tasks=2),
    )

    snapshot = scheduler.run_until_idle(stage_target="annotation")

    assert snapshot.queue_counts.accepted == 10
    assert snapshot.queue_counts.pending == 0


def test_scheduler_recovers_zombie_tasks_on_construction(tmp_path):
    """Tasks left in ANNOTATING / QC / ARBITRATING by a crashed scheduler
    (no active lease, no recent update) should be recovered on the next
    scheduler init: in-flight statuses to PENDING, ARBITRATING to HR."""
    from datetime import datetime, timedelta, timezone
    from annotation_pipeline_skill.core.models import Task as _Task
    from annotation_pipeline_skill.core.states import TaskStatus as _TS

    store = SqliteStore.open(tmp_path)
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    stale = now - timedelta(seconds=601)
    # Two zombie tasks across in-flight statuses.
    annot = _Task.new(task_id="zombie-annot", pipeline_id="p", source_ref={"kind": "jsonl"})
    annot.status = _TS.ANNOTATING
    annot.updated_at = stale
    arb = _Task.new(task_id="zombie-arb", pipeline_id="p", source_ref={"kind": "jsonl"})
    arb.status = _TS.ARBITRATING
    arb.updated_at = stale
    # Recently-updated task (should NOT be recovered).
    fresh = _Task.new(task_id="fresh-annot", pipeline_id="p", source_ref={"kind": "jsonl"})
    fresh.status = _TS.ANNOTATING
    fresh.updated_at = now
    store.save_task(annot)
    store.save_task(arb)
    store.save_task(fresh)

    LocalRuntimeScheduler(
        store=store,
        client_factory=passing_client_factory,
        config=RuntimeConfig(stale_after_seconds=600),
        now_fn=lambda: now,
    )

    assert store.load_task("zombie-annot").status is _TS.PENDING
    assert store.load_task("zombie-arb").status is _TS.HUMAN_REVIEW
    assert store.load_task("fresh-annot").status is _TS.ANNOTATING


def test_worker_task_timeout_releases_lease_on_hung_llm_call(tmp_path):
    """If an LLM call hangs forever, the worker's asyncio.wait_for kicks in,
    cancels the task, and the finally clause releases the lease/active_run.
    The task stays claimable for the next worker run."""
    import asyncio

    class HangingLLMClient:
        async def generate(self, request):
            # Simulate an HTTP/CLI call that never returns.
            await asyncio.sleep(60)
            return None  # never reached

    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="hang-task", pipeline_id="p", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.PENDING
    store.save_task(task)

    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=lambda target: HangingLLMClient(),
        config=RuntimeConfig(
            max_concurrent_tasks=1,
            worker_task_timeout_seconds=1,  # 1s — wait_for fires fast
        ),
    )

    # max_tasks=1 stops the pool after one completion (timeout counts as one)
    scheduler.run_until_idle(stage_target="annotation", max_tasks=1)

    # Lease/active_run released even though the LLM never returned
    assert store.list_runtime_leases() == []
    assert store.list_active_runs() == []
