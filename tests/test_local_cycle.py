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
