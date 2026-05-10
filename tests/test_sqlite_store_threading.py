import threading
from pathlib import Path

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_store_usable_across_threads(tmp_path: Path):
    store = SqliteStore.open(tmp_path)
    store.save_task(Task.new(task_id="main", pipeline_id="p", source_ref={}))

    errors: list[Exception] = []
    results: list[int] = []

    def worker(idx: int):
        try:
            store.save_task(Task.new(task_id=f"w-{idx}", pipeline_id="p", source_ref={}))
            results.append(len(store.list_tasks()))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # Final main-thread view must see all 8 worker writes + the initial save.
    assert len(store.list_tasks()) == 9
    store.close()
