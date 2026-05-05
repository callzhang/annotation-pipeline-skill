from annotation_pipeline_skill.core.models import FeedbackRecord, Task
from annotation_pipeline_skill.core.states import FeedbackSeverity, FeedbackSource, TaskStatus
from annotation_pipeline_skill.services.coordinator_service import CoordinatorService
from annotation_pipeline_skill.store.file_store import FileStore


def _task(store: FileStore, task_id: str, status: TaskStatus) -> Task:
    task = Task.new(
        task_id=task_id,
        pipeline_id="pipe",
        source_ref={"kind": "jsonl", "payload": {"text": "alpha"}},
        annotation_requirements={"annotation_types": ["entity_span"]},
    )
    task.status = status
    store.save_task(task)
    return task


def test_coordinator_report_summarizes_review_feedback_and_actions(tmp_path):
    store = FileStore(tmp_path)
    _task(store, "task-1", TaskStatus.HUMAN_REVIEW)
    _task(store, "task-2", TaskStatus.ACCEPTED)
    store.append_feedback(
        FeedbackRecord.new(
            task_id="task-1",
            attempt_id="attempt-1",
            source_stage=FeedbackSource.QC,
            severity=FeedbackSeverity.BLOCKING,
            category="missing_entity",
            message="Missing entity span.",
            target={"row": 1},
            suggested_action="manual_annotation",
            created_by="qc",
        )
    )

    report = CoordinatorService(store).build_report(project_id="pipe")

    assert report["task_count"] == 2
    assert report["status_counts"]["human_review"] == 1
    assert report["human_review_task_ids"] == ["task-1"]
    assert report["open_feedback_count"] == 1
    assert report["feedback_by_category"] == {"missing_entity": 1}
    assert "remind_user_to_complete_human_review" in report["recommended_actions"]
    assert report["provider_diagnostics"]["config_valid"] is False


def test_coordinator_records_rule_updates_and_long_tail_issues(tmp_path):
    store = FileStore(tmp_path)
    service = CoordinatorService(store)

    rule = service.record_rule_update(
        project_id="pipe",
        source="qc",
        summary="Boundary rule needs a concrete product example.",
        action="Add product-span example before rerun.",
        created_by="agent",
        task_ids=["task-2", "task-1"],
    )
    issue = service.record_long_tail_issue(
        project_id="pipe",
        category="ambiguous_abbreviation",
        summary="Abbreviations are repeatedly ambiguous.",
        recommended_action="Ask user for a disambiguation rule.",
        created_by="agent",
        task_ids=["task-1"],
    )

    assert rule["record_id"].startswith("rule-update-")
    assert rule["task_ids"] == ["task-1", "task-2"]
    assert issue["issue_id"].startswith("long-tail-")
    assert service.list_rule_updates("pipe")[0]["summary"].startswith("Boundary")
    assert service.list_long_tail_issues("pipe")[0]["category"] == "ambiguous_abbreviation"
