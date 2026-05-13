from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from annotation_pipeline_skill.core.models import ArtifactRef, FeedbackRecord, Task
from annotation_pipeline_skill.core.schema_validation import (
    SchemaValidationError,
    validate_payload_against_task_schema,
)
from annotation_pipeline_skill.core.states import FeedbackSeverity, FeedbackSource, TaskStatus
from annotation_pipeline_skill.core.transitions import InvalidTransition, transition_task
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


@dataclass(frozen=True)
class HumanReviewDecisionResult:
    task: Task
    decision: dict

    def to_dict(self) -> dict:
        return {
            "task": self.task.to_dict(),
            "decision": self.decision,
        }


@dataclass(frozen=True)
class HumanCorrectionResult:
    task: Task
    artifact: ArtifactRef
    answer: dict

    def to_dict(self) -> dict:
        return {
            "task": self.task.to_dict(),
            "artifact": self.artifact.to_dict(),
            "answer": self.answer,
        }


class HumanReviewService:
    def __init__(self, store: SqliteStore):
        self.store = store

    def decide(
        self,
        *,
        task_id: str,
        action: str,
        actor: str,
        feedback: str,
        correction_mode: str,
    ) -> HumanReviewDecisionResult:
        task = self.store.load_task(task_id)
        if task.status is not TaskStatus.HUMAN_REVIEW:
            raise InvalidTransition(f"task {task_id} is not in human_review")

        next_status, reason = self._transition_for_action(action)
        decision = {
            "task_id": task_id,
            "action": action,
            "actor": actor,
            "feedback": feedback,
            "correction_mode": correction_mode,
        }
        event = transition_task(
            task,
            next_status,
            actor=actor,
            reason=reason,
            stage="human_review",
            metadata={
                "action": action,
                "correction_mode": correction_mode,
                "feedback": feedback,
            },
        )
        self.store.append_event(event)
        self.store.save_task(task)

        # Persist the human reviewer's feedback as a first-class FeedbackRecord
        # so it appears in the Discussions tab alongside QC feedback.
        if feedback.strip() and action in {"request_changes", "reject"}:
            attempts = self.store.list_attempts(task_id)
            attempt_id = attempts[-1].attempt_id if attempts else f"{task_id}-attempt-0"
            severity = FeedbackSeverity.BLOCKING if action == "reject" else FeedbackSeverity.WARNING
            self.store.append_feedback(
                FeedbackRecord.new(
                    task_id=task_id,
                    attempt_id=attempt_id,
                    source_stage=FeedbackSource.HUMAN_REVIEW,
                    severity=severity,
                    category="human_review_decision",
                    message=feedback,
                    target={},
                    suggested_action=action,
                    created_by=actor,
                    metadata={"correction_mode": correction_mode},
                )
            )
        return HumanReviewDecisionResult(task=task, decision=decision)

    def submit_correction(
        self,
        *,
        task_id: str,
        answer: dict,
        actor: str,
        note: str | None,
    ) -> HumanCorrectionResult:
        task = self.store.load_task(task_id)
        if task.status is not TaskStatus.HUMAN_REVIEW:
            raise InvalidTransition(f"task {task_id} is not in human_review")

        # Schema-validate. Raises SchemaValidationError on failure (missing schema OR mismatch).
        validate_payload_against_task_schema(task, answer, store=self.store)

        artifact = self._write_correction_artifact(task_id, answer, actor=actor, note=note)
        event = transition_task(
            task,
            TaskStatus.ACCEPTED,
            actor=actor,
            reason="human review submitted corrected answer",
            stage="human_review",
            metadata={
                "human_authored": True,
                "answer_artifact_id": artifact.artifact_id,
                "answer_artifact_path": artifact.path,
                "note": note,
            },
        )
        self.store.append_artifact(artifact)
        self.store.append_event(event)
        self.store.save_task(task)
        return HumanCorrectionResult(task=task, artifact=artifact, answer=answer)

    def _write_correction_artifact(self, task_id: str, answer: dict, *, actor: str, note: str | None) -> ArtifactRef:
        relative_path = Path("artifact_payloads") / task_id / f"human_review_answer-{uuid4().hex}.json"
        absolute_path = self.store.root / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_text(
            json.dumps({"answer": answer, "actor": actor, "note": note}, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return ArtifactRef.new(
            task_id=task_id,
            kind="human_review_answer",
            path=relative_path.as_posix(),
            content_type="application/json",
            metadata={"actor": actor, "note": note},
        )

    def _transition_for_action(self, action: str) -> tuple[TaskStatus, str]:
        if action == "accept":
            return TaskStatus.ACCEPTED, "human review accepted task"
        if action == "reject":
            return TaskStatus.REJECTED, "human review rejected task"
        if action == "request_changes":
            return TaskStatus.ANNOTATING, "human review requested annotator changes"
        raise ValueError(f"unknown human review action: {action}")
