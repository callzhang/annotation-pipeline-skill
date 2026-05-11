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


def test_annotator_output_failing_schema_records_blocking_feedback_and_loops(tmp_path):
    """Annotator returns JSON that fails task.output_schema -> validation feedback + PENDING."""
    from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import FeedbackSeverity, FeedbackSource, TaskStatus
    from annotation_pipeline_skill.llm.client import LLMGenerateResult

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-1",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": "Acme",
                "annotation_guidance": {
                    "output_schema": {
                        "type": "object",
                        "required": ["entities"],
                        "properties": {"entities": {"type": "array"}},
                    }
                },
            },
        },
    )
    task.status = TaskStatus.PENDING
    store.save_task(task)

    class _StubClient:
        async def generate(self, request):
            return LLMGenerateResult(
                final_text='{"wrong_field": []}',
                raw_response={}, usage={}, diagnostics={}, runtime="stub",
                provider="stub", model="stub", continuity_handle=None,
            )

    runtime = SubagentRuntime(store=store, client_factory=lambda _t: _StubClient())
    runtime.run_task(store.load_task("t-1"))

    task_after = store.load_task("t-1")
    assert task_after.status is TaskStatus.PENDING

    feedbacks = store.list_feedback("t-1")
    schema_fb = [f for f in feedbacks if f.source_stage is FeedbackSource.VALIDATION and f.category == "schema_invalid"]
    assert schema_fb, f"expected schema_invalid feedback, got {[f.category for f in feedbacks]}"
    assert schema_fb[0].severity is FeedbackSeverity.BLOCKING


def test_annotator_output_invalid_json_records_validation_feedback(tmp_path):
    """Annotator returns non-JSON text -> schema_invalid feedback (parse error)."""
    from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import FeedbackSource, TaskStatus
    from annotation_pipeline_skill.llm.client import LLMGenerateResult

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-3",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": "Acme",
                "annotation_guidance": {
                    "output_schema": {"type": "object", "required": ["entities"]}
                },
            },
        },
    )
    task.status = TaskStatus.PENDING
    store.save_task(task)

    class _StubClient:
        async def generate(self, request):
            return LLMGenerateResult(
                final_text="not json at all",
                raw_response={}, usage={}, diagnostics={}, runtime="stub",
                provider="stub", model="stub", continuity_handle=None,
            )

    runtime = SubagentRuntime(store=store, client_factory=lambda _t: _StubClient())
    runtime.run_task(store.load_task("t-3"))

    task_after = store.load_task("t-3")
    assert task_after.status is TaskStatus.PENDING
    feedbacks = store.list_feedback("t-3")
    assert any(f.source_stage is FeedbackSource.VALIDATION and f.category == "schema_invalid" for f in feedbacks)


def test_annotator_output_without_schema_is_passed_through(tmp_path):
    """Task with no output_schema does not trigger schema_invalid gate; reaches QC."""
    from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus
    from annotation_pipeline_skill.llm.client import LLMGenerateResult

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-noschema",
        pipeline_id="p",
        source_ref={"kind": "jsonl", "payload": {"text": "Acme"}},  # no annotation_guidance
    )
    task.status = TaskStatus.PENDING
    store.save_task(task)

    call = {"n": 0}
    class _StubClient:
        async def generate(self, request):
            call["n"] += 1
            if call["n"] == 1:
                return LLMGenerateResult(
                    final_text='{"entities": []}',
                    raw_response={}, usage={}, diagnostics={}, runtime="stub",
                    provider="stub", model="stub", continuity_handle=None,
                )
            return LLMGenerateResult(
                final_text='{"passed": true}',
                raw_response={}, usage={}, diagnostics={}, runtime="stub",
                provider="stub", model="stub", continuity_handle=None,
            )

    runtime = SubagentRuntime(store=store, client_factory=lambda _t: _StubClient())
    runtime.run_task(store.load_task("t-noschema"))

    task_after = store.load_task("t-noschema")
    assert task_after.status is TaskStatus.ACCEPTED


