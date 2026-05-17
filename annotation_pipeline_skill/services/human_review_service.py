from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from annotation_pipeline_skill.core.models import ArtifactRef, FeedbackRecord, Task
from annotation_pipeline_skill.core.schema_validation import (
    SchemaValidationError,
    find_cross_type_collisions,
    find_trailing_punctuation_spans,
    find_verbatim_violations,
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
        # Verbatim guard on the "accept underlying annotation as-is" path.
        # The task likely landed in HR because the arbiter's verbatim retries
        # exhausted on a hallucinated span — accepting blindly would commit
        # known-bad data. Operator must use submit_correction with verbatim
        # spans, or request_changes.
        if next_status is TaskStatus.ACCEPTED:
            latest_annotation = self._latest_annotation_payload(task_id)
            if latest_annotation is not None:
                # Same span checks the annotator / arbiter / submit_correction
                # paths run. Accepting "as-is" must NOT bypass them — the
                # underlying annotation is what would end up in the training
                # export, and the operator may not have noticed defects.
                violations = find_verbatim_violations(task, latest_annotation)
                if violations:
                    raise SchemaValidationError(
                        f"underlying annotation has {len(violations)} non-verbatim span(s); "
                        f"use submit_correction (with verbatim spans) or request_changes",
                        [
                            {"kind": "non_verbatim_span",
                             "path": f"rows[{v['row_index']}].output.{v['field']}",
                             "message": f"span {v['span']!r} is not a verbatim substring of the row's input.text"}
                            for v in violations
                        ],
                    )
                collisions = find_cross_type_collisions(latest_annotation)
                if collisions:
                    raise SchemaValidationError(
                        f"underlying annotation has {len(collisions)} cross-type entity collision(s); "
                        f"use submit_correction or request_changes",
                        [
                            {"kind": "cross_type_collision",
                             "path": f"rows[{c['row_index']}].output.entities",
                             "message": f"span {c['span']!r} tagged as both {c['types'][0]!r} and {c['types'][1]!r}; pick one"}
                            for c in collisions
                        ],
                    )
                trailing = find_trailing_punctuation_spans(task, latest_annotation)
                if trailing:
                    raise SchemaValidationError(
                        f"underlying annotation has {len(trailing)} span(s) with trailing sentence punctuation; "
                        f"use submit_correction or request_changes",
                        [
                            {"kind": "trailing_punctuation_span",
                             "path": f"rows[{t['row_index']}].output.{t['field']}",
                             "message": f"span {t['span']!r} should be {t['trimmed']!r} — trim trailing punctuation"}
                            for t in trailing
                        ],
                    )
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
        # Verbatim check — operator-submitted corrections must use exact spans
        # from the input, same as annotator/arbiter outputs. Without this, an
        # operator could paste a normalized / paraphrased span and ACCEPT a
        # task with a non-verbatim span (the same defect we just fixed in
        # the arbiter path).
        violations = find_verbatim_violations(task, answer)
        if violations:
            raise SchemaValidationError(
                f"corrected answer has {len(violations)} non-verbatim span(s)",
                [
                    {"kind": "non_verbatim_span", "path": f"rows[{v['row_index']}].output.{v['field']}",
                     "message": f"span {v['span']!r} is not a verbatim substring of the row's input.text"}
                    for v in violations
                ],
            )
        # Cross-type collision — same span tagged as two entity types in one row.
        # Block, same as annotator/arbiter paths.
        collisions = find_cross_type_collisions(answer)
        if collisions:
            raise SchemaValidationError(
                f"corrected answer has {len(collisions)} cross-type entity collision(s)",
                [
                    {"kind": "cross_type_collision",
                     "path": f"rows[{c['row_index']}].output.entities",
                     "message": f"span {c['span']!r} tagged as both {c['types'][0]!r} and {c['types'][1]!r}; pick one"}
                    for c in collisions
                ],
            )
        # Trailing-punctuation span boundary — block "Mitul Mallik." when the
        # trimmed form is also verbatim in input.text.
        trailing = find_trailing_punctuation_spans(task, answer)
        if trailing:
            raise SchemaValidationError(
                f"corrected answer has {len(trailing)} span(s) with trailing sentence punctuation",
                [
                    {"kind": "trailing_punctuation_span",
                     "path": f"rows[{t['row_index']}].output.{t['field']}",
                     "message": f"span {t['span']!r} should be {t['trimmed']!r} — trim trailing punctuation"}
                    for t in trailing
                ],
            )

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
        # Auto-record entity conventions for any entity-type changes the
        # operator made vs the latest annotation. Captured per-project so
        # future tasks in the same project benefit from the human's call.
        self._record_conventions_from_correction(task, answer, actor)
        return HumanCorrectionResult(task=task, artifact=artifact, answer=answer)

    def _record_conventions_from_correction(self, task: Task, answer: dict, actor: str) -> None:
        from annotation_pipeline_skill.services.entity_convention_service import (
            EntityConventionService,
            extract_entity_type_decisions,
        )
        prior = self._latest_annotation_payload(task.task_id)
        decisions = extract_entity_type_decisions(prior, answer)
        if not decisions:
            return
        svc = EntityConventionService(self.store)
        for span, entity_type in decisions:
            try:
                svc.record_decision(
                    project_id=task.pipeline_id,
                    span=span,
                    entity_type=entity_type,
                    source=f"hr_correction:{actor}",
                    task_id=task.task_id,
                )
            except (ValueError, TypeError):
                continue

    def _latest_annotation_payload(self, task_id: str) -> dict | None:
        """Load and parse the most recent annotation_result artifact's inner
        annotation JSON. Returns None when there's no annotation_result yet
        or when the inner text isn't parseable JSON.

        Strips ``<think>...</think>`` reasoning blocks and a single leading
        markdown fence — same wrapper handling the runtime uses.
        """
        import re
        artifacts = [a for a in self.store.list_artifacts(task_id) if a.kind == "annotation_result"]
        if not artifacts:
            return None
        path = self.store.root / artifacts[-1].path
        if not path.exists():
            return None
        outer = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(outer, dict):
            return None
        text = outer.get("text")
        if not isinstance(text, str):
            return None
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip().startswith("```"):
                text = "\n".join(lines[1:-1]).strip()
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None

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
