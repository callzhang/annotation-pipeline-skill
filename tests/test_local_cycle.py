from annotation_pipeline_skill.config.models import ProjectConfig
from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.interfaces.cli import main
from annotation_pipeline_skill.runtime.local_cycle import run_local_cycle
from annotation_pipeline_skill.store.file_store import FileStore


def empty_config(human_review_required=False):
    return ProjectConfig(
        providers={},
        stage_routes={},
        annotators={},
        external_tasks={},
        human_review_required=human_review_required,
    )


def test_local_cycle_advances_ready_task_to_accepted(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.READY
    store.save_task(task)

    result = run_local_cycle(store, empty_config())

    loaded = store.load_task("task-1")
    assert result.started == 1
    assert loaded.status is TaskStatus.ACCEPTED
    assert [event.next_status for event in store.list_events("task-1")] == [
        TaskStatus.ANNOTATING,
        TaskStatus.VALIDATING,
        TaskStatus.QC,
        TaskStatus.ACCEPTED,
    ]
    assert store.list_attempts("task-1")[0].stage == "annotation"
    assert store.list_artifacts("task-1")[0].kind == "annotation_result"


def test_local_cycle_routes_to_human_review_when_required(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.READY
    store.save_task(task)

    run_local_cycle(store, empty_config(human_review_required=True))

    assert store.load_task("task-1").status is TaskStatus.HUMAN_REVIEW


def test_cli_run_cycle_uses_project_config(tmp_path):
    main(["init", "--project-root", str(tmp_path)])
    store = FileStore(tmp_path / ".annotation-pipeline")
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.READY
    store.save_task(task)

    exit_code = main(["run-cycle", "--project-root", str(tmp_path)])

    assert exit_code == 0
    assert store.load_task("task-1").status is TaskStatus.ACCEPTED


def test_local_cycle_can_auto_merge_accepted_task(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.READY
    store.save_task(task)

    result = run_local_cycle(store, empty_config(), auto_merge=True)

    loaded = store.load_task("task-1")
    assert result.started == 1
    assert result.accepted == 1
    assert result.merged == 1
    assert loaded.status is TaskStatus.MERGED
    assert [event.next_status for event in store.list_events("task-1")] == [
        TaskStatus.ANNOTATING,
        TaskStatus.VALIDATING,
        TaskStatus.QC,
        TaskStatus.ACCEPTED,
        TaskStatus.MERGED,
    ]
    outbox = store.list_outbox()
    assert len(outbox) == 1
    assert outbox[0].payload["result"]["status"] == "merged"


def test_cli_merge_accepted_moves_task_to_merged(tmp_path):
    main(["init", "--project-root", str(tmp_path)])
    store = FileStore(tmp_path / ".annotation-pipeline")
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)

    exit_code = main(["merge-accepted", "--project-root", str(tmp_path)])

    assert exit_code == 0
    assert store.load_task("task-1").status is TaskStatus.MERGED
    assert store.list_events("task-1")[-1].next_status is TaskStatus.MERGED
