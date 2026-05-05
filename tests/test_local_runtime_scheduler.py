from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.runtime import RuntimeConfig
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.llm.client import LLMGenerateResult
from annotation_pipeline_skill.runtime.local_scheduler import LocalRuntimeScheduler
from annotation_pipeline_skill.store.file_store import FileStore


class StubLLMClient:
    async def generate(self, request):
        return LLMGenerateResult(
            runtime="test_runtime",
            provider="test_provider",
            model="test-model",
            continuity_handle=None,
            final_text='{"labels":[]}',
            usage={"total_tokens": 10},
            raw_response={"id": "test"},
            diagnostics={},
        )


class FailingLLMClient:
    async def generate(self, request):
        raise RuntimeError("provider unavailable")


def test_local_runtime_scheduler_respects_max_starts_per_cycle(tmp_path):
    store = FileStore(tmp_path)
    for index in range(1, 4):
        task = Task.new(task_id=f"task-{index}", pipeline_id="pipe", source_ref={"kind": "jsonl"})
        task.status = TaskStatus.PENDING
        store.save_task(task)
    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=lambda target: StubLLMClient(),
        config=RuntimeConfig(max_concurrent_tasks=4, max_starts_per_cycle=2),
    )

    snapshot = scheduler.run_once(stage_target="annotation")

    assert snapshot.queue_counts.accepted == 2
    assert snapshot.queue_counts.pending == 1
    assert snapshot.cycle_stats[-1].started == 2
    assert snapshot.cycle_stats[-1].accepted == 2


def test_local_runtime_scheduler_respects_existing_active_capacity(tmp_path):
    from datetime import datetime, timezone
    from annotation_pipeline_skill.core.runtime import ActiveRun

    store = FileStore(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    store.save_active_run(
        ActiveRun(
            run_id="run-existing",
            task_id="existing-task",
            stage="annotation",
            attempt_id="attempt-1",
            provider_target="annotation",
            started_at=now,
            heartbeat_at=now,
        )
    )
    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=lambda target: StubLLMClient(),
        config=RuntimeConfig(max_concurrent_tasks=1, max_starts_per_cycle=2),
    )

    snapshot = scheduler.run_once(stage_target="annotation", now=now)

    assert snapshot.queue_counts.pending == 1
    assert snapshot.cycle_stats[-1].started == 0
    assert snapshot.cycle_stats[-1].capacity_available == 0


def test_local_runtime_scheduler_cleans_active_run_after_success(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=lambda target: StubLLMClient(),
        config=RuntimeConfig(max_concurrent_tasks=1, max_starts_per_cycle=1),
    )

    scheduler.run_once(stage_target="annotation")

    assert store.list_active_runs() == []
    assert store.load_task("task-1").status is TaskStatus.ACCEPTED


def test_local_runtime_scheduler_records_failure_and_returns_snapshot(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=lambda target: FailingLLMClient(),
        config=RuntimeConfig(max_concurrent_tasks=1, max_starts_per_cycle=1),
    )

    snapshot = scheduler.run_once(stage_target="annotation")

    assert store.list_active_runs() == []
    assert snapshot is not None
    assert store.load_runtime_snapshot() == snapshot
    assert snapshot.cycle_stats[-1].started == 1
    assert snapshot.cycle_stats[-1].failed == 1
    assert snapshot.cycle_stats[-1].accepted == 0
    assert snapshot.cycle_stats[-1].errors == [
        {
            "task_id": "task-1",
            "error_type": "RuntimeError",
            "message": "provider unavailable",
        }
    ]
