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


def test_scheduler_arbitrating_zombies_to_hr_on_init(tmp_path):
    """ARBITRATING tasks without an active lease are routed to HUMAN_REVIEW
    at scheduler init — the arbiter already had a turn, auto-re-running
    without operator intent isn't useful. ANNOTATING / QC orphans are NOT
    touched here (see resume tests below); they're handled by
    _try_claim_task's resume path or the delayed sweep."""
    from datetime import datetime, timezone
    from annotation_pipeline_skill.core.models import Task as _Task
    from annotation_pipeline_skill.core.states import TaskStatus as _TS

    store = SqliteStore.open(tmp_path)
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    annot = _Task.new(task_id="zombie-annot", pipeline_id="p", source_ref={"kind": "jsonl"})
    annot.status = _TS.ANNOTATING
    arb = _Task.new(task_id="zombie-arb", pipeline_id="p", source_ref={"kind": "jsonl"})
    arb.status = _TS.ARBITRATING
    store.save_task(annot)
    store.save_task(arb)

    LocalRuntimeScheduler(
        store=store,
        client_factory=passing_client_factory,
        config=RuntimeConfig(stale_after_seconds=600),
        now_fn=lambda: now,
    )

    assert store.load_task("zombie-arb").status is _TS.HUMAN_REVIEW
    # ANNOTATING is preserved at init; resume / delayed-sweep handles it.
    assert store.load_task("zombie-annot").status is _TS.ANNOTATING


def test_try_claim_resumes_annotating_to_qc_when_annotation_artifact_exists(tmp_path):
    """An ANNOTATING task with an annotation_result artifact but no qc_result
    after it should be resumed at the QC stage on next claim: status → QC,
    metadata.runtime_next_stage = "qc"."""
    from annotation_pipeline_skill.core.models import ArtifactRef

    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="resume-qc", pipeline_id="p", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ANNOTATING
    store.save_task(task)
    # Seed an annotation_result artifact — would normally exist from a
    # half-finished pipeline cycle before a restart.
    artifact_path = "artifact_payloads/resume-qc/annotation.json"
    (store.root / artifact_path).parent.mkdir(parents=True, exist_ok=True)
    (store.root / artifact_path).write_text('{"text": "{}"}', encoding="utf-8")
    store.append_artifact(ArtifactRef.new(
        task_id="resume-qc", kind="annotation_result", path=artifact_path,
        content_type="application/json",
    ))

    scheduler = LocalRuntimeScheduler(
        store=store, client_factory=passing_client_factory,
        config=RuntimeConfig(max_concurrent_tasks=1),
    )
    claim = scheduler._try_claim_task("annotation")
    assert claim is not None
    claimed_task, _, _ = claim
    assert claimed_task.status is TaskStatus.QC
    assert claimed_task.metadata.get("runtime_next_stage") == "qc"


def test_try_claim_resets_annotating_to_pending_when_no_annotation_artifact(tmp_path):
    """An ANNOTATING task with NO annotation_result yet must restart from
    annotation — _try_claim_task transitions it to PENDING so a worker picks
    it up via the normal entry path."""
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="resume-pending", pipeline_id="p", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ANNOTATING
    store.save_task(task)

    scheduler = LocalRuntimeScheduler(
        store=store, client_factory=passing_client_factory,
        config=RuntimeConfig(max_concurrent_tasks=1),
    )
    claim = scheduler._try_claim_task("annotation")
    assert claim is not None
    claimed_task, _, _ = claim
    assert claimed_task.status is TaskStatus.PENDING


def test_delayed_sweep_resets_truly_orphaned_in_flight_tasks(tmp_path):
    """_delayed_sweep_unclaimed_orphans is the safety net: any ANNOTATING /
    QC task with no lease and no active_run gets reset to PENDING.
    """
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="sweep-me", pipeline_id="p", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ANNOTATING
    store.save_task(task)

    scheduler = LocalRuntimeScheduler(
        store=store, client_factory=passing_client_factory,
        config=RuntimeConfig(max_concurrent_tasks=1),
    )
    # Don't go through _try_claim_task — exercise the sweep directly.
    scheduler._delayed_sweep_unclaimed_orphans()

    assert store.load_task("sweep-me").status is TaskStatus.PENDING


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
