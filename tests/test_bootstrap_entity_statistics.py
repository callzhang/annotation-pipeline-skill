import json
import subprocess
import sys

from annotation_pipeline_skill.core.models import ArtifactRef, Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.services.entity_statistics_service import (
    EntityStatisticsService,
)
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def _accept_task_with_annotation(store, task_id, annotation, *, hr=False):
    task = Task.new(
        task_id=task_id, pipeline_id="p", source_ref={"kind": "jsonl", "payload": {}}
    )
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    kind = "human_review_answer" if hr else "annotation_result"
    rel_path = f"artifact_payloads/{task_id}/{kind}.json"
    abs_path = store.root / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    if hr:
        abs_path.write_text(json.dumps({"answer": annotation}), encoding="utf-8")
    else:
        abs_path.write_text(
            json.dumps({"text": json.dumps(annotation)}), encoding="utf-8"
        )
    store.append_artifact(ArtifactRef.new(
        task_id=task_id, kind=kind, path=rel_path, content_type="application/json",
    ))


def test_bootstrap_increments_stats_with_weighting(tmp_path):
    store = SqliteStore.open(tmp_path / "ws")
    # Three QC-pass tasks: each tags "Apple" as organization (weight 1)
    for i in range(3):
        _accept_task_with_annotation(
            store, f"t-{i}",
            {"rows": [{"row_index": 0, "output": {"entities": {"organization": ["Apple"]}}}]},
        )
    # One HR-corrected task: "Apple" as project (weight 5)
    _accept_task_with_annotation(
        store, "t-hr",
        {"rows": [{"row_index": 0, "output": {"entities": {"project": ["Apple"]}}}]},
        hr=True,
    )

    # Run the bootstrap script
    result = subprocess.run(
        [sys.executable, "scripts/bootstrap_entity_statistics.py", str(tmp_path / "ws")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr

    svc = EntityStatisticsService(store)
    dist = svc.distribution(project_id="p", span="Apple")
    assert dist == {"organization": 3, "project": 5}
