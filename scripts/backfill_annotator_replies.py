"""One-shot backfill: replay past annotation_result artifacts and persist any
`discussion_replies` (rows[].discussion_replies or top-level) as
FeedbackDiscussionEntry rows that the runtime missed before the fix.

Usage:
    python scripts/backfill_annotator_replies.py <project-root>

Idempotent: skips replies already recorded for the same
(feedback_id, role='annotator', metadata.attempt_id).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from annotation_pipeline_skill.core.models import FeedbackDiscussionEntry
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


_ATTEMPT_FROM_FILENAME = re.compile(r"-(attempt-\d+)_annotation_result\.json$")


def _attempt_id_from_artifact(task_id: str, artifact_path: str) -> str:
    """Best-effort attempt_id from the artifact filename, fallback to a synthetic id."""
    match = _ATTEMPT_FROM_FILENAME.search(artifact_path)
    if match:
        return f"{task_id}-{match.group(1)}"
    return f"{task_id}-attempt-unknown"


def _clamp_confidence(raw) -> float | None:
    if not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value != value:  # NaN
        return None
    return max(0.0, min(1.0, value))


def _collect_replies(payload: dict) -> list[dict]:
    out: list[dict] = []
    top = payload.get("discussion_replies")
    if isinstance(top, list):
        out.extend(top)
    rows = payload.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            inner = row.get("discussion_replies")
            if isinstance(inner, list):
                out.extend(inner)
    return out


def backfill(project_root: Path) -> dict[str, int]:
    store = SqliteStore.open(project_root / ".annotation-pipeline")
    stats = {"tasks": 0, "artifacts": 0, "wrote": 0, "skipped_existing": 0}
    try:
        for task in store.list_tasks():
            stats["tasks"] += 1
            existing = store.list_feedback_discussions(task.task_id)
            seen = {
                (e.feedback_id, e.role, str(e.metadata.get("attempt_id", "")))
                for e in existing
            }
            feedback_ids = {f.feedback_id for f in store.list_feedback(task.task_id)}
            artifacts = [a for a in store.list_artifacts(task.task_id) if a.kind == "annotation_result"]
            for artifact in artifacts:
                stats["artifacts"] += 1
                content_path = store.root / artifact.path
                if not content_path.exists():
                    continue
                try:
                    data = json.loads(content_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                text = data.get("text") if isinstance(data, dict) else None
                if not isinstance(text, str):
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                replies = _collect_replies(payload)
                if not replies:
                    continue
                attempt_id = _attempt_id_from_artifact(task.task_id, artifact.path)
                for reply in replies:
                    if not isinstance(reply, dict):
                        continue
                    fid = reply.get("feedback_id")
                    if not isinstance(fid, str) or fid not in feedback_ids:
                        continue
                    message = str(reply.get("message") or "").strip()
                    if not message:
                        continue
                    key = (fid, "annotator", attempt_id)
                    if key in seen:
                        stats["skipped_existing"] += 1
                        continue
                    conf = _clamp_confidence(reply.get("confidence"))
                    metadata: dict = {"attempt_id": attempt_id, "backfilled": True}
                    if conf is not None:
                        metadata["confidence"] = conf
                    store.append_feedback_discussion(
                        FeedbackDiscussionEntry.new(
                            task_id=task.task_id,
                            feedback_id=fid,
                            role="annotator",
                            stance=str(reply.get("stance") or "comment"),
                            message=message,
                            agreed_points=[
                                str(p) for p in (reply.get("agreed_points") or [])
                                if isinstance(p, str)
                            ],
                            disputed_points=[
                                str(p) for p in (reply.get("disputed_points") or [])
                                if isinstance(p, str)
                            ],
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
                    seen.add(key)
                    stats["wrote"] += 1
    finally:
        store.close()
    return stats


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    project_root = Path(sys.argv[1]).resolve()
    if not (project_root / ".annotation-pipeline").exists():
        print(f"no .annotation-pipeline under {project_root}")
        sys.exit(1)
    result = backfill(project_root)
    print(json.dumps(result, indent=2))
