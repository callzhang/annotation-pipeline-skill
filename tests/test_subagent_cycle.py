import json

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import FeedbackSource, TaskStatus
from annotation_pipeline_skill.llm.client import LLMGenerateResult
from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime, SubagentRuntimeResult
from annotation_pipeline_skill.runtime.local_scheduler import LocalRuntimeScheduler
from annotation_pipeline_skill.core.runtime import RuntimeConfig
from annotation_pipeline_skill.services.feedback_service import build_feedback_consensus_summary
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


class StubLLMClient:
    def __init__(self, final_text='{"labels":[{"text":"alpha","type":"ENTITY"}]}', provider="test_provider"):
        self.final_text = final_text
        self.provider = provider
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)

        return LLMGenerateResult(
            runtime="test_runtime",
            provider=self.provider,
            model="test-model",
            continuity_handle="thread-1",
            final_text=self.final_text,
            usage={"total_tokens": 10},
            raw_response={"id": "test"},
            diagnostics={"queue_wait_ms": 0},
        )


def test_subagent_runtime_runs_annotation_and_qc_before_accepting(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl", "payload": {"text": "alpha"}})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    annotation_client = StubLLMClient(final_text='{"labels":[{"text":"alpha","type":"ENTITY"}]}', provider="annotator")
    qc_client = StubLLMClient(final_text='{"passed": true, "summary": "acceptable"}', provider="qc")
    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda target: qc_client if target == "qc" else annotation_client,
    )

    result = runtime.run_once(stage_target="annotation")

    loaded = store.load_task("task-1")
    attempts = store.list_attempts("task-1")
    artifacts = store.list_artifacts("task-1")
    assert isinstance(result, SubagentRuntimeResult)
    assert result.started == 1
    assert result.accepted == 1
    assert loaded.status is TaskStatus.ACCEPTED
    assert [attempt.stage for attempt in attempts] == ["annotation", "qc"]
    assert [attempt.provider_id for attempt in attempts] == ["annotator", "qc"]
    assert [artifact.kind for artifact in artifacts] == ["annotation_result", "qc_result"]
    assert artifacts[0].metadata["continuity_handle"] == "thread-1"
    assert store.list_feedback("task-1") == []
    assert "annotation_guidance" in annotation_client.requests[0].instructions
    assert "raw JSON" in qc_client.requests[0].instructions
    assert '"annotation_result"' in qc_client.requests[0].prompt


def test_subagent_runtime_accepts_markdown_fenced_qc_json(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl", "payload": {"text": "alpha"}})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda target: StubLLMClient(final_text='```json\n{"passed": true, "summary": "acceptable"}\n```'),
    )

    result = runtime.run_once(stage_target="annotation")

    loaded = store.load_task("task-1")
    assert result.accepted == 1
    assert loaded.status is TaskStatus.ACCEPTED
    assert store.list_feedback("task-1") == []


def test_subagent_runtime_records_qc_feedback_and_returns_task_to_pending(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl", "payload": {"text": "alpha"}})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda target: StubLLMClient(
            final_text='{"passed": false, "message": "missing entity", "category": "quality", "severity": "warning", "suggested_action": "annotator_rerun", "target": {"field": "labels"}}',
            provider="qc",
        )
        if target == "qc"
        else StubLLMClient(final_text='{"labels":[]}', provider="annotator"),
    )

    result = runtime.run_once(stage_target="annotation")

    loaded = store.load_task("task-1")
    feedback = store.list_feedback("task-1")
    assert result.started == 1
    assert result.accepted == 0
    assert result.failed == 0
    assert loaded.status is TaskStatus.PENDING
    assert feedback[0].source_stage is FeedbackSource.QC
    assert feedback[0].message == "missing entity"
    assert feedback[0].suggested_action == "annotator_rerun"
    assert store.list_artifacts("task-1")[-1].kind == "qc_result"


