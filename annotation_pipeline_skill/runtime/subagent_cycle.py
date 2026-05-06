from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Callable

from annotation_pipeline_skill.core.models import ArtifactRef, Attempt, FeedbackRecord, Task, utc_now
from annotation_pipeline_skill.core.states import AttemptStatus, FeedbackSeverity, FeedbackSource, TaskStatus
from annotation_pipeline_skill.core.transitions import transition_task
from annotation_pipeline_skill.llm.client import LLMClient, LLMGenerateRequest, LLMGenerateResult
from annotation_pipeline_skill.services.feedback_service import build_feedback_bundle
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
                self.run_task(task, stage_target)
            except Exception:
                failed += 1
                raise
            if task.status is TaskStatus.ACCEPTED:
                accepted += 1
        return SubagentRuntimeResult(started=len(pending_tasks), accepted=accepted, failed=failed)

    def run_task(self, task: Task, stage_target: str = "annotation") -> None:
        self._run_task(task, stage_target)

    def _run_task(self, task: Task, stage_target: str) -> None:
        annotation_attempt_id = self._next_attempt_id(task)
        self._transition(
            task,
            TaskStatus.ANNOTATING,
            reason="subagent runtime started annotation",
            stage="annotation",
            attempt_id=annotation_attempt_id,
        )

        annotation_result = self._generate(
            stage_target,
            LLMGenerateRequest(
                instructions=_annotation_instructions(task),
                prompt=self._annotation_prompt(task),
                continuity_handle=task.metadata.get("continuity_handle"),
            ),
        )
        task.current_attempt += 1
        annotation_artifact = self._write_stage_artifact(
            task,
            annotation_result,
            kind="annotation_result",
            attempt_id=annotation_attempt_id,
            payload={"text": annotation_result.final_text},
        )
        self._append_attempt(
            Attempt(
                attempt_id=annotation_attempt_id,
                task_id=task.task_id,
                index=task.current_attempt,
                stage="annotation",
                status=AttemptStatus.SUCCEEDED,
                started_at=utc_now(),
                finished_at=utc_now(),
                provider_id=annotation_result.provider,
                model=annotation_result.model,
                effort=None,
                route_role=stage_target,
                summary=annotation_result.final_text[:500],
                artifacts=[annotation_artifact],
            ),
            annotation_artifact,
        )

        self._transition(
            task,
            TaskStatus.VALIDATING,
            reason="subagent annotation produced result",
            stage="validation",
            attempt_id=annotation_attempt_id,
        )
        if not annotation_result.final_text.strip():
            self._record_validation_feedback(task, annotation_attempt_id)
            self._transition(
                task,
                TaskStatus.PENDING,
                reason="deterministic validation failed",
                stage="validation",
                attempt_id=annotation_attempt_id,
            )
            self.store.save_task(task)
            return

        self._transition(
            task,
            TaskStatus.QC,
            reason="deterministic validation passed",
            stage="qc",
            attempt_id=annotation_attempt_id,
        )
        qc_attempt_id = self._next_attempt_id(task)
        qc_result = self._generate(
            "qc",
            LLMGenerateRequest(
                instructions=_qc_instructions(task),
                prompt=self._qc_prompt(task, annotation_artifact),
                continuity_handle=task.metadata.get("qc_continuity_handle"),
            ),
        )
        qc_decision = _parse_qc_decision(qc_result.final_text)
        task.current_attempt += 1
        qc_artifact = self._write_stage_artifact(
            task,
            qc_result,
            kind="qc_result",
            attempt_id=qc_attempt_id,
            payload={"decision": qc_decision},
        )
        self._append_attempt(
            Attempt(
                attempt_id=qc_attempt_id,
                task_id=task.task_id,
                index=task.current_attempt,
                stage="qc",
                status=AttemptStatus.SUCCEEDED,
                started_at=utc_now(),
                finished_at=utc_now(),
                provider_id=qc_result.provider,
                model=qc_result.model,
                effort=None,
                route_role="qc",
                summary=qc_result.final_text[:500],
                artifacts=[qc_artifact],
            ),
            qc_artifact,
        )

        task.metadata["continuity_handle"] = annotation_result.continuity_handle
        task.metadata["qc_continuity_handle"] = qc_result.continuity_handle
        if qc_decision["passed"]:
            self._transition(
                task,
                TaskStatus.ACCEPTED,
                reason="subagent qc accepted result",
                stage="qc",
                attempt_id=qc_attempt_id,
                metadata={"qc_artifact_id": qc_artifact.artifact_id},
            )
        else:
            feedback = _feedback_from_qc_decision(task, qc_attempt_id, qc_decision)
            self.store.append_feedback(feedback)
            self._transition(
                task,
                TaskStatus.PENDING,
                reason="subagent qc requested annotator rerun",
                stage="qc",
                attempt_id=qc_attempt_id,
                metadata={"feedback_id": feedback.feedback_id, "qc_artifact_id": qc_artifact.artifact_id},
            )
        self.store.save_task(task)

    def _generate(self, target: str, request: LLMGenerateRequest) -> LLMGenerateResult:
        return asyncio.run(self._generate_and_close(target, request))

    async def _generate_and_close(self, target: str, request: LLMGenerateRequest) -> LLMGenerateResult:
        client = self.client_factory(target)
        try:
            return await client.generate(request)
        finally:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()

    def _append_attempt(self, attempt: Attempt, artifact: ArtifactRef) -> None:
        self.store.append_attempt(attempt)
        self.store.append_artifact(artifact)

    def _next_attempt_id(self, task: Task) -> str:
        return f"{task.task_id}-attempt-{task.current_attempt + 1}"

    def _record_validation_feedback(self, task: Task, attempt_id: str) -> None:
        self.store.append_feedback(
            FeedbackRecord.new(
                task_id=task.task_id,
                attempt_id=attempt_id,
                source_stage=FeedbackSource.VALIDATION,
                severity=FeedbackSeverity.BLOCKING,
                category="empty_annotation",
                message="Annotation result was empty.",
                target={},
                suggested_action="annotator_rerun",
                created_by="validation",
            )
        )

    def _annotation_prompt(self, task: Task) -> str:
        return json.dumps(
            {
                "task": _task_payload(task),
                "feedback_bundle": build_feedback_bundle(self.store, task.task_id),
                "prior_artifacts": self._artifact_context(task.task_id),
            },
            sort_keys=True,
        )

    def _qc_prompt(self, task: Task, annotation_artifact: ArtifactRef) -> str:
        return json.dumps(
            {
                "task": _task_payload(task),
                "annotation_artifact": {
                    **annotation_artifact.to_dict(),
                    "payload": self._read_artifact_payload(annotation_artifact),
                },
                "feedback_bundle": build_feedback_bundle(self.store, task.task_id),
            },
            sort_keys=True,
        )

    def _artifact_context(self, task_id: str) -> list[dict[str, Any]]:
        return [
            {**artifact.to_dict(), "payload": self._read_artifact_payload(artifact)}
            for artifact in self.store.list_artifacts(task_id)
        ]

    def _read_artifact_payload(self, artifact: ArtifactRef) -> Any:
        path = self.store.root / artifact.path
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _transition(
        self,
        task: Task,
        next_status: TaskStatus,
        *,
        reason: str,
        stage: str,
        attempt_id: str,
        metadata: dict | None = None,
    ) -> None:
        event = transition_task(
            task,
            next_status,
            actor="subagent-runtime",
            reason=reason,
            stage=stage,
            attempt_id=attempt_id,
            metadata=metadata,
        )
        self.store.append_event(event)

    def _write_stage_artifact(
        self,
        task: Task,
        result: LLMGenerateResult,
        *,
        kind: str,
        attempt_id: str,
        payload: dict[str, Any],
    ) -> ArtifactRef:
        relative_path = f"artifact_payloads/{task.task_id}/{attempt_id}_{kind}.json"
        path = self.store.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "task_id": task.task_id,
                    **payload,
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
            kind=kind,
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


