from __future__ import annotations

import asyncio
import json
import re
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


def _is_rate_limited(exc: BaseException) -> bool:
    """Detect provider rate-limit / quota errors across SDKs and local-CLI clients.

    Covers openai.RateLimitError (status 429), generic APIStatusError with
    .status_code==429, and CLI-style errors that just carry a message — we
    inspect both the type name and the string representation.
    """
    name = type(exc).__name__
    if "RateLimit" in name:
        return True
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return "rate limit" in text or "429" in text or "too many requests" in text


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
        # Rolling per-role confidence history used to normalize raw model
        # output. LLMs are systematically miscalibrated (QC tends to output
        # 0.85-0.99; annotator the same), so the literal numbers don't
        # compare. Tracking each role's recent min/max and re-scaling lets us
        # treat 0.85 as "low for this role" or "high for this role" depending
        # on the speaker's habits.
        self._confidence_history: dict[str, list[float]] = {"qc": [], "annotator": []}
        self._confidence_window = 200
        self._confidence_min_samples = 10

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
        if task.status is TaskStatus.ARBITRATING:
            # Manual rearbitrate path: human dragged a REJECTED/HR card into the
            # Arbitration column. Re-run the arbiter over the full feedback
            # history (including consensus-closed entries from a prior arbiter
            # pass) and dispatch the outcome.
            await self._run_rearbitration(task)
            return

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
        cleaned_annotation_text = _strip_markdown_json_fence(annotation_result.final_text)
        annotation_artifact = self._write_stage_artifact(
            task,
            annotation_result,
            kind="annotation_result",
            attempt_id=annotation_attempt_id,
            payload={"text": cleaned_annotation_text},
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
        self._record_annotator_replies(task, annotation_attempt_id, annotation_result.final_text)

        # Confidence-based early escalation: both sides uncertain on at least
        # one open feedback → bounce to human reviewer instead of burning more
        # rounds. _record_annotator_replies sets the flag.
        if task.metadata.pop("needs_early_hr_low_confidence", False):
            low_ids = task.metadata.get("low_confidence_feedback_ids", [])
            reason_key = task.metadata.get("early_hr_reason", "low_confidence")
            reason_msg = {
                "low_confidence": "escalated: QC and annotator both have low confidence (<0.5) on disputed feedback",
                "high_confidence_stalemate": "escalated: QC and annotator both highly confident (>=0.85) and disagreeing — semantic stalemate",
            }.get(reason_key, "escalated: confidence-based dispute resolution selected human review")
            arb = await self._arbitrate_and_apply(task, annotation_attempt_id, stage="annotation")
            terminal = self._terminal_from_arbiter(task, annotation_attempt_id, "annotation", arb)
            if terminal is not None:
                # Arbiter made an authoritative call — ACCEPTED or REJECTED.
                return
            if arb["closed"] > 0 and self._retry_round_count(task.task_id) == 0:
                # All open disputes closed in annotator's favor; resume normal loop.
                task.metadata.pop("needs_early_hr_low_confidence", None)
                task.metadata.pop("early_hr_reason", None)
                task.metadata.pop("low_confidence_feedback_ids", None)
                task.metadata.pop("early_hr_confidence", None)
            else:
                self._transition(
                    task,
                    TaskStatus.HUMAN_REVIEW,
                    reason=reason_msg,
                    stage="annotation",
                    attempt_id=annotation_attempt_id,
                    metadata={
                        "low_confidence_feedback_ids": low_ids,
                        "early_hr_reason": reason_key,
                        "early_hr_confidence": task.metadata.get("early_hr_confidence", {}),
                        "arbiter_ran": arb["ran"],
                        "arbiter_unresolved": arb["unresolved"],
                    },
                )
                return

        task.metadata["continuity_handle"] = annotation_result.continuity_handle
        await self._run_validation_and_qc(
            task,
            annotation_artifact,
            annotation_attempt_id,
            annotation_result.final_text,
        )

    def _retry_round_count(self, task_id: str) -> int:
        """Count how many *open* retry rounds have happened for this task.

        A round is any QC/VALIDATION feedback that bounced the task back to
        PENDING. Feedbacks that have already been resolved by consensus
        (QC accepted an annotator rebuttal) are excluded — otherwise a
        single subjective complaint that both sides agreed to drop would
        still march the task toward HUMAN_REVIEW.
        """
        discussions = self.store.list_feedback_discussions(task_id)
        consensus_ids = {d.feedback_id for d in discussions if d.consensus}
        return sum(
            1 for f in self.store.list_feedback(task_id)
            if (f.source_stage is FeedbackSource.QC or f.source_stage is FeedbackSource.VALIDATION)
            and f.feedback_id not in consensus_ids
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
                # Last shot before HR: invoke the arbiter even if the
                # annotator never produced a discussion rebuttal. Without
                # this, silent annotators (models that don't emit
                # discussion_replies) bypass arbitration entirely and
                # always fall through to HR — see audit metadata where
                # arbiter_ran=False and arbiter_unresolved=0.
                arb = await self._arbitrate_and_apply(
                    task, annotation_attempt_id, stage="validation",
                    require_rebuttal=False,
                )
                terminal = self._terminal_from_arbiter(task, annotation_attempt_id, "validation", arb)
                if terminal is not None:
                    self.store.save_task(task)
                    return
                if arb["closed"] > 0 and self._retry_round_count(task.task_id) < self.max_qc_rounds:
                    self._transition(
                        task,
                        TaskStatus.PENDING,
                        reason="arbiter resolved enough disputes; resuming retry loop",
                        stage="validation",
                        attempt_id=annotation_attempt_id,
                    )
                else:
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
                            "arbiter_ran": arb["ran"],
                            "arbiter_unresolved": arb["unresolved"],
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
        # Honor explicit consensus from QC (e.g. accepted annotator rebuttal)
        # even when overall QC verdict is fail — those specific feedbacks are
        # closed by consensus and won't count toward future retry rounds.
        self._record_explicit_consensus(task, qc_attempt_id, qc_artifact, qc_decision)
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
            qc_conf = _clamp_confidence(feedback.metadata.get("confidence"))
            if qc_conf is not None:
                self._record_confidence_sample("qc", qc_conf)
            round_count = self._retry_round_count(task.task_id)
            if round_count >= self.max_qc_rounds:
                # Last shot before HR: same rationale as the validation path.
                arb = await self._arbitrate_and_apply(
                    task, qc_attempt_id, stage="qc",
                    require_rebuttal=False,
                )
                terminal = self._terminal_from_arbiter(task, qc_attempt_id, "qc", arb)
                if terminal is not None:
                    self.store.save_task(task)
                    return
                if arb["closed"] > 0 and self._retry_round_count(task.task_id) < self.max_qc_rounds:
                    self._transition(
                        task,
                        TaskStatus.PENDING,
                        reason="arbiter resolved enough disputes; resuming retry loop",
                        stage="qc",
                        attempt_id=qc_attempt_id,
                        metadata={"feedback_id": feedback.feedback_id, "qc_artifact_id": qc_artifact.artifact_id},
                    )
                else:
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
                            "arbiter_ran": arb["ran"],
                            "arbiter_unresolved": arb["unresolved"],
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

    def _record_explicit_consensus(
        self,
        task: Task,
        qc_attempt_id: str,
        qc_artifact: ArtifactRef,
        qc_decision: dict[str, Any],
    ) -> None:
        """Mark feedbacks as resolved by consensus when QC explicitly acks an annotator rebuttal."""
        ack_ids = qc_decision.get("consensus_acknowledgements") or []
        if not ack_ids:
            return
        known_feedback_ids = {f.feedback_id for f in self.store.list_feedback(task.task_id)}
        for feedback_id in ack_ids:
            if feedback_id not in known_feedback_ids:
                continue
            self.store.append_feedback_discussion(
                FeedbackDiscussionEntry.new(
                    task_id=task.task_id,
                    feedback_id=feedback_id,
                    role="qc",
                    stance="agree",
                    message="QC accepted annotator rebuttal; feedback closed by consensus.",
                    consensus=True,
                    created_by="qc-agent",
                    metadata={
                        "attempt_id": qc_attempt_id,
                        "qc_artifact_id": qc_artifact.artifact_id,
                        "resolution_source": "consensus_acknowledgement",
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
        try:
            return await self._call_client(target, request)
        except Exception as exc:  # noqa: BLE001 — fall back on any rate-limit signal
            if target == "fallback" or not _is_rate_limited(exc):
                raise
            return await self._call_client("fallback", request)

    async def _call_client(self, target: str, request: LLMGenerateRequest) -> LLMGenerateResult:
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
        if isinstance(payload, dict):
            # Strip discussion_replies before schema validation: it's a
            # side-channel for QC dialogue, not part of the output schema.
            # May appear at top level or nested inside each row.
            payload.pop("discussion_replies", None)
            rows = payload.get("rows")
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        row.pop("discussion_replies", None)
        try:
            validate_payload_against_task_schema(task, payload, store=self.store)
        except SchemaValidationError as exc:
            return {
                "category": "schema_invalid",
                "message": f"Annotation result failed schema validation: {exc}",
                "reason": "schema validation failed",
                "target": {"errors": exc.errors},
            }
        # After the schema check, enforce verbatim — every annotated entity /
        # phrase string must exist in the corresponding input row's text.
        # Catches "annotator hallucinated a span" failures at validation time
        # instead of waiting for QC.
        verbatim_failure = self._check_verbatim_spans(task, payload)
        if verbatim_failure is not None:
            return verbatim_failure
        return None

    def _check_verbatim_spans(self, task: Task, payload: Any) -> dict | None:
        """Wrap the shared ``find_verbatim_violations`` helper in the
        validation-failure dict shape the pipeline uses (first mismatch only,
        so retry feedback stays focused on one issue at a time).
        """
        from annotation_pipeline_skill.core.schema_validation import find_verbatim_violations
        violations = find_verbatim_violations(task, payload)
        if not violations:
            return None
        first = violations[0]
        return {
            "category": "non_verbatim_span",
            "message": (
                f"Row {first['row_index']} {first['field']}: span {first['span']!r} "
                f"is not a verbatim substring of the input text."
            ),
            "reason": "verbatim check failed",
            "target": first,
        }

    def _record_annotator_replies(self, task: Task, attempt_id: str, final_text: str) -> int:
        try:
            payload = json.loads(_strip_markdown_json_fence(final_text))
        except (json.JSONDecodeError, ValueError):
            return 0
        if not isinstance(payload, dict):
            return 0
        # Annotator may emit discussion_replies at the top level OR nested
        # inside each row (rows[i].discussion_replies). The prompt doesn't
        # mandate a location and live outputs use the per-row form.
        replies: list = []
        top_level = payload.get("discussion_replies")
        if isinstance(top_level, list):
            replies.extend(top_level)
        rows = payload.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_replies = row.get("discussion_replies")
                if isinstance(row_replies, list):
                    replies.extend(row_replies)
        if not replies:
            return 0
        feedback_index = {f.feedback_id: f for f in self.store.list_feedback(task.task_id)}
        written = 0
        for reply in replies:
            if not isinstance(reply, dict):
                continue
            fid = reply.get("feedback_id")
            if not isinstance(fid, str) or fid not in feedback_index:
                continue
            message = str(reply.get("message") or "").strip()
            if not message:
                continue
            ann_label = _resolve_confidence_label(reply.get("confidence"))
            metadata: dict[str, Any] = {"attempt_id": attempt_id}
            if ann_label is not None:
                metadata["confidence"] = ann_label
            self.store.append_feedback_discussion(
                FeedbackDiscussionEntry.new(
                    task_id=task.task_id,
                    feedback_id=fid,
                    role="annotator",
                    stance=str(reply.get("stance") or "comment"),
                    message=message,
                    agreed_points=[str(p) for p in (reply.get("agreed_points") or []) if isinstance(p, str)],
                    disputed_points=[str(p) for p in (reply.get("disputed_points") or []) if isinstance(p, str)],
                    proposed_resolution=(
                        str(reply["proposed_resolution"])
                        if isinstance(reply.get("proposed_resolution"), str)
                        else None
                    ),
                    consensus=False,
                    created_by="annotator-agent",
                    metadata=metadata,
                )
            )
            written += 1
            # Label-based resolution. Per the empirical calibration study
            # (every confidence bucket for both roles produced the same actual
            # correctness rate, so numeric comparison was noise), decisions
            # branch on the verbal label only — no thresholds.
            ann_label = _resolve_confidence_label(reply.get("confidence"))
            if ann_label is None:
                continue
            qc_feedback = feedback_index[fid]
            qc_label = _resolve_confidence_label(qc_feedback.metadata.get("confidence"))
            # QC: unsure → drop the feedback as noise. QC itself admitted it
            # wasn't sure; no point burning a retry on a guess.
            if qc_label == "unsure":
                self.store.append_feedback_discussion(
                    FeedbackDiscussionEntry.new(
                        task_id=task.task_id,
                        feedback_id=fid,
                        role="qc",
                        stance="agree",
                        message="QC was unsure when filing this; closing by consensus.",
                        consensus=True,
                        created_by="label-resolver",
                        metadata={"attempt_id": attempt_id, "resolution_source": "qc_unsure"},
                    )
                )
                continue
            # Annotator unsure (and QC isn't) → annotator concedes; the
            # natural retry loop continues with whatever fix the annotator
            # silently produced.
            if ann_label == "unsure":
                continue
            # Both sides have at least some confidence and disagree (annotator
            # filed a rebuttal). Don't auto-resolve — let the dispute reach
            # the arbiter at max_qc_rounds. Genuine disagreement is what the
            # arbiter exists for.
        return written

    def _terminal_from_arbiter(
        self,
        task: Task,
        attempt_id: str,
        stage: str,
        arb: dict[str, Any],
    ) -> TaskStatus | None:
        """If the arbiter made an authoritative call, transition the task to a
        terminal state and return it. Otherwise return None (caller continues
        with the normal HR / retry flow).

        Rules:
        - Any unresolved verdict (arbiter label tentative/unsure) → None (HR fallthrough).
        - Any fixed verdict (qc-wins or neither, label certain/confident) AND
          corrected_annotation present → write the correction as the final
          annotation and ACCEPT.
        - All open feedbacks closed in annotator's favor (label certain/confident)
          and zero unresolved → ACCEPT with the current annotation.
        - Anything else → None (HR fallthrough).
        """
        if not arb.get("ran"):
            return None
        if arb["unresolved"] > 0:
            # The arbiter wasn't sure on at least one dispute; let HR handle it.
            return None
        if arb["fixed"] > 0:
            corrected = arb.get("corrected_annotation")
            if not isinstance(corrected, dict):
                return None
            applied = self._apply_arbiter_correction(task, attempt_id, corrected, arb)
            return applied
        if arb["closed"] > 0:
            self._transition(
                task,
                TaskStatus.ACCEPTED,
                reason="arbiter resolved all disputes in annotator's favor",
                stage=stage,
                attempt_id=attempt_id,
                metadata={
                    "resolution_source": "arbiter",
                    "arbiter_closed": arb["closed"],
                },
            )
            return TaskStatus.ACCEPTED
        return None

    def _apply_arbiter_correction(
        self,
        task: Task,
        attempt_id: str,
        corrected: dict[str, Any],
        arb: dict[str, Any],
    ) -> TaskStatus | None:
        """Write the arbiter's corrected_annotation as a fresh annotation_result
        artifact and accept the task. Returns ACCEPTED on success or None if the
        correction couldn't be applied (caller falls through to HR).
        """
        from annotation_pipeline_skill.core.schema_validation import (
            SchemaValidationError,
            validate_payload_against_task_schema,
        )

        # Schema check the corrected annotation up front. If it fails we punt
        # back to HR rather than save a bad artifact.
        try:
            validate_payload_against_task_schema(task, corrected, store=self.store)
        except SchemaValidationError:
            return None
        # Verbatim check — arbiter sometimes paraphrases / normalizes spans
        # (e.g., traditional→simplified Chinese, dropped articles) that pass
        # schema but break the input.text substring guarantee. Without this
        # check, hallucinated/normalized spans landed in ACCEPTED tasks
        # (5% audit found ~11% violation rate). On failure, return None so
        # _terminal_from_arbiter falls through to HUMAN_REVIEW instead of
        # saving a bad corrected_annotation as the final artifact.
        verbatim_failure = self._check_verbatim_spans(task, corrected)
        if verbatim_failure is not None:
            return None

        cleaned_text = json.dumps(corrected, sort_keys=True, indent=2)
        relative_path = f"artifact_payloads/{task.task_id}/{attempt_id}_arbiter_correction.json"
        artifact_path = self.store.root / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(
                {
                    "text": cleaned_text,
                    "task_id": task.task_id,
                    "source": "arbiter_correction",
                    "diagnostics": {"resolution_source": "arbiter"},
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        artifact = ArtifactRef.new(
            task_id=task.task_id,
            kind="annotation_result",
            path=relative_path,
            content_type="application/json",
            metadata={"source": "arbiter_correction", "attempt_id": attempt_id},
        )
        self.store.append_artifact(artifact)
        self._transition(
            task,
            TaskStatus.ACCEPTED,
            reason="arbiter produced corrected annotation; task accepted",
            stage="arbitration",
            attempt_id=attempt_id,
            metadata={
                "resolution_source": "arbiter",
                "arbiter_closed": arb["closed"],
                "arbiter_fixed": arb["fixed"],
                "arbiter_correction_artifact_id": artifact.artifact_id,
            },
        )
        return TaskStatus.ACCEPTED

    async def _run_rearbitration(self, task: Task) -> None:
        """Worker entry for human-dragged REJECTED/HR → Arbitration cards.

        Task already has status=ARBITRATING (the manual-move API set it).
        We re-evaluate every QC/validation feedback (consensus-closed ones
        included) and let the arbiter decide. On no-fix outcome the task
        falls back to HUMAN_REVIEW.
        """
        attempt_id = self._next_attempt_id(task)
        arb = await self._arbitrate_and_apply(
            task,
            attempt_id,
            stage="arbitration",
            include_closed_feedbacks=True,
            require_rebuttal=False,
        )
        terminal = self._terminal_from_arbiter(task, attempt_id, "arbitration", arb)
        if terminal is None:
            self._transition(
                task,
                TaskStatus.HUMAN_REVIEW,
                reason="rearbitration produced no fix; routing back to human review",
                stage="arbitration",
                attempt_id=attempt_id,
                metadata={
                    "rearbitrate": True,
                    "arbiter_ran": arb["ran"],
                    "arbiter_unresolved": arb["unresolved"],
                    "arbiter_closed": arb["closed"],
                    "arbiter_fixed": arb["fixed"],
                },
            )
        self.store.save_task(task)

    async def _arbitrate_and_apply(
        self,
        task: Task,
        attempt_id: str,
        stage: str,
        *,
        include_closed_feedbacks: bool = False,
        require_rebuttal: bool = True,
    ) -> dict[str, Any]:
        """Run the external arbiter as judge + fixer over open disputes.

        Returns:
            {
                "ran": bool,                 # arbiter was invoked
                "closed": int,               # annotator-wins verdicts (label certain/confident)
                "fixed": int,                # qc-wins verdicts where arbiter also provided a fix
                "unresolved": int,           # any verdict labeled tentative/unsure, or qc-wins without a fix
                "corrected_annotation": dict | None,  # full corrected annotation from arbiter, when provided
            }
        Callers decide the terminal transition based on these counts (with help
        from _terminal_from_arbiter, which applies the correction).

        ``require_rebuttal`` (default True): the auto pipeline gates the arbiter
        on the annotator having posted a discussion rebuttal — no rebuttal means
        the annotator gave up, no dispute to arbitrate. The human-dragged
        ``rearbitrate`` path overrides this to False: the human is explicitly
        asking the arbiter to look at the task again, even if the annotator
        never produced a coherent rebuttal. In that case the arbiter judges
        QC's complaint directly against the latest annotation artifact and may
        still produce a corrected annotation.
        """
        empty = {"ran": False, "closed": 0, "fixed": 0, "unresolved": 0, "corrected_annotation": None}
        discussions = self.store.list_feedback_discussions(task.task_id)
        replies_by_feedback = {
            d.feedback_id: d for d in discussions
            if d.role == "annotator"
        }
        if require_rebuttal and not replies_by_feedback:
            return empty
        consensus_ids = {d.feedback_id for d in discussions if d.consensus}
        open_feedbacks = [
            f for f in self.store.list_feedback(task.task_id)
            if (include_closed_feedbacks or f.feedback_id not in consensus_ids)
            and (not require_rebuttal or f.feedback_id in replies_by_feedback)
            and (f.source_stage is FeedbackSource.QC or f.source_stage is FeedbackSource.VALIDATION)
        ]
        if not open_feedbacks:
            return empty
        try:
            arbiter_client = self.client_factory("arbiter")
        except Exception:
            return empty
        # Promote the task into ARBITRATING — visible in the kanban while the
        # arbiter LLM is running. Idempotent: if a human (or a prior step) has
        # already moved the task into ARBITRATING, skip the transition.
        if task.status is not TaskStatus.ARBITRATING:
            self._transition(
                task,
                TaskStatus.ARBITRATING,
                reason="invoking arbiter to resolve QC / annotator disputes",
                stage="arbitration",
                attempt_id=attempt_id,
            )
        items = []
        for f in open_feedbacks:
            reply = replies_by_feedback.get(f.feedback_id)
            if reply is not None:
                annotator_view = {
                    "message": reply.message,
                    "confidence": reply.metadata.get("confidence"),
                    "disputed_points": reply.disputed_points,
                    "agreed_points": reply.agreed_points,
                }
            else:
                # Rearbitrate-without-rebuttal: annotator never posted an
                # explicit reply. Tell the arbiter to judge QC's complaint
                # against the current annotation directly.
                annotator_view = {
                    "message": "(no explicit rebuttal posted; refer to current_annotation for the annotator's position)",
                    "confidence": None,
                    "disputed_points": [],
                    "agreed_points": [],
                }
            items.append({
                "feedback_id": f.feedback_id,
                "category": f.category,
                "qc": {
                    "message": f.message,
                    "confidence": f.metadata.get("confidence"),
                    "target": f.target,
                },
                "annotator": annotator_view,
            })
        # Build the full task context for arbiter-as-fixer: the input text and
        # the annotator's latest annotation. Arbiter can both judge AND produce
        # a corrected annotation that we'll apply on its behalf.
        latest_annotation_artifact = self._latest_annotation_artifact(task.task_id)
        current_annotation = self._slim_annotation_payload(latest_annotation_artifact)
        instructions = (
            "You are a senior arbiter AND fixer for an annotation pipeline. You receive the input task, "
            "the annotator's latest annotation, and a list of disputes between the automated QC "
            "reviewer and the annotator.\n\n"
            "Your response shape is ALWAYS:\n"
            "{\n"
            '  "verdicts": [{"feedback_id", "verdict", "confidence", "reasoning"}, ...],\n'
            '  "corrected_annotation": <full corrected annotation object> | null\n'
            "}\n"
            "`corrected_annotation` is a TOP-LEVEL REQUIRED key. Always present. Either an object "
            "matching current_annotation's shape, or literally null. Do not omit the field.\n\n"
            "For EACH disputed feedback choose exactly one verdict:\n"
            "  - 'annotator': the annotator's current annotation IS correct on this item; QC is wrong.\n"
            "  - 'qc':        QC's complaint IS correct; the annotation has the defect QC describes — "
            "YOU MUST APPLY QC's REQUESTED FIX in corrected_annotation. (Add the missing entity, "
            "remove the wrong span, repopulate json_structures, whatever QC asked for.)\n"
            "  - 'neither':   both sides are wrong; YOU produce the right answer in corrected_annotation.\n"
            "Confidence: ONE of these strings (no numbers; the runtime won't accept them):\n"
            "  - \"certain\"   = evidence unambiguous; any reasonable reviewer would reach the same verdict.\n"
            "  - \"confident\" = strong case but a reasonable reviewer with different priors might rule differently.\n"
            "  - \"tentative\" = judgment call; you lean this way but admit another reading is defensible.\n"
            "  - \"unsure\"    = you don't really know; route to human.\n"
            "Pick the label that fits the evidence; don't default to \"certain\".\n\n"
            "OUTPUT SHAPE REQUIREMENTS:\n"
            "  - If ANY verdict is 'qc' or 'neither' (the annotation needs change), corrected_annotation "
            "MUST be a non-null object with the FULL corrected annotation. Describing the fix in "
            "reasoning while leaving corrected_annotation null wastes your verdicts.\n"
            "  - If ALL verdicts are 'annotator' (the annotation stands as-is), set corrected_annotation = null.\n"
            "There is no 'rejected' outcome.\n\n"
            "Shape of corrected_annotation when non-null: a {\"rows\": [{\"row_index\": int, "
            "\"output\": {entities, classifications, relations, json_structures}}, ...]} object that "
            "preserves every row from current_annotation.\n"
            "MUST CONFORM TO output_schema (provided in the prompt). In particular: entity types are "
            "limited to the enum in $defs.entityType — do NOT invent new entity types like 'attribute' "
            "or 'system'. json_structures keys are limited to the enum in $defs.jsonStructureType. "
            "Each entity / phrase is a bare VERBATIM string copied from the corresponding row's "
            "input.text (no character offsets, just the text itself). Pipeline validates: every span "
            "must appear in input.text via substring match.\n"
            "Preserve fields the annotator already had right; only change what your verdicts say "
            "needs changing.\n\n"
            "Return raw JSON only, no markdown fences."
        )
        # Include the resolved output_schema so the arbiter doesn't invent
        # entity types, phrase types, or field shapes when constructing
        # corrected_annotation. Without this constraint, gpt-5.5 was emitting
        # entity names like "attribute" / "system" that the schema validator
        # rejected, causing the fix to silently fall back to HR.
        from annotation_pipeline_skill.core.schema_validation import resolve_output_schema
        output_schema = resolve_output_schema(task, self.store)
        prompt = json.dumps(
            {
                "task_id": task.task_id,
                "input": task.source_ref.get("payload", {}),
                "current_annotation": current_annotation,
                "output_schema": output_schema,
                "disputed_items": items,
            },
            indent=2,
            sort_keys=True,
        )
        # Up to ``arbiter_verbatim_retries`` retry rounds if the arbiter's
        # corrected_annotation contains a non-verbatim span (the model
        # paraphrased / normalized something that isn't in input.text). Each
        # retry tells the model exactly which span failed and asks for a
        # fresh attempt. After retries exhausted, abandon the correction —
        # the caller falls through to HR.
        max_retries = getattr(self.config, "arbiter_verbatim_retries", 2)
        retry_note = ""
        result = None
        payload = None
        verdicts = None
        started_at = utc_now()
        for attempt_idx in range(max_retries + 1):
            attempt_instructions = instructions + retry_note
            try:
                result = await arbiter_client.generate(LLMGenerateRequest(
                    instructions=attempt_instructions,
                    prompt=prompt,
                    continuity_handle=None,
                ))
            except Exception:
                return empty
            try:
                payload = json.loads(_strip_markdown_json_fence(result.final_text))
            except (json.JSONDecodeError, ValueError):
                return empty
            verdicts = payload.get("verdicts") if isinstance(payload, dict) else None
            if not isinstance(verdicts, list):
                return empty
            # If arbiter produced a corrected_annotation, pre-validate verbatim
            # before accepting the response. Schema check is repeated later in
            # _apply_arbiter_correction; here we only gate on verbatim because
            # that's the empirical failure mode (~18% of accepted tasks
            # contained hallucinated spans from this path before the guard).
            corrected_check = payload.get("corrected_annotation") if isinstance(payload, dict) else None
            if isinstance(corrected_check, dict):
                verbatim_failure = self._check_verbatim_spans(task, corrected_check)
                if verbatim_failure is not None:
                    if attempt_idx < max_retries:
                        target = verbatim_failure.get("target", {})
                        retry_note = (
                            f"\n\nPREVIOUS ATTEMPT FAILED VERBATIM CHECK: "
                            f"span {target.get('span')!r} at {target.get('field')!r} "
                            f"is not a verbatim substring of the row's input.text. "
                            f"Re-emit corrected_annotation using only spans that appear "
                            f"VERBATIM (exact character match including punctuation, "
                            f"whitespace, traditional vs simplified Chinese, case) in "
                            f"input.text. Do not paraphrase, normalize, or invent spans."
                        )
                        continue
                    # Retries exhausted — drop the bad corrected_annotation so
                    # the outcome falls through to HR instead of silently
                    # accepting a hallucinated span.
                    payload["corrected_annotation"] = None
            else:
                # Detect "high-conf qc/neither verdict but corrected_annotation
                # is null/missing" — the arbiter described a fix in reasoning
                # but forgot to emit the JSON object. Without retrying, every
                # such verdict falls into ``unresolved`` → HR. Observed in
                # production: gpt-5.5 sometimes writes "the corrected
                # annotation uses the verbatim sentence" in reasoning while
                # leaving the field null.
                needs_correction = any(
                    isinstance(v, dict)
                    and str(v.get("verdict") or "").lower() in {"qc", "neither"}
                    and _resolve_confidence_label(v.get("confidence")) in ("certain", "confident")
                    for v in verdicts
                )
                if needs_correction and attempt_idx < max_retries:
                    retry_note = (
                        "\n\nPREVIOUS ATTEMPT WAS MISSING corrected_annotation: you ruled "
                        "'qc' or 'neither' on at least one feedback (meaning the annotation "
                        "needs change) but set corrected_annotation to null. Re-emit your "
                        "full response with a non-null corrected_annotation: "
                        "{\"rows\": [...]} containing the FULL corrected annotation. "
                        "Your reasoning is wasted without it."
                    )
                    continue
            break
        finished_at = utc_now()
        # Record an Attempt for audit traceability.
        arbiter_attempt_id = self._next_attempt_id(task)
        task.current_attempt += 1
        arbiter_artifact = self._write_stage_artifact(
            task,
            result,
            kind="arbiter_result",
            attempt_id=arbiter_attempt_id,
            payload={"decision": payload, "items": items},
        )
        self._append_attempt(
            Attempt(
                attempt_id=arbiter_attempt_id,
                task_id=task.task_id,
                index=task.current_attempt,
                stage="arbitration",
                status=AttemptStatus.SUCCEEDED,
                started_at=started_at,
                finished_at=finished_at,
                provider_id=result.provider,
                model=result.model,
                effort=None,
                route_role="arbiter",
                summary=result.final_text[:500],
                artifacts=[arbiter_artifact],
            ),
            arbiter_artifact,
        )
        outcome = {
            "ran": True,
            "closed": 0,
            "fixed": 0,
            "unresolved": 0,
            "corrected_annotation": None,
        }
        corrected = payload.get("corrected_annotation") if isinstance(payload, dict) else None
        if isinstance(corrected, dict):
            outcome["corrected_annotation"] = corrected
        known_ids = {f.feedback_id for f in open_feedbacks}
        for verdict_entry in verdicts:
            if not isinstance(verdict_entry, dict):
                continue
            fid = verdict_entry.get("feedback_id")
            if not isinstance(fid, str) or fid not in known_ids:
                continue
            verdict = str(verdict_entry.get("verdict") or "").lower()
            conf_label = _resolve_confidence_label(verdict_entry.get("confidence"))
            reasoning = str(verdict_entry.get("reasoning") or "")
            base_metadata = {
                "attempt_id": arbiter_attempt_id,
                "resolution_source": "arbiter",
                "arbiter_confidence": conf_label,
                "arbiter_verdict": verdict,
                "arbiter_reasoning": reasoning,
            }
            # Arbiter labels {tentative, unsure, None} → can't trust the
            # verdict; punt to HR. Only {certain, confident} produce a
            # terminal decision.
            if conf_label in (None, "tentative", "unsure"):
                outcome["unresolved"] += 1
                self.store.append_feedback_discussion(
                    FeedbackDiscussionEntry.new(
                        task_id=task.task_id,
                        feedback_id=fid,
                        role="qc",
                        stance="comment",
                        message=f"Arbiter ({result.provider}/{result.model}) uncertain: {reasoning}",
                        consensus=False,
                        created_by="arbiter",
                        metadata=base_metadata,
                    )
                )
                continue
            if verdict == "annotator":
                # Confident annotator-wins: close the feedback by consensus.
                outcome["closed"] += 1
                self.store.append_feedback_discussion(
                    FeedbackDiscussionEntry.new(
                        task_id=task.task_id,
                        feedback_id=fid,
                        role="qc",
                        stance="agree",
                        message=f"Arbiter ({result.provider}/{result.model}) ruled in annotator's favor: {reasoning}",
                        consensus=True,
                        created_by="arbiter",
                        metadata=base_metadata,
                    )
                )
            elif verdict in {"qc", "neither"}:
                # Confident the current annotation is wrong. The arbiter must have
                # provided corrected_annotation — runtime will apply it and accept
                # the task. If the correction is missing, fall back to HR.
                if outcome["corrected_annotation"] is not None:
                    outcome["fixed"] += 1
                    self.store.append_feedback_discussion(
                        FeedbackDiscussionEntry.new(
                            task_id=task.task_id,
                            feedback_id=fid,
                            role="qc",
                            stance="agree",
                            message=(
                                f"Arbiter ({result.provider}/{result.model}) ruled {verdict!r} "
                                f"and produced a fix: {reasoning}"
                            ),
                            consensus=True,
                            created_by="arbiter",
                            metadata=base_metadata,
                        )
                    )
                else:
                    # No fix provided — punt to HR.
                    outcome["unresolved"] += 1
                    self.store.append_feedback_discussion(
                        FeedbackDiscussionEntry.new(
                            task_id=task.task_id,
                            feedback_id=fid,
                            role="qc",
                            stance="comment",
                            message=(
                                f"Arbiter ({result.provider}/{result.model}) ruled {verdict!r} but "
                                f"did not produce a fix: {reasoning}"
                            ),
                            consensus=False,
                            created_by="arbiter",
                            metadata=base_metadata,
                        )
                    )
            else:
                # Unknown verdict value at high confidence — treat as uncertain.
                outcome["unresolved"] += 1
        return outcome

    def _record_confidence_sample(self, role: str, value: float) -> None:
        history = self._confidence_history.setdefault(role, [])
        history.append(value)
        if len(history) > self._confidence_window:
            del history[: len(history) - self._confidence_window]

    def _normalize_confidence(self, role: str, value: float) -> float:
        history = self._confidence_history.get(role, [])
        if len(history) < self._confidence_min_samples:
            return value
        lo, hi = min(history), max(history)
        if hi <= lo:
            return value
        return max(0.0, min(1.0, (value - lo) / (hi - lo)))

    def _mark_early_hr(
        self,
        task: Task,
        feedback_id: str,
        reason: str,
        annotator_confidence: float,
        qc_confidence: float,
    ) -> None:
        task.metadata["needs_early_hr_low_confidence"] = True
        task.metadata.setdefault("early_hr_reason", reason)
        ids = list(task.metadata.get("low_confidence_feedback_ids", []))
        if feedback_id not in ids:
            ids.append(feedback_id)
        task.metadata["low_confidence_feedback_ids"] = ids
        confs = dict(task.metadata.get("early_hr_confidence", {}))
        confs[feedback_id] = {"annotator": annotator_confidence, "qc": qc_confidence}
        task.metadata["early_hr_confidence"] = confs

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
                    "payload": self._slim_annotation_payload(annotation_artifact),
                },
                "feedback_bundle": build_feedback_bundle(self.store, task.task_id),
                "output_schema": resolve_output_schema(task, self.store),
            },
            sort_keys=True,
        )

    def _slim_annotation_payload(self, artifact: ArtifactRef) -> Any:
        """Return only the parsed annotation rows, dropping ``raw_response`` and
        other provider-side metadata that downstream consumers (QC, arbiter)
        don't read. The minimax HTTP response can be 20 KB on its own — 75% of
        the QC prompt — and contributes nothing to QC's actual job. The
        pre-parsed inner JSON also saves QC/arbiter from re-parsing the
        ``<think>``/markdown-fence wrapper."""
        raw = self._read_artifact_payload(artifact)
        if not isinstance(raw, dict):
            return raw
        text = raw.get("text")
        if not isinstance(text, str):
            # Fallback: keep the dict but drop the bulky raw_response.
            return {k: v for k, v in raw.items() if k != "raw_response"}
        try:
            return json.loads(_strip_markdown_json_fence(text))
        except (json.JSONDecodeError, ValueError):
            return {"text": text}

    def _artifact_context(
        self, task_id: str, *, per_kind_limit: int = 1
    ) -> list[dict[str, Any]]:
        """Return artifacts grouped by kind, keeping only the most recent N per kind.

        Default ``per_kind_limit=1``: just the single latest artifact of each
        kind. The previous 3-per-kind cap was a conservative cushion from
        early development; in practice the annotator only needs the most
        recent annotation (to see its own last attempt) and the most recent
        qc_result (the latest reviewer output). Earlier attempts are stale —
        the active QC complaints come through feedback_bundle anyway, which
        names the specific row/target to fix. Cutting 3→1 saves another
        ~14 KB per prompt on a loop-heavy task.

        Prevents the annotator prompt from growing unbounded when a task loops
        through repeated annotation/QC retries (the 73-attempt case we hit in
        production blew past the LLM context window).

        Each artifact is slimmed down to the fields the annotator/QC/arbiter
        actually read. Things dropped:
          • wrapper.path, wrapper.content_type — filesystem detail
          • payload.raw_response — provider HTTP wrapper, single biggest bloat
          • payload.usage, payload.diagnostics, payload.task_id — runtime
            telemetry the LLM doesn't read (task_id is already on the wrapper)
          • payload.decision.raw_response (qc_result, arbiter_result) — the
            qc/arbiter parser stores its parsed JSON back under
            ``decision.raw_response`` in addition to lifting fields to the top
            level. Pure duplicate of decision.{failures,feedback_resolution,
            message,passed}; dropping saves ~900 chars per qc_result.

        Empirically the annotation prompt drops from 173 KB → ~50 KB after
        all of these trims on a task with the max 3+3+1 artifacts.
        """
        by_kind: dict[str, list[ArtifactRef]] = {}
        for artifact in self.store.list_artifacts(task_id):
            by_kind.setdefault(artifact.kind, []).append(artifact)
        selected: list[ArtifactRef] = []
        for arts in by_kind.values():
            # Artifacts are returned in insertion (seq) order — keep tail N
            selected.extend(arts[-per_kind_limit:])
        results: list[dict[str, Any]] = []
        for artifact in selected:
            payload = self._read_artifact_payload(artifact)
            if isinstance(payload, dict):
                payload = {
                    k: v for k, v in payload.items()
                    if k not in {"raw_response", "usage", "diagnostics", "task_id"}
                }
                decision = payload.get("decision")
                if isinstance(decision, dict) and "raw_response" in decision:
                    payload = {
                        **payload,
                        "decision": {k: v for k, v in decision.items() if k != "raw_response"},
                    }
            wrapper = {k: v for k, v in artifact.to_dict().items() if k not in {"path", "content_type"}}
            results.append({**wrapper, "payload": payload})
        return results

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
        # Persist the new status immediately so the kanban (5s poll) can show
        # tasks transiting ANNOTATING → VALIDATING → QC, not just PENDING → ACCEPTED.
        self.store.save_task(task)

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
        "For json_structures: on every row, scan the input text for all 10 phrase types defined in annotation_guidance "
        "(status, risk, goal, strategy, constraint, decision, task, preference, reason, technology) and populate "
        "json_structures with arrays of VERBATIM strings copied from the input — no character offsets, just the text "
        "itself. The pipeline rejects any span that isn't a substring of input.text, so do not paraphrase. Building "
        "codes, requirements, must/shall statements are almost always constraints. Empty json_structures = {} is only "
        "acceptable when the input genuinely contains no instance of any type. "
        "\n\n"
        "HANDLING QC FEEDBACK: for each item in feedback_bundle, choose either to fix or to rebut:\n"
        "(a) if you accept the complaint — silently fix the annotation; no discussion_reply needed.\n"
        "(b) if you disagree — add a discussion_reply with a verbal confidence label.\n"
        "\n"
        "discussion_replies schema (each entry):\n"
        "  feedback_id: str (must match feedback_bundle.items)\n"
        "  confidence:  REQUIRED — one of these strings (no numbers; the runtime won't accept them):\n"
        "    - \"certain\"   = evidence unambiguous; you can quote the exact span/text proving QC is wrong; "
        "any reasonable reviewer would agree.\n"
        "    - \"confident\" = strong case but a reasonable reviewer with different priors might side with QC.\n"
        "    - \"tentative\" = judgment call; you lean against QC but admit the other reading is defensible.\n"
        "    - \"unsure\"    = you don't know — let the arbiter / human decide.\n"
        "    Don't anchor on \"certain\". Pick the label that actually fits the evidence strength.\n"
        "  message:     str, REQUIRED, your reasoning\n"
        "  disputed_points: list[str], optional\n"
        "  proposed_resolution: str, optional\n"
        "  stance:      str, optional — for human readability only. The label drives the decision.\n"
        "Omit discussion_replies on a first attempt with no prior feedback. Never set consensus yourself."
        f"\n\nModality: {task.modality}. Requirements: {json.dumps(task.annotation_requirements, sort_keys=True)}."
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
        "You are a QC subagent. Inspect EVERY row of the task and the latest annotation artifact end-to-end. "
        "Return raw JSON with no markdown fences. Include a boolean field named passed. "
        "If passed is false, include message or failures. failures must be a list of objects with row_id or target, category, message, severity, and suggested_action. "
        "When feedback discussions or annotator rebuttals are present, include feedback_resolution as a list of row-level decisions with row_id, decision, and reason. "
        "Use the output_schema and annotation_guidance fields in this prompt as the quality policy when present. "
        "\n\n"
        "DETERMINISM: scan every row exactly once. Do not sample, do not pick random rows. "
        "If you fail this task, the NEXT QC pass on the same input MUST produce the same failure list — "
        "do not surface different missing types on different passes; that creates infinite retry loops. "
        "\n\n"
        "json_structures recall: for each row, scan the input text for all 10 phrase types "
        "(status, risk, goal, strategy, constraint, decision, task, preference, reason, technology) defined "
        "in annotation_guidance. Each phrase is a verbatim string copied from input.text — no character offsets, "
        "and the pipeline rejects spans that aren't substrings of input.text. Building codes / must / shall / "
        "should statements are clear constraints. Note: json_structures.technology is OPTIONAL — do NOT flag "
        "tasks for missing technology phrases just because the same name appears in entities.technology. "
        "json_structures.technology is appropriate only when the technology is the structural subject of a "
        "phrase (decision about it, constraint on it, status update on it). "
        "\n\n"
        "CONFIDENCE: every entry in failures MUST include a confidence field set to ONE of these "
        "strings (no numbers; the runtime won't accept them):\n"
        "  - \"certain\"   = you can quote the exact verbatim span the annotation got wrong; any reasonable "
        "reviewer would agree this is a defect.\n"
        "  - \"confident\" = strong defect but requires reading more than one sentence to confirm; reasonable "
        "reviewer with different priors might disagree.\n"
        "  - \"tentative\" = judgment call you'd defend but you admit a reasonable reviewer could disagree.\n"
        "  - \"unsure\"    = you're really not sure — at that point DO NOT FLAG. Just pass instead.\n"
        "Don't anchor on \"certain\". Pick the label that fits the evidence strength. If you only ever use "
        "\"certain\", you are miscalibrated.\n"
        "\n"
        "ANNOTATOR REBUTTALS: if feedback_bundle items carry annotator discussion_replies, each reply has a "
        "confidence label. Compare against your own label for that feedback:\n"
        "(1) annotator label is HIGHER than yours (e.g. annotator=\"certain\", you=\"tentative\") → the "
        "annotator is more sure; emit this feedback_id in consensus_acknowledgements (closes the dispute).\n"
        "(2) labels are equal → re-evaluate; if still defective keep the failure (same label); if you've "
        "changed your mind, ack it.\n"
        "(3) annotator label is LOWER than yours → keep the failure.\n"
        "\n\n"
        f"qc_policy (informational): {json.dumps(resolved_policy, sort_keys=True)}. "
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
    consensus_acks = payload.get("consensus_acknowledgements", [])
    if consensus_acks is not None and not isinstance(consensus_acks, list):
        consensus_acks = []
    return {
        "passed": bool(payload.get("passed", False)),
        "message": str(payload.get("message") or payload.get("summary") or ""),
        "category": str(payload.get("category") or "qc"),
        "severity": _severity_value(payload.get("severity")),
        "target": payload.get("target") if isinstance(payload.get("target"), dict) else {},
        "suggested_action": str(payload.get("suggested_action") or "annotator_rerun"),
        "failures": failures or [],
        "feedback_resolution": feedback_resolution or [],
        "consensus_acknowledgements": [str(x) for x in consensus_acks if isinstance(x, str)],
        "raw_response": payload,
    }


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_markdown_json_fence(text: str) -> str:
    # Many recent open-weight models (minimax, deepseek-reasoner, qwen-r1, etc.)
    # emit a leading <think>...</think> reasoning block before the JSON payload.
    # Strip those blocks first, then handle the markdown fence.
    stripped = _THINK_BLOCK_RE.sub("", text).strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        return stripped
    opening = lines[0].strip().lower()
    if opening not in {"```", "```json"}:
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _iter_verbatim_spans(output: dict) -> "list[tuple[str, str]]":
    """Yield (span_text, location) pairs from an annotation row's output for
    verbatim-against-input checking. location is a short label like
    'entities.number' or 'json_structures.constraint'.
    """
    spans: list[tuple[str, str]] = []
    entities = output.get("entities")
    if isinstance(entities, dict):
        for ent_type, items in entities.items():
            if not isinstance(items, list):
                continue
            for s in items:
                if isinstance(s, str):
                    spans.append((s, f"entities.{ent_type}"))
    js = output.get("json_structures")
    if isinstance(js, dict):
        for phrase_type, items in js.items():
            if not isinstance(items, list):
                continue
            for s in items:
                if isinstance(s, str):
                    spans.append((s, f"json_structures.{phrase_type}"))
                elif isinstance(s, dict) and isinstance(s.get("text"), str):
                    # Tolerate the legacy {text,start,end} shape too.
                    spans.append((s["text"], f"json_structures.{phrase_type}"))
    return spans


def _clamp_confidence(value: Any) -> float | None:
    """Coerce a model-provided confidence value to a clamped float in [0, 1].

    Accepts a verbal label (preferred) or a legacy numeric value. Labels map
    to bin midpoints so callers that still need a number get a comparable
    one. Returns None if the value can't be interpreted.
    """
    label = _resolve_confidence_label(value)
    if label is not None:
        return _LABEL_TO_NUMERIC[label]
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return max(0.0, min(1.0, f))


# Verbal confidence scale. Ordered high → low. Each label has an explicit
# semantic anchor written into the role prompts; the runtime treats them as
# categorical (no numeric comparison across roles). The numeric mapping is
# kept only for backward compat with historical samples and for legacy
# diagnostics — decisions should branch on the label.
CONFIDENCE_LABELS = ("certain", "confident", "tentative", "unsure")

_LABEL_TO_NUMERIC: dict[str, float] = {
    "certain": 0.97,
    "confident": 0.85,
    "tentative": 0.55,
    "unsure": 0.20,
}

# Coarse buckets to map legacy numeric values back into the label scale.
# Threshold is the inclusive lower bound.
_NUMERIC_TO_LABEL_BINS: list[tuple[float, str]] = [
    (0.85, "certain"),
    (0.65, "confident"),
    (0.40, "tentative"),
    (0.0, "unsure"),
]


def _resolve_confidence_label(value: Any) -> str | None:
    """Return one of CONFIDENCE_LABELS for any model-provided confidence value.

    Accepts the new verbal label or a legacy numeric value. Returns None
    if the value is missing or uninterpretable.
    """
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in CONFIDENCE_LABELS:
            return normalized
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    f = max(0.0, min(1.0, f))
    for threshold, label in _NUMERIC_TO_LABEL_BINS:
        if f >= threshold:
            return label
    return "unsure"


def _feedback_from_qc_decision(task: Task, attempt_id: str, decision: dict[str, Any]) -> FeedbackRecord:
    failures = decision.get("failures") if isinstance(decision.get("failures"), list) else []
    first_failure = failures[0] if failures and isinstance(failures[0], dict) else {}
    confidence_label = _resolve_confidence_label(
        first_failure.get("confidence") if isinstance(first_failure, dict) else None
    )
    metadata: dict[str, Any] = {"qc_decision": decision}
    if confidence_label is not None:
        metadata["confidence"] = confidence_label
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
        metadata=metadata,
    )


def _severity_value(value: object) -> str:
    if isinstance(value, str):
        try:
            return FeedbackSeverity(value).value
        except ValueError:
            return FeedbackSeverity.WARNING.value
    return FeedbackSeverity.WARNING.value