def test_qc_rejection_escalates_to_human_review_after_n_rounds(tmp_path):
    """After 3 QC rejections, task transitions to HUMAN_REVIEW instead of PENDING."""
    from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import FeedbackSource, TaskStatus
    from annotation_pipeline_skill.llm.client import LLMGenerateResult

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-loop",
        pipeline_id="p",
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": "Acme",
                "annotation_guidance": {
                    "output_schema": {"type": "object"}  # permissive: annotator always passes
                },
            },
        },
    )
    task.status = TaskStatus.PENDING
    store.save_task(task)

    # Stub: every annotation passes schema; every QC rejects.
    class _StubClient:
        async def generate(self, request):
            instructions = request.instructions
            if "qc subagent" in instructions.lower():
                final = '{"passed": false, "message": "still bad", "failures": [{"category": "x", "message": "still bad"}]}'
            else:
                final = '{"entities": []}'
            return LLMGenerateResult(
                final_text=final, raw_response={}, usage={}, diagnostics={},
                runtime="stub", provider="stub", model="stub", continuity_handle=None,
            )

    runtime = SubagentRuntime(store=store, client_factory=lambda _t: _StubClient(), max_qc_rounds=3)

    for _ in range(3):
        runtime.run_once()

    task_after = store.load_task("t-loop")
    assert task_after.status is TaskStatus.HUMAN_REVIEW, f"got {task_after.status}"

    qc_feedbacks = [f for f in store.list_feedback("t-loop") if f.source_stage is FeedbackSource.QC]
    assert len(qc_feedbacks) == 3


def test_qc_rejection_loops_normally_under_threshold(tmp_path):
    """1 or 2 QC rejections still go back to PENDING."""
    from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.core.states import TaskStatus
    from annotation_pipeline_skill.llm.client import LLMGenerateResult

    store = SqliteStore.open(tmp_path)
    task = Task.new(
        task_id="t-loop2",
        pipeline_id="p",
        source_ref={"kind": "jsonl", "payload": {"text": "x", "annotation_guidance": {"output_schema": {"type": "object"}}}},
    )
    task.status = TaskStatus.PENDING
    store.save_task(task)

    class _StubClient:
        async def generate(self, request):
            instructions = request.instructions
            if "qc subagent" in instructions.lower():
                final = '{"passed": false, "message": "bad", "failures": [{"category": "x", "message": "bad"}]}'
            else:
                final = '{"entities": []}'
            return LLMGenerateResult(
                final_text=final, raw_response={}, usage={}, diagnostics={},
                runtime="stub", provider="stub", model="stub", continuity_handle=None,
            )

    runtime = SubagentRuntime(store=store, client_factory=lambda _t: _StubClient(), max_qc_rounds=3)
    runtime.run_once()
    runtime.run_once()

    task_after = store.load_task("t-loop2")
    assert task_after.status is TaskStatus.PENDING


def test_subagent_runtime_defaults_max_qc_rounds_to_3(tmp_path):
    from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    store = SqliteStore.open(tmp_path)
    runtime = SubagentRuntime(store=store, client_factory=lambda _t: None)
    assert runtime.max_qc_rounds == 3


def _seed_prelabeled_task(store, *, task_id, annotation_text, output_schema=None):
    """Create a PENDING task with a prelabeled annotation_result artifact already on disk."""
    from annotation_pipeline_skill.core.models import ArtifactRef, Attempt
    from annotation_pipeline_skill.core.states import AttemptStatus

    payload = {
        "text": "alpha",
    }
    if output_schema is not None:
        payload["annotation_guidance"] = {"output_schema": output_schema}
    task = Task.new(
        task_id=task_id,
        pipeline_id="pipe",
        source_ref={"kind": "jsonl_prelabeled", "payload": payload},
        metadata={"prelabeled": True},
    )
    task.status = TaskStatus.PENDING
    store.save_task(task)

    artifact_path = f"artifact_payloads/{task_id}/prelabeled-annotation.json"
    full_path = store.root / artifact_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "text": annotation_text,
                "raw_response": {"source": "v2_prelabel"},
                "usage": {},
                "diagnostics": {"source": "prelabel"},
            },
            sort_keys=True,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    artifact = ArtifactRef.new(
        task_id=task_id,
        kind="annotation_result",
        path=artifact_path,
        content_type="application/json",
        metadata={"runtime": "import", "provider": "prelabel"},
    )
    store.append_artifact(artifact)
    attempt = Attempt(
        attempt_id=f"attempt-prelabel-{task_id}",
        task_id=task_id,
        index=0,
        stage="annotation",
        status=AttemptStatus.SUCCEEDED,
        provider_id="prelabel",
        model="v2_baseline",
        summary="imported from v2 annotation",
    )
    store.append_attempt(attempt)
    return task


