from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from annotation_pipeline_skill.core.models import ArtifactRef, Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.core.transitions import InvalidTransition, transition_task
from annotation_pipeline_skill.store.file_store import FileStore


@dataclass(frozen=True)
class HumanReviewDecisionResult:
    task: Task
    decision: dict
    artifact: ArtifactRef

    def to_dict(self) -> dict:
        return {
            "task": self.task.to_dict(),
            "decision": self.decision,
            "artifact": self.artifact.to_dict(),
        }


class HumanReviewService:
    def __init__(self, store: FileStore):
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
        artifact = self._write_decision_artifact(task_id, decision)
        event = transition_task(
            task,
            next_status,
            actor=actor,
            reason=reason,
            stage="human_review",
            metadata={
                "action": action,
                "correction_mode": correction_mode,
                "decision_artifact_id": artifact.artifact_id,
                "decision_artifact_path": artifact.path,
            },
        )
        self.store.append_artifact(artifact)
        self.store.append_event(event)
        self.store.save_task(task)
        return HumanReviewDecisionResult(task=task, decision=decision, artifact=artifact)

    def _transition_for_action(self, action: str) -> tuple[TaskStatus, str]:
        if action == "accept":
            return TaskStatus.ACCEPTED, "human review accepted task"
        if action == "reject":
            return TaskStatus.REJECTED, "human review rejected task"
        if action == "request_changes":
            return TaskStatus.ANNOTATING, "human review requested annotator changes"
        raise ValueError(f"unknown human review action: {action}")

    def _write_decision_artifact(self, task_id: str, decision: dict) -> ArtifactRef:
        relative_path = Path("artifact_payloads") / task_id / f"human_review_decision-{uuid4().hex}.json"
        absolute_path = self.store.root / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_text(json.dumps(decision, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return ArtifactRef.new(
            task_id=task_id,
            kind="human_review_decision",
            path=relative_path.as_posix(),
            content_type="application/json",
            metadata={
                "action": decision["action"],
                "correction_mode": decision["correction_mode"],
                "actor": decision["actor"],
            },
        )