def test_local_scheduler_records_qc_parse_error_without_annotator_feedback_and_retries_qc(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl", "payload": {"text": "alpha"}})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    annotation_client = StubLLMClient(final_text='{"labels":[]}', provider="annotator")
    qc_clients = [
        StubLLMClient(final_text="not json", provider="qc"),
        StubLLMClient(final_text='{"passed": true, "summary": "acceptable"}', provider="qc"),
    ]

    def client_factory(target):
        return qc_clients.pop(0) if target == "qc" else annotation_client

    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=client_factory,
        config=RuntimeConfig(max_concurrent_tasks=1, max_starts_per_cycle=1),
    )

    first = scheduler.run_once(stage_target="annotation")
    second = scheduler.run_once(stage_target="annotation")

    attempts = store.list_attempts("task-1")
    assert first.cycle_stats[-1].failed == 1
    assert second.cycle_stats[-1].accepted == 1
    assert store.load_task("task-1").status is TaskStatus.ACCEPTED
    assert store.list_feedback("task-1") == []
    assert [attempt.stage for attempt in attempts] == ["annotation", "qc", "qc"]
    assert attempts[1].status.value == "failed"
    assert attempts[1].error["kind"] == "parse_error"
    assert len(annotation_client.requests) == 1


def test_subagent_runtime_qc_prompt_includes_task_qc_sampling_policy(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="task-1",
        pipeline_id="pipe",
        source_ref={"kind": "jsonl", "payload": {"rows": [{"text": "alpha"}, {"text": "beta"}]}},
        metadata={
            "qc_policy": {
                "mode": "sample_count",
                "row_count": 2,
                "sample_count": 1,
                "required_correct_rows": 1,
                "sample_scope": "per_task",
            }
        },
    )
    task.status = TaskStatus.PENDING
    store.save_task(task)
    annotation_client = StubLLMClient(final_text='{"labels":[]}', provider="annotator")
    qc_client = StubLLMClient(final_text='{"passed": true}', provider="qc")
    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda target: qc_client if target == "qc" else annotation_client,
    )

    runtime.run_once(stage_target="annotation")

    assert "qc_policy" in qc_client.requests[0].instructions
    assert "sample_count" in qc_client.requests[0].instructions
    assert '"sample_count": 1' in qc_client.requests[0].prompt


def test_subagent_runtime_rerun_prompt_includes_feedback_context(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl", "payload": {"text": "alpha"}})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    first_annotation_client = StubLLMClient(final_text='{"labels":[]}', provider="annotator")
    second_annotation_client = StubLLMClient(
        final_text='{"labels":[{"text":"alpha","type":"ENTITY"}]}',
        provider="annotator",
    )
    annotation_clients = [first_annotation_client, second_annotation_client]
    qc_clients = [
        StubLLMClient(final_text='{"passed": false, "message": "missing entity"}', provider="qc"),
        StubLLMClient(final_text='{"passed": true, "summary": "fixed"}', provider="qc"),
    ]

    def client_factory(target):
        return qc_clients.pop(0) if target == "qc" else annotation_clients.pop(0)

    runtime = SubagentRuntime(store=store, client_factory=client_factory)

    first = runtime.run_once(stage_target="annotation")
    second = runtime.run_once(stage_target="annotation")

    loaded = store.load_task("task-1")
    attempts = store.list_attempts("task-1")
    artifacts = store.list_artifacts("task-1")
    assert first.accepted == 0
    assert second.accepted == 1
    assert loaded.status is TaskStatus.ACCEPTED
    assert loaded.current_attempt == 4
    assert [attempt.stage for attempt in attempts] == ["annotation", "qc", "annotation", "qc"]
    assert [artifact.kind for artifact in artifacts] == ["annotation_result", "qc_result", "annotation_result", "qc_result"]
    rerun_prompt = second_annotation_client.requests[0].prompt
    assert "missing entity" in rerun_prompt
    assert "feedback_bundle" in rerun_prompt
    assert "prior_artifacts" in rerun_prompt
    discussions = store.list_feedback_discussions("task-1")
    assert len(discussions) == 1
    assert discussions[0].consensus is True
    assert discussions[0].stance == "resolved"
    assert discussions[0].metadata["resolution_source"] == "subsequent_qc_pass"
    assert build_feedback_consensus_summary(store, "task-1")["open_feedback"] == []
    assert json.loads((tmp_path / artifacts[2].path).read_text(encoding="utf-8"))["text"] == '{"labels":[{"text":"alpha","type":"ENTITY"}]}'
