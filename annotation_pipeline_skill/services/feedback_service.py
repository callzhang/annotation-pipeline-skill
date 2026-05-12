from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def build_feedback_bundle(store: SqliteStore, task_id: str, *, limit: int = 6) -> dict:
    records = sorted(store.list_feedback(task_id), key=lambda record: record.created_at)
    # Keep only the most-recent N records to bound prompt growth across retries.
    records = records[-limit:]
    discussions = sorted(store.list_feedback_discussions(task_id), key=lambda entry: entry.created_at)
    return {
        "task_id": task_id,
        "items": [
            {
                "feedback_id": record.feedback_id,
                "attempt_id": record.attempt_id,
                "source_stage": record.source_stage.value,
                "severity": record.severity.value,
                "category": record.category,
                "message": record.message,
                "target": record.target,
                "suggested_action": record.suggested_action,
                "created_at": record.created_at.isoformat(),
                "created_by": record.created_by,
                "discussion": [
                    entry.to_dict()
                    for entry in discussions
                    if entry.feedback_id == record.feedback_id
                ],
                "consensus": any(
                    entry.consensus
                    for entry in discussions
                    if entry.feedback_id == record.feedback_id
                ),
            }
            for record in records
        ],
    }


def build_feedback_consensus_summary(store: SqliteStore, task_id: str) -> dict:
    feedback = store.list_feedback(task_id)
    discussions = store.list_feedback_discussions(task_id)
    consensus_feedback_ids = {
        entry.feedback_id
        for entry in discussions
        if entry.consensus
    }
    return {
        "task_id": task_id,
        "total_feedback": len(feedback),
        "consensus_feedback": len(consensus_feedback_ids),
        "open_feedback": [
            record.feedback_id
            for record in feedback
            if record.feedback_id not in consensus_feedback_ids
        ],
        "can_accept_by_consensus": bool(feedback) and len(consensus_feedback_ids) == len(feedback),
    }
