from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime, SubagentRuntimeResult
from annotation_pipeline_skill.store.file_store import FileStore


class StubLLMClient:
    async def generate(self, request):
        from annotation_pipeline_skill.llm.client import LLMGenerateResult

        return LLMGenerateResult(
            runtime="test_runtime",
            provider="test_provider",
            model="test-model",
            continuity_handle="thread-1",
            final_text='{"labels":[{"text":"alpha","type":"ENTITY"}]}',
            usage={"total_tokens": 10},
            raw_response={"id": "test"},
            diagnostics={"queue_wait_ms": 0},
        )


def test_subagent_runtime_advances_ready_task_and_records_attempt(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl", "payload": {"text": "alpha"}})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    runtime = SubagentRuntime(store=store, client_factory=lambda target: StubLLMClient())

    result = runtime.run_once(stage_target="annotation")

    loaded = store.load_task("task-1")
    attempts = store.list_attempts("task-1")
    artifacts = store.list_artifacts("task-1")
    assert isinstance(result, SubagentRuntimeResult)
    assert result.started == 1
    assert loaded.status is TaskStatus.ACCEPTED
    assert attempts[0].provider_id == "test_provider"
    assert attempts[0].model == "test-model"
    assert artifacts[0].kind == "annotation_result"
    assert artifacts[0].metadata["continuity_handle"] == "thread-1"
