from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Callable

from annotation_pipeline_skill.core.models import ArtifactRef, Attempt, Task, utc_now
from annotation_pipeline_skill.core.states import AttemptStatus, TaskStatus
from annotation_pipeline_skill.core.transitions import transition_task
from annotation_pipeline_skill.llm.client import LLMClient, LLMGenerateRequest, LLMGenerateResult
from annotation_pipeline_skill.store.file_store import FileStore


@dataclass(frozen=True)
class SubagentRuntimeResult:
    started: int
    accepted: int
    failed: int


class SubagentRuntime:
    def __init__(self, store: FileStore, client_factory: Callable[[str], LLMClient]):
        self.store = store
        self.client_factory = client_factory

    def run_once(self, stage_target: str = "annotation", limit: int | None = None) -> SubagentRuntimeResult:
        pending_tasks = [task for task in self.store.list_tasks() if task.status is TaskStatus.PENDING]
        if limit is not None:
            pending_tasks = pending_tasks[:limit]

        accepted = 0
        failed = 0
        for task in pending_tasks:
            try:
                self._run_task(task, stage_target)
            except Exception:
                failed += 1
                raise
            if task.status is TaskStatus.ACCEPTED:
                accepted += 1
        return SubagentRuntimeResult(started=len(pending_tasks), accepted=accepted, failed=failed)

    def _run_task(self, task: Task, stage_target: str) -> None:
        attempt_id = f"{task.task_id}-attempt-{task.current_attempt + 1}"
        self._transition(
            task,
            TaskStatus.ANNOTATING,
            reason="subagent runtime started annotation",
            stage="annotation",
            attempt_id=attempt_id,
        )

        client = self.client_factory(stage_target)
        result = asyncio.run(
            client.generate(
                LLMGenerateRequest(
                    instructions=_stage_instructions(task),
                    prompt=_task_prompt(task),
                    continuity_handle=task.metadata.get("continuity_handle"),
                )
            )
        )
        task.current_attempt += 1
        artifact = self._write_annotation_artifact(task, result)
        attempt = Attempt(
            attempt_id=attempt_id,
            task_id=task.task_id,
            index=task.current_attempt,
            stage="annotation",
            status=AttemptStatus.SUCCEEDED,
            started_at=utc_now(),
            finished_at=utc_now(),
            provider_id=result.provider,
            model=result.model,
            effort=None,
            route_role=stage_target,
            summary=result.final_text[:500],
            artifacts=[artifact],
        )
        self.store.append_attempt(attempt)
        self.store.append_artifact(artifact)

        self._transition(
            task,
            TaskStatus.VALIDATING,
            reason="subagent annotation produced result",
            stage="validation",
            attempt_id=attempt_id,
        )
        self._transition(
            task,
            TaskStatus.QC,
            reason="deterministic validation passed",
            stage="qc",
            attempt_id=attempt_id,
        )
        self._transition(
            task,
            TaskStatus.ACCEPTED,
            reason="subagent qc accepted result",
            stage="qc",
            attempt_id=attempt_id,
        )
        task.metadata["continuity_handle"] = result.continuity_handle
        self.store.save_task(task)

    def _transition(
        self,
        task: Task,
        next_status: TaskStatus,
        *,
        reason: str,
        stage: str,
        attempt_id: str,
    ) -> None:
        event = transition_task(
            task,
            next_status,
            actor="subagent-runtime",
            reason=reason,
            stage=stage,
            attempt_id=attempt_id,
        )
        self.store.append_event(event)

    def _write_annotation_artifact(self, task: Task, result: LLMGenerateResult) -> ArtifactRef:
        relative_path = f"artifact_payloads/{task.task_id}/annotation_result.json"
        path = self.store.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "task_id": task.task_id,
                    "text": result.final_text,
                    "raw_response": result.raw_response,
                    "usage": result.usage,
                    "diagnostics": result.diagnostics,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return ArtifactRef.new(
            task_id=task.task_id,
            kind="annotation_result",
            path=relative_path,
            content_type="application/json",
            metadata={
                "runtime": result.runtime,
                "provider": result.provider,
                "model": result.model,
                "continuity_handle": result.continuity_handle,
                "diagnostics": result.diagnostics or {},
            },
        )


def _stage_instructions(task: Task) -> str:
    return (
        "You are an annotation subagent. Return only the annotation result for the task. "
        f"Modality: {task.modality}. Requirements: {json.dumps(task.annotation_requirements, sort_keys=True)}."
    )


def _task_prompt(task: Task) -> str:
    return json.dumps(
        {
            "task_id": task.task_id,
            "source_ref": task.source_ref,
            "selected_annotator_id": task.selected_annotator_id,
            "metadata": task.metadata,
        },
        sort_keys=True,
    )
