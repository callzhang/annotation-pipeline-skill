from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from annotation_pipeline_skill.core.models import ArtifactRef, Attempt, FeedbackDiscussionEntry, FeedbackRecord, Task, utc_now
from annotation_pipeline_skill.core.runtime import RuntimeConfig
from annotation_pipeline_skill.core.schema_validation import (
    SchemaValidationError,
    resolve_output_schema,
    validate_payload_against_task_schema,
)
from annotation_pipeline_skill.core.states import AttemptStatus, FeedbackSeverity, FeedbackSource, TaskStatus
from annotation_pipeline_skill.core.transitions import transition_task
from annotation_pipeline_skill.llm.client import LLMClient, LLMGenerateRequest, LLMGenerateResult
from annotation_pipeline_skill.services.feedback_service import build_feedback_bundle, build_feedback_consensus_summary
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


@dataclass(frozen=True)
class SubagentRuntimeResult:
    started: int
    accepted: int
    failed: int


class QCParseError(ValueError):
    def __init__(self, message: str, *, raw_text: str):
        super().__init__(message)
        self.diagnostics = {"error_kind": "parse_error", "raw_text": raw_text}


class SubagentRuntime:
    def __init__(
        self,
        store: SqliteStore,
        client_factory: Callable[[str], LLMClient],
        *,
        max_qc_rounds: int | None = None,
        config: RuntimeConfig | None = None,
    ):
        self.store = store
        self.client_factory = client_factory
        # ``config`` carries the project-level QC sampling policy and the
        # max-rounds setting. When omitted (callers that predate the lift, or
        # tests that only care about the per-task flow), fall back to defaults.
        self.config = config or RuntimeConfig()
        # Explicit ``max_qc_rounds`` still wins for backward compat with the
        # local scheduler kwarg that already passed it directly.
        self.max_qc_rounds = (
            max_qc_rounds if max_qc_rounds is not None else self.config.max_qc_rounds
        )

    def run_once(self, stage_target: str = "annotation", limit: int | None = None) -> SubagentRuntimeResult:
        pending_tasks = self.store.list_tasks_by_status({TaskStatus.PENDING})
        if limit is not None:
            pending_tasks = pending_tasks[:limit]

        accepted = 0
        failed = 0
        for task in pending_tasks:
            try:
                self.run_task(task, stage_target)
            except Exception:
                failed += 1
                continue
            if task.status is TaskStatus.ACCEPTED:
                accepted += 1
        return SubagentRuntimeResult(started=len(pending_tasks), accepted=accepted, failed=failed)

    def run_task(self, task: Task, stage_target: str = "annotation") -> None:
        """Synchronous entry point. Wraps the async core for tests and CLI use."""
        asyncio.run(self.run_task_async(task, stage_target))

    async def run_task_async(self, task: Task, stage_target: str = "annotation") -> None:
        """Async entry point used by the scheduler to run tasks concurrently."""
        await self._run_task(task, stage_target)

    def _load_guideline(self, task: Task) -> str | None:
        if not task.document_version_id:
            return None
        try:
            ver = self.store.load_document_version(task.document_version_id)
        except FileNotFoundError:
            return None
        return f"Annotation guideline ({ver.version}):\n{ver.content}"

    async def _run_task(self, task: Task, stage_target: str) -> None:
        if task.status is TaskStatus.QC and task.metadata.get("runtime_next_stage") == "qc":
            await self._run_qc_only(task)
            return

        if (
            task.status is TaskStatus.PENDING
            and task.current_attempt == 0
            and task.metadata.get("prelabeled")
        ):
            prelabeled = [
                artifact for artifact in self.store.list_artifacts(task.task_id)
                if artifact.kind == "annotation_result"
            ]
            if prelabeled:
                annotation_artifact = prelabeled[-1]
                attempts = self.store.list_attempts(task.task_id)
                annotation_attempt_id = (
                    attempts[-1].attempt_id if attempts else f"prelabeled-{task.task_id}"
                )
                task.current_attempt = 1
                payload = self._read_artifact_payload(annotation_artifact)
                if isinstance(payload, dict):
                    final_text = payload.get("text", json.dumps(payload, sort_keys=True))
                else:
                    final_text = json.dumps(payload, sort_keys=True)
                self._transition(
                    task,
                    TaskStatus.ANNOTATING,
                    reason="prelabeled annotation reused; skipping LLM annotation",
                    stage="annotation",
                    attempt_id=annotation_attempt_id,
                    metadata={"prelabeled": True},
                )
                self._transition(
                    task,
                    TaskStatus.VALIDATING,
                    reason="prelabeled annotation ready for schema validation",
                    stage="validation",
                    attempt_id=annotation_attempt_id,
                    metadata={"prelabeled": True},
                )
                await self._run_validation_and_qc(
                    task,
                    annotation_artifact,
                    annotation_attempt_id,
                    final_text,
                )
                return

        guideline = self._load_guideline(task)
        annotation_attempt_id = self._next_attempt_id(task)
        self._transition(
            task,
            TaskStatus.ANNOTATING,
            reason="subagent runtime started annotation",
            stage="annotation",
            attempt_id=annotation_attempt_id,
        )

        annotation_started_at = utc_now()
        annotation_result = await self._generate_async(
            stage_target,
            LLMGenerateRequest(
                instructions=_annotation_instructions(task, guideline=guideline),
                prompt=self._annotation_prompt(task),
                continuity_handle=task.metadata.get("continuity_handle"),
            ),
        )
        annotation_finished_at = utc_now()
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
                started_at=annotation_started_at,
                finished_at=annotation_finished_at,
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
        task.metadata["continuity_handle"] = annotation_result.continuity_handle
        await self._run_validation_and_qc(
            task,
            annotation_artifact,
            annotation_attempt_id,
            annotation_result.final_text,
        )

    def _retry_round_count(self, task_id: str) -> int:
        """Count how many full retry rounds have happened for this task.

        A round is any feedback record from QC or VALIDATION stages that
        bounced the task back to PENDING. We count both because either kind
        of failure indicates the annotator must redo the work.
        """
        return sum(
            1 for f in self.store.list_feedback(task_id)
            if f.source_stage is FeedbackSource.QC or f.source_stage is FeedbackSource.VALIDATION
        )

    async def _run_validation_and_qc(
        self,
        task: Task,
        annotation_artifact: ArtifactRef,
        annotation_attempt_id: str,
        annotation_final_text: str,
    ) -> None:
        validation_failure = self._check_annotation_validation(task, annotation_final_text)
        if validation_failure is not None:
            self._record_validation_feedback(
                task,
                annotation_attempt_id,
                category=validation_failure["category"],
                message=validation_failure["message"],
                target=validation_failure.get("target", {}),
            )
            round_count = self._retry_round_count(task.task_id)
            if round_count >= self.max_qc_rounds:
                self._transition(
                    task,
                    TaskStatus.HUMAN_REVIEW,
                    reason="auto-escalated after repeated annotation/QC failures",
                    stage="validation",
                    attempt_id=annotation_attempt_id,
                    metadata={
                        "auto_escalated": True,
                        "round_count": round_count,
                        "max_qc_rounds": self.max_qc_rounds,
                    },
                )
            else:
                self._transition(
                    task,
                    TaskStatus.PENDING,
                    reason=validation_failure["reason"],
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
        await self._run_qc_stage(task, annotation_artifact)
        self.store.save_task(task)

    async def _run_qc_only(self, task: Task) -> None:
        annotation_artifact = self._latest_annotation_artifact(task.task_id)
        await self._run_qc_stage(task, annotation_artifact)
        self.store.save_task(task)

    async def _run_qc_stage(self, task: Task, annotation_artifact: ArtifactRef) -> None:
        guideline = self._load_guideline(task)
        qc_attempt_id = self._next_attempt_id(task)
        qc_started_at = utc_now()
        qc_result = await self._generate_async(
            "qc",
            LLMGenerateRequest(
                instructions=self._qc_instructions(task, guideline=guideline),
                prompt=self._qc_prompt(task, annotation_artifact),
                continuity_handle=task.metadata.get("qc_continuity_handle"),
            ),
        )
        qc_finished_at = utc_now()
        try:
            qc_decision = _parse_qc_decision(qc_result.final_text)
        except QCParseError as exc:
            self._record_qc_parse_error(task, qc_attempt_id, qc_result, exc, started_at=qc_started_at)
            raise
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
                started_at=qc_started_at,
                finished_at=qc_finished_at,
                provider_id=qc_result.provider,
                model=qc_result.model,
                effort=None,
                route_role="qc",
                summary=qc_result.final_text[:500],
                artifacts=[qc_artifact],
            ),
            qc_artifact,
        )

        task.metadata["qc_continuity_handle"] = qc_result.continuity_handle
        task.metadata.pop("runtime_next_stage", None)
        if qc_decision["passed"]:
            self._record_feedback_resolution(task, qc_attempt_id, qc_artifact, qc_decision)
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
            round_count = self._retry_round_count(task.task_id)
            if round_count >= self.max_qc_rounds:
                self._transition(
                    task,
                    TaskStatus.HUMAN_REVIEW,
                    reason="auto-escalated after repeated annotation/QC failures",
                    stage="qc",
                    attempt_id=qc_attempt_id,
                    metadata={
                        "auto_escalated": True,
                        "round_count": round_count,
                        "max_qc_rounds": self.max_qc_rounds,
                        "feedback_id": feedback.feedback_id,
                        "qc_artifact_id": qc_artifact.artifact_id,
                    },
                )
            else:
                self._transition(
                    task,
                    TaskStatus.PENDING,
                    reason="subagent qc requested annotator rerun",
                    stage="qc",
                    attempt_id=qc_attempt_id,
                    metadata={"feedback_id": feedback.feedback_id, "qc_artifact_id": qc_artifact.artifact_id},
                )
        self.store.save_task(task)

    def _record_feedback_resolution(
        self,
        task: Task,
        qc_attempt_id: str,
        qc_artifact: ArtifactRef,
        qc_decision: dict[str, Any],
    ) -> None:
        open_feedback_ids = build_feedback_consensus_summary(self.store, task.task_id)["open_feedback"]
        if not open_feedback_ids:
            return

        message = str(qc_decision.get("summary") or "Resolved by a subsequent QC pass.")
        for feedback_id in open_feedback_ids:
            self.store.append_feedback_discussion(
                FeedbackDiscussionEntry.new(
                    task_id=task.task_id,
                    feedback_id=feedback_id,
                    role="qc",
                    stance="resolved",
                    message=message,
                    proposed_resolution="Subsequent annotation attempt passed QC.",
                    consensus=True,
                    created_by="qc-agent",
                    metadata={
                        "attempt_id": qc_attempt_id,
                        "qc_artifact_id": qc_artifact.artifact_id,
                        "resolution_source": "subsequent_qc_pass",
                    },
                )
            )

    def _latest_annotation_artifact(self, task_id: str) -> ArtifactRef:
        annotation_artifacts = [
            artifact for artifact in self.store.list_artifacts(task_id)
            if artifact.kind == "annotation_result"
        ]
        if not annotation_artifacts:
            raise QCParseError("QC retry requires an annotation artifact.", raw_text="")
        return annotation_artifacts[-1]

    def _record_qc_parse_error(
        self,
        task: Task,
        attempt_id: str,
        result: LLMGenerateResult,
        error: QCParseError,
        *,
        started_at: datetime,
    ) -> None:
        finished_at = utc_now()
        task.current_attempt += 1
        artifact = self._write_stage_artifact(
            task,
            result,
            kind="qc_result",
            attempt_id=attempt_id,
            payload={"parse_error": error.diagnostics},
        )
        self._append_attempt(
            Attempt(
                attempt_id=attempt_id,
                task_id=task.task_id,
                index=task.current_attempt,
                stage="qc",
                status=AttemptStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
                provider_id=result.provider,
                model=result.model,
                route_role="qc",
                summary=str(error),
                error={"kind": "parse_error", "message": str(error)},
                artifacts=[artifact],
            ),
            artifact,
        )
        task.metadata["qc_continuity_handle"] = result.continuity_handle
        task.metadata["runtime_next_stage"] = "qc"
        self.store.save_task(task)

    def _generate(self, target: str, request: LLMGenerateRequest) -> LLMGenerateResult:
        """Sync wrapper retained for any external callers; the runtime uses _generate_async."""
        return asyncio.run(self._generate_async(target, request))

    async def _generate_async(self, target: str, request: LLMGenerateRequest) -> LLMGenerateResult:
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
        # Derive from MAX(idx) of already-persisted attempts rather than
        # task.current_attempt: the latter can be reset to 0 by an import
        # UPSERT, which would otherwise produce a colliding attempt-1 and
        # blow up on the attempts.attempt_id UNIQUE constraint.
        existing = self.store.list_attempts(task.task_id)
        max_idx = max((a.index for a in existing), default=0)
        return f"{task.task_id}-attempt-{max_idx + 1}"

    def _record_validation_feedback(
        self,
        task: Task,
        attempt_id: str,
        *,
        category: str = "empty_annotation",
        message: str = "Annotation result was empty.",
        target: dict | None = None,
    ) -> None:
        self.store.append_feedback(
            FeedbackRecord.new(
                task_id=task.task_id,
                attempt_id=attempt_id,
                source_stage=FeedbackSource.VALIDATION,
                severity=FeedbackSeverity.BLOCKING,
                category=category,
                message=message,
                target=target or {},
                suggested_action="annotator_rerun",
                created_by="validation",
            )
        )

    def _check_annotation_validation(self, task: Task, final_text: str) -> dict | None:
        if not final_text.strip():
            return {
                "category": "empty_annotation",
                "message": "Annotation result was empty.",
                "reason": "deterministic validation failed",
            }
        schema = resolve_output_schema(task, self.store)
        if schema is None:
            return None
        try:
            payload = json.loads(_strip_markdown_json_fence(final_text))
        except json.JSONDecodeError as exc:
            return {
                "category": "schema_invalid",
                "message": f"Annotation result is not valid JSON: {exc}",
                "reason": "schema validation failed",
            }
        try:
            validate_payload_against_task_schema(task, payload, store=self.store)
        except SchemaValidationError as exc:
            return {
                "category": "schema_invalid",
                "message": f"Annotation result failed schema validation: {exc}",
                "reason": "schema validation failed",
                "target": {"errors": exc.errors},
            }
        return None

    def _resolved_qc_policy(self, task: Task) -> dict[str, Any]:
        return _resolve_qc_policy_from_task_or_config(task, self.config)

    def _qc_instructions(self, task: Task, *, guideline: str | None = None) -> str:
        return _build_qc_instructions(
            task,
            resolved_policy=self._resolved_qc_policy(task),
            guideline=guideline,
        )

    def _annotation_prompt(self, task: Task) -> str:
        return json.dumps(
            {
                "task": _task_payload(task),
                "feedback_bundle": build_feedback_bundle(self.store, task.task_id),
                "prior_artifacts": self._artifact_context(task.task_id),
                "output_schema": resolve_output_schema(task, self.store),
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
                "output_schema": resolve_output_schema(task, self.store),
            },
            sort_keys=True,
        )

    def _artifact_context(
        self, task_id: str, *, per_kind_limit: int = 3
    ) -> list[dict[str, Any]]:
        """Return artifacts grouped by kind, keeping only the most recent N per kind.

        Prevents the annotator prompt from growing unbounded when a task loops
        through repeated annotation/QC retries (the 73-attempt case we hit in
        production blew past the LLM context window).
        """
        by_kind: dict[str, list[ArtifactRef]] = {}
        for artifact in self.store.list_artifacts(task_id):
            by_kind.setdefault(artifact.kind, []).append(artifact)
        selected: list[ArtifactRef] = []
        for arts in by_kind.values():
            # Artifacts are returned in insertion (seq) order — keep tail N
            selected.extend(arts[-per_kind_limit:])
        return [
            {**artifact.to_dict(), "payload": self._read_artifact_payload(artifact)}
            for artifact in selected
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


def _annotation_instructions(task: Task, *, guideline: str | None = None) -> str:
    base = (
        "You are an annotation subagent. Return raw JSON only, with no markdown fences or commentary. "
        "Follow the output_schema and annotation_guidance fields in this prompt (output_schema is the JSON Schema your response must conform to). Honor allowed_entity_types and rules from annotation_guidance when present. "
        "For text entity spans, copy exact contiguous text spans from task.source_ref.payload.text. "
        "Do not add entity labels outside the configured allowed entity types. "
        f"Modality: {task.modality}. Requirements: {json.dumps(task.annotation_requirements, sort_keys=True)}."
    )
    if guideline:
        return f"{base}\n\n{guideline}"
    return base


def _qc_instructions(task: Task, *, guideline: str | None = None) -> str:
    """Legacy module-level helper retained for any external callers.

    The runtime now uses ``SubagentRuntime._qc_instructions``, which resolves
    the QC sampling policy from project config when the task has none. This
    fallback uses the default ``RuntimeConfig``.
    """
    return _build_qc_instructions(
        task,
        resolved_policy=_resolve_qc_policy_from_task_or_config(task, RuntimeConfig()),
        guideline=guideline,
    )


def _resolve_qc_policy_from_task_or_config(task: Task, config: RuntimeConfig) -> dict[str, Any]:
    """Build the QC sampling policy: legacy per-task override wins, else project default."""
    task_policy = task.metadata.get("qc_policy") if isinstance(task.metadata, dict) else None
    if isinstance(task_policy, dict) and task_policy:
        return task_policy
    return {
        "mode": config.qc_sample_mode,
        "sample_ratio": config.qc_sample_ratio,
        "sample_count": config.qc_sample_count,
    }


def _build_qc_instructions(
    task: Task,
    *,
    resolved_policy: dict[str, Any],
    guideline: str | None = None,
) -> str:
    base = (
        "You are a QC subagent. Inspect the task and latest annotation artifact. "
        "Return raw JSON with no markdown fences. Include a boolean field named passed. "
        "If passed is false, include message or failures. failures must be a list of objects with row_id or target, category, message, severity, and suggested_action. "
        "When feedback discussions or annotator rebuttals are present, include feedback_resolution as a list of row-level decisions with row_id, decision, and reason. "
        "Use the output_schema and annotation_guidance fields in this prompt as the quality policy when present. "
        f"Apply the project-level qc_policy below to decide the QC scope for this task: {json.dumps(resolved_policy, sort_keys=True)}. "
        "When qc_policy.mode is sample_count or sample_ratio, inspect exactly qc_policy.sample_count rows/items from this task "
        "(or the count implied by sample_ratio * row_count when sample_count is null), "
        "choose them deterministically from task payload order, and include sampled row ids or row indexes in the QC response. "
        f"Modality: {task.modality}. Requirements: {json.dumps(task.annotation_requirements, sort_keys=True)}."
    )
    if guideline:
        return f"{base}\n\n{guideline}"
    return base


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
    except json.JSONDecodeError as exc:
        raise QCParseError("QC response was not valid JSON.", raw_text=text) from exc
    if not isinstance(payload, dict):
        raise QCParseError("QC response JSON must be an object.", raw_text=text)
    if not isinstance(payload.get("passed"), bool):
        raise QCParseError("QC response JSON must include boolean passed.", raw_text=text)
    failures = payload.get("failures", [])
    if failures is not None and not isinstance(failures, list):
        raise QCParseError("QC response failures must be a list when present.", raw_text=text)
    feedback_resolution = payload.get("feedback_resolution", [])
    if feedback_resolution is not None and not isinstance(feedback_resolution, list):
        raise QCParseError("QC response feedback_resolution must be a list when present.", raw_text=text)
    if payload["passed"] is False and not str(payload.get("message") or payload.get("summary") or "").strip() and not failures:
        raise QCParseError("Rejected QC response must include message or failures.", raw_text=text)
    return {
        "passed": bool(payload.get("passed", False)),
        "message": str(payload.get("message") or payload.get("summary") or ""),
        "category": str(payload.get("category") or "qc"),
        "severity": _severity_value(payload.get("severity")),
        "target": payload.get("target") if isinstance(payload.get("target"), dict) else {},
        "suggested_action": str(payload.get("suggested_action") or "annotator_rerun"),
        "failures": failures or [],
        "feedback_resolution": feedback_resolution or [],
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
    failures = decision.get("failures") if isinstance(decision.get("failures"), list) else []
    first_failure = failures[0] if failures and isinstance(failures[0], dict) else {}
    return FeedbackRecord.new(
        task_id=task.task_id,
        attempt_id=attempt_id,
        source_stage=FeedbackSource.QC,
        severity=FeedbackSeverity(decision["severity"]),
        category=str(first_failure.get("category") or decision.get("category") or "qc"),
        message=str(first_failure.get("message") or decision.get("message") or "QC rejected the annotation result."),
        target=first_failure.get("target") if isinstance(first_failure.get("target"), dict) else decision.get("target") if isinstance(decision.get("target"), dict) else {},
        suggested_action=str(first_failure.get("suggested_action") or decision.get("suggested_action") or "annotator_rerun"),
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