def test_prelabeled_task_skips_annotation_and_runs_qc(tmp_path):
    store = SqliteStore.open(tmp_path)
    _seed_prelabeled_task(
        store,
        task_id="pre-1",
        annotation_text='{"labels":[{"text":"alpha","type":"ENTITY"}]}',
    )

    call_log = []

    class TrackingClient:
        def __init__(self, target, final_text):
            self.target = target
            self.final_text = final_text

        async def generate(self, request):
            call_log.append(self.target)
            return LLMGenerateResult(
                runtime="test_runtime",
                provider=self.target,
                model="test-model",
                continuity_handle="handle",
                final_text=self.final_text,
                usage={},
                raw_response={},
                diagnostics={},
            )

    def client_factory(target):
        if target == "qc":
            return TrackingClient("qc", '{"passed": true, "summary": "acceptable"}')
        return TrackingClient("annotation", "SHOULD NOT BE CALLED")

    runtime = SubagentRuntime(store=store, client_factory=client_factory)
    runtime.run_once(stage_target="annotation")

    loaded = store.load_task("pre-1")
    assert loaded.status is TaskStatus.ACCEPTED
    # Annotation LLM never called
    assert "annotation" not in call_log
    assert call_log == ["qc"]
    # Stages recorded: existing prelabel attempt + new qc attempt only
    attempts = store.list_attempts("pre-1")
    stages = [a.stage for a in attempts]
    assert stages.count("annotation") == 1  # only the seeded prelabel attempt
    assert "qc" in stages
    artifacts = store.list_artifacts("pre-1")
    assert any(a.kind == "annotation_result" for a in artifacts)
    assert any(a.kind == "qc_result" for a in artifacts)


def test_prelabeled_task_falls_through_to_normal_annotation_after_qc_failure(tmp_path):
    store = SqliteStore.open(tmp_path)
    _seed_prelabeled_task(
        store,
        task_id="pre-2",
        annotation_text='{"labels":[]}',
    )

    annotation_calls = {"count": 0}

    def client_factory(target):
        if target == "qc":
            return StubLLMClient(
                final_text='{"passed": false, "message": "rejected", "category": "quality", "severity": "warning", "suggested_action": "annotator_rerun"}',
                provider="qc",
            )
        annotation_calls["count"] += 1
        return StubLLMClient(
            final_text='{"labels":[{"text":"alpha","type":"ENTITY"}]}',
            provider="annotator",
        )

    runtime = SubagentRuntime(store=store, client_factory=client_factory)

    # First run: prelabeled path, QC fails -> PENDING
    runtime.run_once(stage_target="annotation")
    loaded = store.load_task("pre-2")
    assert loaded.status is TaskStatus.PENDING
    assert loaded.current_attempt >= 1
    assert annotation_calls["count"] == 0  # prelabeled path skipped annotation LLM

    # Second run: now current_attempt > 0, prelabeled branch must NOT fire.
    # Annotation LLM must be invoked. Use a QC stub that rejects again to keep test simple.
    runtime.run_once(stage_target="annotation")
    assert annotation_calls["count"] == 1
    attempts = store.list_attempts("pre-2")
    annotation_stages = [a for a in attempts if a.stage == "annotation"]
    # Seeded prelabel + new annotation attempt
    assert len(annotation_stages) >= 2
    assert any(a.provider_id == "annotator" for a in annotation_stages)


def test_prelabeled_task_fails_schema_validation_on_existing_artifact(tmp_path):
    store = SqliteStore.open(tmp_path)
    _seed_prelabeled_task(
        store,
        task_id="pre-3",
        annotation_text='{"wrong_field": []}',
        output_schema={
            "type": "object",
            "required": ["labels"],
            "properties": {"labels": {"type": "array"}},
        },
    )

    qc_called = {"count": 0}

    class _StubClient:
        def __init__(self, target):
            self.target = target

        async def generate(self, request):
            if self.target == "qc":
                qc_called["count"] += 1
            return LLMGenerateResult(
                runtime="stub",
                provider=self.target,
                model="m",
                continuity_handle=None,
                final_text='{"passed": true}',
                usage={},
                raw_response={},
                diagnostics={},
            )

    runtime = SubagentRuntime(store=store, client_factory=lambda t: _StubClient(t))
    runtime.run_once(stage_target="annotation")

    loaded = store.load_task("pre-3")
    assert loaded.status is TaskStatus.PENDING
    # Schema validation gate blocked QC entirely
    assert qc_called["count"] == 0
    feedbacks = store.list_feedback("pre-3")
    assert any(f.category == "schema_invalid" for f in feedbacks), f"got {[f.category for f in feedbacks]}"
