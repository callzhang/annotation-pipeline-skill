from annotation_pipeline_skill.store.file_store import FileStore


def build_feedback_bundle(store: FileStore, task_id: str) -> dict:
    records = sorted(store.list_feedback(task_id), key=lambda record: record.created_at)
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
            }
            for record in records
        ],
    }
