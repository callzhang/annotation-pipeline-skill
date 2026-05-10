import multiprocessing as mp
from datetime import datetime, timedelta, timezone

from annotation_pipeline_skill.core.runtime import RuntimeLease
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def _try_acquire(args):
    root, lease_id, owner = args
    store = SqliteStore.open(root)
    lease = RuntimeLease(
        lease_id=lease_id, task_id="t-1", stage="annotate",
        acquired_at=datetime.now(timezone.utc),
        heartbeat_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        owner=owner,
    )
    result = store.save_runtime_lease(lease)
    store.close()
    return result


def _worker_save_task(args):
    from annotation_pipeline_skill.core.models import Task
    root, task_id = args
    store = SqliteStore.open(root)
    store.save_task(Task.new(task_id=task_id, pipeline_id="p", source_ref={"k": "v"}))
    store.close()


def test_only_one_worker_acquires_lease_for_same_task_stage(tmp_path):
    SqliteStore.open(tmp_path).close()  # ensure schema present
    args_list = [(str(tmp_path), f"L-{i}", f"worker-{i}") for i in range(8)]
    with mp.get_context("spawn").Pool(processes=4) as pool:
        results = pool.map(_try_acquire, args_list)

    assert sum(1 for r in results if r) == 1


def test_concurrent_task_writes_do_not_lose_data(tmp_path):
    SqliteStore.open(tmp_path).close()
    args_list = [(str(tmp_path), f"task-{i}") for i in range(40)]
    with mp.get_context("spawn").Pool(processes=4) as pool:
        pool.map(_worker_save_task, args_list)

    store = SqliteStore.open(tmp_path)
    assert len(store.list_tasks()) == 40
    store.close()