def _annotation_instructions(task: Task) -> str:
    return (
        "You are an annotation subagent. Return raw JSON only, with no markdown fences or commentary. "
        "Follow task.source_ref.payload.annotation_guidance when it is present, including output_schema, allowed_entity_types, and rules. "
        "For text entity spans, copy exact contiguous text spans from task.source_ref.payload.text. "
        "Do not add entity labels outside the configured allowed entity types. "
        f"Modality: {task.modality}. Requirements: {json.dumps(task.annotation_requirements, sort_keys=True)}."
    )


def _qc_instructions(task: Task) -> str:
    return (
        "You are a QC subagent. Inspect the task and latest annotation artifact. "
        "Return raw JSON with no markdown fences. Include a boolean field named passed. "
        "If passed is false, include message, category, severity, target, and suggested_action. "
        "Use task.source_ref.payload.annotation_guidance as the quality policy when it is present. "
        f"Modality: {task.modality}. Requirements: {json.dumps(task.annotation_requirements, sort_keys=True)}."
    )


def _task_payload(task: Task) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "source_ref": task.source_ref,
        "selected_annotator_id": task.selected_annotator_id,
        "metadata": task.metadata,
    }


def _parse_qc_decision(text: str) -> dict[str, Any]:
    normalized_text = _strip_markdown_json_fence(text)
    try:
        payload = json.loads(normalized_text)
    except json.JSONDecodeError:
        return {
            "passed": False,
            "message": text.strip() or "QC response was empty or not valid JSON.",
            "category": "qc",
            "severity": FeedbackSeverity.WARNING.value,
            "target": {},
            "suggested_action": "annotator_rerun",
            "raw_text": text,
        }
    if not isinstance(payload, dict):
        return {
            "passed": False,
            "message": "QC response JSON must be an object.",
            "category": "qc",
            "severity": FeedbackSeverity.WARNING.value,
            "target": {},
            "suggested_action": "annotator_rerun",
            "raw_response": payload,
        }
    return {
        "passed": bool(payload.get("passed", False)),
        "message": str(payload.get("message") or payload.get("summary") or ""),
        "category": str(payload.get("category") or "qc"),
        "severity": _severity_value(payload.get("severity")),
        "target": payload.get("target") if isinstance(payload.get("target"), dict) else {},
        "suggested_action": str(payload.get("suggested_action") or "annotator_rerun"),
        "raw_response": payload,
    }


def _strip_markdown_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        return stripped
    opening = lines[0].strip().lower()
    if opening not in {"```", "```json"}:
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _feedback_from_qc_decision(task: Task, attempt_id: str, decision: dict[str, Any]) -> FeedbackRecord:
    return FeedbackRecord.new(
        task_id=task.task_id,
        attempt_id=attempt_id,
        source_stage=FeedbackSource.QC,
        severity=FeedbackSeverity(decision["severity"]),
        category=str(decision.get("category") or "qc"),
        message=str(decision.get("message") or "QC rejected the annotation result."),
        target=decision.get("target") if isinstance(decision.get("target"), dict) else {},
        suggested_action=str(decision.get("suggested_action") or "annotator_rerun"),
        created_by="qc",
        metadata={"qc_decision": decision},
    )


def _severity_value(value: object) -> str:
    if isinstance(value, str):
        try:
            return FeedbackSeverity(value).value
        except ValueError:
            return FeedbackSeverity.WARNING.value
    return FeedbackSeverity.WARNING.value
