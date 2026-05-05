# Runtime Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a monitored local runtime that records heartbeat, cycle stats, active runs, queue/capacity state, stale work, and due retries for `annotation-pipeline-skill`.

**Architecture:** Keep business task state in the existing `Task`/`Attempt`/`AuditEvent` models and add separate runtime read models under `annotation_pipeline_skill/core/runtime.py`. Persist runtime state under `.annotation-pipeline/runtime/` through `FileStore`, build API-ready snapshots in `annotation_pipeline_skill/runtime/snapshot.py`, and run local cycles through a focused `LocalRuntimeScheduler` that wraps the existing `SubagentRuntime` execution path.

**Tech Stack:** Python dataclasses, filesystem JSON/JSONL, argparse CLI, standard-library HTTP API, pytest, existing Vite/React dashboard API types only where needed.

---

## Scope

This plan implements Phase 1 from `docs/superpowers/specs/2026-05-04-runtime-first-optimization-blueprint-design.md`.

It does not implement Redis, Dramatiq, systemd, distributed leases, automatic stale repair, dashboard runtime panels, QC feedback depth, or training data export. It creates the runtime truth that later phases will consume.

## File Structure

- Create `annotation_pipeline_skill/core/runtime.py`
  - Runtime dataclasses and JSON serialization helpers.
  - Owns `RuntimeConfig`, `ActiveRun`, `RuntimeCycleStats`, `RuntimeStatus`, `QueueCounts`, `CapacitySnapshot`, and `RuntimeSnapshot`.
- Modify `annotation_pipeline_skill/store/file_store.py`
  - Add runtime directories and persistence helpers.
  - Owns saving/loading active runs, heartbeat, cycle stats, and latest runtime snapshot.
- Create `annotation_pipeline_skill/runtime/snapshot.py`
  - Builds runtime snapshots from `FileStore`, runtime config, and current task state.
  - Owns queue counts, stale detection, due retry detection, capacity accounting, and heartbeat freshness.
- Create `annotation_pipeline_skill/runtime/local_scheduler.py`
  - Runs one local monitored scheduler cycle.
  - Wraps existing subagent execution with active-run records and cycle stats.
- Modify `annotation_pipeline_skill/runtime/subagent_cycle.py`
  - Expose a public `run_task()` method so the monitored scheduler can run one task while owning active-run bookkeeping.
- Modify `annotation_pipeline_skill/config/models.py` and `annotation_pipeline_skill/config/loader.py`
  - Add runtime config parsing from `workflow.yaml`.
- Modify `annotation_pipeline_skill/interfaces/cli.py`
  - Add `annotation-pipeline runtime once|run|status`.
  - Keep existing `run-cycle` as a compatibility command that delegates to runtime once.
- Modify `annotation_pipeline_skill/interfaces/api.py`
  - Add `GET /api/runtime`, `GET /api/runtime/cycles`, and `POST /api/runtime/run-once`.
- Modify `README.md` and `docs/agent-operator-guide.md`
  - Document runtime commands and snapshot purpose.
- Add tests:
  - `tests/test_runtime_models.py`
  - `tests/test_runtime_store.py`
  - `tests/test_runtime_snapshot.py`
  - `tests/test_local_runtime_scheduler.py`
  - Extend `tests/test_cli.py`
  - Extend `tests/test_dashboard_api.py`

## Task 1: Runtime Domain Models

**Files:**
- Create: `annotation_pipeline_skill/core/runtime.py`
- Test: `tests/test_runtime_models.py`

- [ ] **Step 1: Write failing runtime model serialization tests**

Create `tests/test_runtime_models.py`:

```python
from datetime import datetime, timedelta, timezone

from annotation_pipeline_skill.core.runtime import (
    ActiveRun,
    CapacitySnapshot,
    QueueCounts,
    RuntimeConfig,
    RuntimeCycleStats,
    RuntimeSnapshot,
    RuntimeStatus,
)


def test_runtime_config_uses_safe_defaults():
    config = RuntimeConfig()

    assert config.max_concurrent_tasks == 4
    assert config.max_starts_per_cycle == 2
    assert config.stale_after_seconds == 600
    assert config.retry_delay_seconds == 3600
    assert config.loop_interval_seconds == 5


def test_active_run_round_trips_through_dict():
    started_at = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    run = ActiveRun(
        run_id="run-1",
        task_id="task-1",
        stage="annotation",
        attempt_id="attempt-1",
        provider_target="annotation",
        started_at=started_at,
        heartbeat_at=started_at + timedelta(seconds=3),
        metadata={"pid": 123},
    )

    loaded = ActiveRun.from_dict(run.to_dict())

    assert loaded == run


def test_runtime_snapshot_round_trips_through_dict():
    generated_at = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    snapshot = RuntimeSnapshot(
        generated_at=generated_at,
        runtime_status=RuntimeStatus(
            healthy=True,
            heartbeat_at=generated_at,
            heartbeat_age_seconds=0,
            active=True,
            errors=[],
        ),
        queue_counts=QueueCounts(pending=2, annotating=1, validating=0, qc=0, human_review=0, accepted=3, rejected=0),
        active_runs=[
            ActiveRun(
                run_id="run-1",
                task_id="task-1",
                stage="annotation",
                attempt_id="attempt-1",
                provider_target="annotation",
                started_at=generated_at,
                heartbeat_at=generated_at,
            )
        ],
        capacity=CapacitySnapshot(max_concurrent_tasks=4, max_starts_per_cycle=2, active_count=1, available_slots=3),
        stale_tasks=[],
        due_retries=["task-2"],
        project_summaries=[{"project_id": "demo", "task_count": 6}],
        cycle_stats=[
            RuntimeCycleStats(
                cycle_id="cycle-1",
                started_at=generated_at,
                finished_at=generated_at,
                started=1,
                accepted=1,
                failed=0,
                capacity_available=3,
                errors=[],
            )
        ],
    )

    loaded = RuntimeSnapshot.from_dict(snapshot.to_dict())

    assert loaded == snapshot
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_runtime_models.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'annotation_pipeline_skill.core.runtime'`.

- [ ] **Step 3: Implement runtime dataclasses**

Create `annotation_pipeline_skill/core/runtime.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt_from_str(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True)
class RuntimeConfig:
    max_concurrent_tasks: int = 4
    max_starts_per_cycle: int = 2
    stale_after_seconds: int = 600
    retry_delay_seconds: int = 3600
    loop_interval_seconds: int = 5

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RuntimeConfig":
        values = data or {}
        return cls(
            max_concurrent_tasks=int(values.get("max_concurrent_tasks", 4)),
            max_starts_per_cycle=int(values.get("max_starts_per_cycle", 2)),
            stale_after_seconds=int(values.get("stale_after_seconds", 600)),
            retry_delay_seconds=int(values.get("retry_delay_seconds", 3600)),
            loop_interval_seconds=int(values.get("loop_interval_seconds", 5)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "max_starts_per_cycle": self.max_starts_per_cycle,
            "stale_after_seconds": self.stale_after_seconds,
            "retry_delay_seconds": self.retry_delay_seconds,
            "loop_interval_seconds": self.loop_interval_seconds,
        }


@dataclass(frozen=True)
class ActiveRun:
    run_id: str
    task_id: str
    stage: str
    attempt_id: str
    provider_target: str
    started_at: datetime
    heartbeat_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "stage": self.stage,
            "attempt_id": self.attempt_id,
            "provider_target": self.provider_target,
            "started_at": _dt_to_str(self.started_at),
            "heartbeat_at": _dt_to_str(self.heartbeat_at),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActiveRun":
        return cls(
            run_id=data["run_id"],
            task_id=data["task_id"],
            stage=data["stage"],
            attempt_id=data["attempt_id"],
            provider_target=data["provider_target"],
            started_at=_dt_from_str(data["started_at"]),
            heartbeat_at=_dt_from_str(data["heartbeat_at"]),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class RuntimeCycleStats:
    cycle_id: str
    started_at: datetime
    finished_at: datetime
    started: int
    accepted: int
    failed: int
    capacity_available: int
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "started_at": _dt_to_str(self.started_at),
            "finished_at": _dt_to_str(self.finished_at),
            "started": self.started,
            "accepted": self.accepted,
            "failed": self.failed,
            "capacity_available": self.capacity_available,
            "errors": self.errors,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeCycleStats":
        return cls(
            cycle_id=data["cycle_id"],
            started_at=_dt_from_str(data["started_at"]),
            finished_at=_dt_from_str(data["finished_at"]),
            started=int(data["started"]),
            accepted=int(data["accepted"]),
            failed=int(data["failed"]),
            capacity_available=int(data["capacity_available"]),
            errors=list(data.get("errors", [])),
        )


@dataclass(frozen=True)
class RuntimeStatus:
    healthy: bool
    heartbeat_at: datetime | None
    heartbeat_age_seconds: int | None
    active: bool
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "heartbeat_at": _dt_to_str(self.heartbeat_at),
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "active": self.active,
            "errors": self.errors,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeStatus":
        return cls(
            healthy=bool(data["healthy"]),
            heartbeat_at=_dt_from_str(data.get("heartbeat_at")),
            heartbeat_age_seconds=data.get("heartbeat_age_seconds"),
            active=bool(data["active"]),
            errors=list(data.get("errors", [])),
        )


@dataclass(frozen=True)
class QueueCounts:
    pending: int
    annotating: int
    validating: int
    qc: int
    human_review: int
    accepted: int
    rejected: int

    def to_dict(self) -> dict[str, int]:
        return {
            "pending": self.pending,
            "annotating": self.annotating,
            "validating": self.validating,
            "qc": self.qc,
            "human_review": self.human_review,
            "accepted": self.accepted,
            "rejected": self.rejected,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueueCounts":
        return cls(
            pending=int(data.get("pending", 0)),
            annotating=int(data.get("annotating", 0)),
            validating=int(data.get("validating", 0)),
            qc=int(data.get("qc", 0)),
            human_review=int(data.get("human_review", 0)),
            accepted=int(data.get("accepted", 0)),
            rejected=int(data.get("rejected", 0)),
        )


@dataclass(frozen=True)
class CapacitySnapshot:
    max_concurrent_tasks: int
    max_starts_per_cycle: int
    active_count: int
    available_slots: int

    def to_dict(self) -> dict[str, int]:
        return {
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "max_starts_per_cycle": self.max_starts_per_cycle,
            "active_count": self.active_count,
            "available_slots": self.available_slots,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapacitySnapshot":
        return cls(
            max_concurrent_tasks=int(data["max_concurrent_tasks"]),
            max_starts_per_cycle=int(data["max_starts_per_cycle"]),
            active_count=int(data["active_count"]),
            available_slots=int(data["available_slots"]),
        )


@dataclass(frozen=True)
class RuntimeSnapshot:
    generated_at: datetime
    runtime_status: RuntimeStatus
    queue_counts: QueueCounts
    active_runs: list[ActiveRun]
    capacity: CapacitySnapshot
    stale_tasks: list[str]
    due_retries: list[str]
    project_summaries: list[dict[str, Any]]
    cycle_stats: list[RuntimeCycleStats]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": _dt_to_str(self.generated_at),
            "runtime_status": self.runtime_status.to_dict(),
            "queue_counts": self.queue_counts.to_dict(),
            "active_runs": [run.to_dict() for run in self.active_runs],
            "capacity": self.capacity.to_dict(),
            "stale_tasks": self.stale_tasks,
            "due_retries": self.due_retries,
            "project_summaries": self.project_summaries,
            "cycle_stats": [stats.to_dict() for stats in self.cycle_stats],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeSnapshot":
        return cls(
            generated_at=_dt_from_str(data["generated_at"]),
            runtime_status=RuntimeStatus.from_dict(data["runtime_status"]),
            queue_counts=QueueCounts.from_dict(data["queue_counts"]),
            active_runs=[ActiveRun.from_dict(item) for item in data.get("active_runs", [])],
            capacity=CapacitySnapshot.from_dict(data["capacity"]),
            stale_tasks=list(data.get("stale_tasks", [])),
            due_retries=list(data.get("due_retries", [])),
            project_summaries=list(data.get("project_summaries", [])),
            cycle_stats=[RuntimeCycleStats.from_dict(item) for item in data.get("cycle_stats", [])],
        )
```

- [ ] **Step 4: Run runtime model tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_runtime_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit runtime model foundation**

```bash
git add annotation_pipeline_skill/core/runtime.py tests/test_runtime_models.py
git commit -m "feat: add runtime read model types"
```

## Task 2: Runtime Persistence In FileStore

**Files:**
- Modify: `annotation_pipeline_skill/store/file_store.py`
- Test: `tests/test_runtime_store.py`

- [ ] **Step 1: Write failing runtime store tests**

Create `tests/test_runtime_store.py`:

```python
from datetime import datetime, timezone

from annotation_pipeline_skill.core.runtime import (
    ActiveRun,
    CapacitySnapshot,
    QueueCounts,
    RuntimeConfig,
    RuntimeCycleStats,
    RuntimeSnapshot,
    RuntimeStatus,
)
from annotation_pipeline_skill.store.file_store import FileStore


def test_file_store_saves_loads_and_deletes_active_runs(tmp_path):
    store = FileStore(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    run = ActiveRun(
        run_id="run-1",
        task_id="task-1",
        stage="annotation",
        attempt_id="attempt-1",
        provider_target="annotation",
        started_at=now,
        heartbeat_at=now,
    )

    store.save_active_run(run)

    assert store.list_active_runs() == [run]

    store.delete_active_run("run-1")

    assert store.list_active_runs() == []


def test_file_store_saves_heartbeat_cycle_stats_and_snapshot(tmp_path):
    store = FileStore(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    stats = RuntimeCycleStats(
        cycle_id="cycle-1",
        started_at=now,
        finished_at=now,
        started=1,
        accepted=1,
        failed=0,
        capacity_available=4,
        errors=[],
    )
    snapshot = RuntimeSnapshot(
        generated_at=now,
        runtime_status=RuntimeStatus(healthy=True, heartbeat_at=now, heartbeat_age_seconds=0, active=True),
        queue_counts=QueueCounts(pending=0, annotating=0, validating=0, qc=0, human_review=0, accepted=1, rejected=0),
        active_runs=[],
        capacity=CapacitySnapshot(max_concurrent_tasks=4, max_starts_per_cycle=2, active_count=0, available_slots=4),
        stale_tasks=[],
        due_retries=[],
        project_summaries=[],
        cycle_stats=[stats],
    )

    store.save_runtime_heartbeat(now)
    store.append_runtime_cycle_stats(stats)
    store.save_runtime_snapshot(snapshot)

    assert store.load_runtime_heartbeat() == now
    assert store.list_runtime_cycle_stats() == [stats]
    assert store.load_runtime_snapshot() == snapshot
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_runtime_store.py -q
```

Expected: FAIL with `AttributeError` for missing `save_active_run`.

- [ ] **Step 3: Add runtime persistence methods**

Modify `annotation_pipeline_skill/store/file_store.py` imports:

```python
from datetime import datetime
```

Add runtime imports:

```python
from annotation_pipeline_skill.core.runtime import ActiveRun, RuntimeCycleStats, RuntimeSnapshot
```

In `FileStore.__init__`, add:

```python
self.runtime_dir = self.root / "runtime"
self.active_runs_dir = self.runtime_dir / "active_runs"
self.runtime_cycles_path = self.runtime_dir / "cycle_stats.jsonl"
self.runtime_heartbeat_path = self.runtime_dir / "heartbeat.json"
self.runtime_snapshot_path = self.runtime_dir / "runtime_snapshot.json"
```

Add `self.runtime_dir` and `self.active_runs_dir` to the directory creation tuple.

Add methods to `FileStore`:

```python
def save_active_run(self, run: ActiveRun) -> None:
    self._write_json(self.active_runs_dir / f"{run.run_id}.json", run.to_dict())


def list_active_runs(self) -> list[ActiveRun]:
    return [
        ActiveRun.from_dict(self._read_json(path))
        for path in sorted(self.active_runs_dir.glob("*.json"))
    ]


def delete_active_run(self, run_id: str) -> None:
    (self.active_runs_dir / f"{run_id}.json").unlink(missing_ok=True)


def save_runtime_heartbeat(self, heartbeat_at: datetime) -> None:
    self._write_json(self.runtime_heartbeat_path, {"heartbeat_at": heartbeat_at.isoformat()})


def load_runtime_heartbeat(self) -> datetime | None:
    if not self.runtime_heartbeat_path.exists():
        return None
    payload = self._read_json(self.runtime_heartbeat_path)
    value = payload.get("heartbeat_at")
    return datetime.fromisoformat(value) if value else None


def append_runtime_cycle_stats(self, stats: RuntimeCycleStats) -> None:
    self._append_jsonl(self.runtime_cycles_path, stats.to_dict())


def list_runtime_cycle_stats(self) -> list[RuntimeCycleStats]:
    return self._read_jsonl(self.runtime_cycles_path, RuntimeCycleStats.from_dict)


def save_runtime_snapshot(self, snapshot: RuntimeSnapshot) -> None:
    self._write_json(self.runtime_snapshot_path, snapshot.to_dict())


def load_runtime_snapshot(self) -> RuntimeSnapshot | None:
    if not self.runtime_snapshot_path.exists():
        return None
    return RuntimeSnapshot.from_dict(self._read_json(self.runtime_snapshot_path))
```

- [ ] **Step 4: Run runtime store tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_runtime_store.py -q
```

Expected: PASS.

- [ ] **Step 5: Run existing file store tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_file_store.py tests/test_runtime_store.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit runtime store persistence**

```bash
git add annotation_pipeline_skill/store/file_store.py tests/test_runtime_store.py
git commit -m "feat: persist local runtime state"
```

## Task 3: Runtime Snapshot Builder

**Files:**
- Create: `annotation_pipeline_skill/runtime/snapshot.py`
- Test: `tests/test_runtime_snapshot.py`

- [ ] **Step 1: Write failing snapshot tests**

Create `tests/test_runtime_snapshot.py`:

```python
from datetime import datetime, timedelta, timezone

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.runtime import ActiveRun, RuntimeConfig
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot
from annotation_pipeline_skill.store.file_store import FileStore


def test_runtime_snapshot_counts_queues_capacity_projects_and_due_retries(tmp_path):
    store = FileStore(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    pending = Task.new(task_id="alpha-1", pipeline_id="alpha", source_ref={"kind": "jsonl"})
    pending.status = TaskStatus.PENDING
    retry = Task.new(task_id="beta-1", pipeline_id="beta", source_ref={"kind": "jsonl"})
    retry.status = TaskStatus.ANNOTATING
    retry.next_retry_at = now - timedelta(seconds=1)
    accepted = Task.new(task_id="alpha-2", pipeline_id="alpha", source_ref={"kind": "jsonl"})
    accepted.status = TaskStatus.ACCEPTED
    store.save_task(pending)
    store.save_task(retry)
    store.save_task(accepted)
    store.save_active_run(
        ActiveRun(
            run_id="run-1",
            task_id="beta-1",
            stage="annotation",
            attempt_id="attempt-1",
            provider_target="annotation",
            started_at=now,
            heartbeat_at=now,
        )
    )
    store.save_runtime_heartbeat(now)

    snapshot = build_runtime_snapshot(
        store,
        RuntimeConfig(max_concurrent_tasks=4, max_starts_per_cycle=2),
        now=now,
    )

    assert snapshot.runtime_status.healthy is True
    assert snapshot.queue_counts.pending == 1
    assert snapshot.queue_counts.annotating == 1
    assert snapshot.queue_counts.accepted == 1
    assert snapshot.capacity.active_count == 1
    assert snapshot.capacity.available_slots == 3
    assert snapshot.due_retries == ["beta-1"]
    assert snapshot.project_summaries == [
        {"project_id": "alpha", "status_counts": {"accepted": 1, "pending": 1}, "task_count": 2},
        {"project_id": "beta", "status_counts": {"annotating": 1}, "task_count": 1},
    ]


def test_runtime_snapshot_marks_missing_heartbeat_unhealthy(tmp_path):
    store = FileStore(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)

    snapshot = build_runtime_snapshot(store, RuntimeConfig(), now=now)

    assert snapshot.runtime_status.healthy is False
    assert "heartbeat_missing" in snapshot.runtime_status.errors


def test_runtime_snapshot_detects_stale_active_runs(tmp_path):
    store = FileStore(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    stale_at = now - timedelta(seconds=601)
    store.save_runtime_heartbeat(now)
    store.save_active_run(
        ActiveRun(
            run_id="run-stale",
            task_id="task-stale",
            stage="annotation",
            attempt_id="attempt-1",
            provider_target="annotation",
            started_at=stale_at,
            heartbeat_at=stale_at,
        )
    )

    snapshot = build_runtime_snapshot(
        store,
        RuntimeConfig(stale_after_seconds=600),
        now=now,
    )

    assert snapshot.runtime_status.healthy is False
    assert snapshot.stale_tasks == ["task-stale"]
    assert "stale_active_runs" in snapshot.runtime_status.errors
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_runtime_snapshot.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `annotation_pipeline_skill.runtime.snapshot`.

- [ ] **Step 3: Implement snapshot builder**

Create `annotation_pipeline_skill/runtime/snapshot.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from annotation_pipeline_skill.core.runtime import (
    CapacitySnapshot,
    QueueCounts,
    RuntimeConfig,
    RuntimeSnapshot,
    RuntimeStatus,
)
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.services.dashboard_service import build_project_summaries
from annotation_pipeline_skill.store.file_store import FileStore


def build_runtime_snapshot(
    store: FileStore,
    config: RuntimeConfig,
    *,
    now: datetime | None = None,
) -> RuntimeSnapshot:
    current_time = now or datetime.now(timezone.utc)
    tasks = store.list_tasks()
    active_runs = store.list_active_runs()
    heartbeat_at = store.load_runtime_heartbeat()
    errors: list[str] = []
    heartbeat_age_seconds: int | None = None

    if heartbeat_at is None:
        errors.append("heartbeat_missing")
    else:
        heartbeat_age_seconds = int((current_time - heartbeat_at).total_seconds())
        if heartbeat_age_seconds > max(config.loop_interval_seconds * 2, 120):
            errors.append("heartbeat_stale")

    stale_tasks = [
        run.task_id
        for run in active_runs
        if int((current_time - run.heartbeat_at).total_seconds()) > config.stale_after_seconds
    ]
    if stale_tasks:
        errors.append("stale_active_runs")

    active_count = len(active_runs)
    available_slots = max(config.max_concurrent_tasks - active_count, 0)
    due_retries = sorted(
        task.task_id
        for task in tasks
        if task.next_retry_at is not None and task.next_retry_at <= current_time
    )

    return RuntimeSnapshot(
        generated_at=current_time,
        runtime_status=RuntimeStatus(
            healthy=not errors,
            heartbeat_at=heartbeat_at,
            heartbeat_age_seconds=heartbeat_age_seconds,
            active=heartbeat_at is not None,
            errors=errors,
        ),
        queue_counts=_queue_counts(tasks),
        active_runs=active_runs,
        capacity=CapacitySnapshot(
            max_concurrent_tasks=config.max_concurrent_tasks,
            max_starts_per_cycle=config.max_starts_per_cycle,
            active_count=active_count,
            available_slots=available_slots,
        ),
        stale_tasks=sorted(stale_tasks),
        due_retries=due_retries,
        project_summaries=build_project_summaries(store)["projects"],
        cycle_stats=store.list_runtime_cycle_stats(),
    )


def _queue_counts(tasks) -> QueueCounts:
    counts = {status.value: 0 for status in TaskStatus}
    for task in tasks:
        counts[task.status.value] = counts.get(task.status.value, 0) + 1
    return QueueCounts(
        pending=counts.get(TaskStatus.PENDING.value, 0),
        annotating=counts.get(TaskStatus.ANNOTATING.value, 0),
        validating=counts.get(TaskStatus.VALIDATING.value, 0),
        qc=counts.get(TaskStatus.QC.value, 0),
        human_review=counts.get(TaskStatus.HUMAN_REVIEW.value, 0),
        accepted=counts.get(TaskStatus.ACCEPTED.value, 0),
        rejected=counts.get(TaskStatus.REJECTED.value, 0),
    )
```

- [ ] **Step 4: Run snapshot tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_runtime_snapshot.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit snapshot builder**

```bash
git add annotation_pipeline_skill/runtime/snapshot.py tests/test_runtime_snapshot.py
git commit -m "feat: build runtime snapshots"
```

## Task 4: Local Runtime Scheduler

**Files:**
- Create: `annotation_pipeline_skill/runtime/local_scheduler.py`
- Modify: `annotation_pipeline_skill/runtime/subagent_cycle.py`
- Test: `tests/test_local_runtime_scheduler.py`

- [ ] **Step 1: Write failing scheduler tests**

Create `tests/test_local_runtime_scheduler.py`:

```python
from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.runtime import RuntimeConfig
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.llm.client import LLMGenerateResult
from annotation_pipeline_skill.runtime.local_scheduler import LocalRuntimeScheduler
from annotation_pipeline_skill.store.file_store import FileStore


class StubLLMClient:
    async def generate(self, request):
        return LLMGenerateResult(
            runtime="test_runtime",
            provider="test_provider",
            model="test-model",
            continuity_handle=None,
            final_text='{"labels":[]}',
            usage={"total_tokens": 10},
            raw_response={"id": "test"},
            diagnostics={},
        )


def test_local_runtime_scheduler_respects_max_starts_per_cycle(tmp_path):
    store = FileStore(tmp_path)
    for index in range(1, 4):
        task = Task.new(task_id=f"task-{index}", pipeline_id="pipe", source_ref={"kind": "jsonl"})
        task.status = TaskStatus.PENDING
        store.save_task(task)
    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=lambda target: StubLLMClient(),
        config=RuntimeConfig(max_concurrent_tasks=4, max_starts_per_cycle=2),
    )

    snapshot = scheduler.run_once(stage_target="annotation")

    assert snapshot.queue_counts.accepted == 2
    assert snapshot.queue_counts.pending == 1
    assert snapshot.cycle_stats[-1].started == 2
    assert snapshot.cycle_stats[-1].accepted == 2


def test_local_runtime_scheduler_respects_existing_active_capacity(tmp_path):
    from datetime import datetime, timezone
    from annotation_pipeline_skill.core.runtime import ActiveRun

    store = FileStore(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    store.save_active_run(
        ActiveRun(
            run_id="run-existing",
            task_id="existing-task",
            stage="annotation",
            attempt_id="attempt-1",
            provider_target="annotation",
            started_at=now,
            heartbeat_at=now,
        )
    )
    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=lambda target: StubLLMClient(),
        config=RuntimeConfig(max_concurrent_tasks=1, max_starts_per_cycle=2),
    )

    snapshot = scheduler.run_once(stage_target="annotation", now=now)

    assert snapshot.queue_counts.pending == 1
    assert snapshot.cycle_stats[-1].started == 0
    assert snapshot.cycle_stats[-1].capacity_available == 0


def test_local_runtime_scheduler_cleans_active_run_after_success(tmp_path):
    store = FileStore(tmp_path)
    task = Task.new(task_id="task-1", pipeline_id="pipe", source_ref={"kind": "jsonl"})
    task.status = TaskStatus.PENDING
    store.save_task(task)
    scheduler = LocalRuntimeScheduler(
        store=store,
        client_factory=lambda target: StubLLMClient(),
        config=RuntimeConfig(max_concurrent_tasks=1, max_starts_per_cycle=1),
    )

    scheduler.run_once(stage_target="annotation")

    assert store.list_active_runs() == []
    assert store.load_task("task-1").status is TaskStatus.ACCEPTED
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_local_runtime_scheduler.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `annotation_pipeline_skill.runtime.local_scheduler`.

- [ ] **Step 3: Expose public `SubagentRuntime.run_task`**

Modify `annotation_pipeline_skill/runtime/subagent_cycle.py`:

```python
def run_task(self, task: Task, stage_target: str = "annotation") -> None:
    self._run_task(task, stage_target)
```

Update `run_once` to call `self.run_task(task, stage_target)` instead of `self._run_task(task, stage_target)`.

- [ ] **Step 4: Implement local scheduler**

Create `annotation_pipeline_skill/runtime/local_scheduler.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from annotation_pipeline_skill.core.runtime import ActiveRun, RuntimeConfig, RuntimeCycleStats, RuntimeSnapshot
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.llm.client import LLMClient
from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot
from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
from annotation_pipeline_skill.store.file_store import FileStore


class LocalRuntimeScheduler:
    def __init__(self, store: FileStore, client_factory, config: RuntimeConfig):
        self.store = store
        self.client_factory = client_factory
        self.config = config

    def run_once(
        self,
        *,
        stage_target: str = "annotation",
        now: datetime | None = None,
    ) -> RuntimeSnapshot:
        started_at = now or datetime.now(timezone.utc)
        self.store.save_runtime_heartbeat(started_at)
        active_count = len(self.store.list_active_runs())
        capacity_available = max(self.config.max_concurrent_tasks - active_count, 0)
        start_limit = min(capacity_available, self.config.max_starts_per_cycle)
        pending_tasks = [
            task
            for task in self.store.list_tasks()
            if task.status is TaskStatus.PENDING
        ][:start_limit]

        runtime = SubagentRuntime(store=self.store, client_factory=self.client_factory)
        started = 0
        accepted = 0
        failed = 0
        errors: list[dict] = []

        for task in pending_tasks:
            attempt_id = f"{task.task_id}-attempt-{task.current_attempt + 1}"
            run = ActiveRun(
                run_id=f"run-{uuid4().hex}",
                task_id=task.task_id,
                stage="annotation",
                attempt_id=attempt_id,
                provider_target=stage_target,
                started_at=started_at,
                heartbeat_at=started_at,
            )
            self.store.save_active_run(run)
            started += 1
            try:
                runtime.run_task(task, stage_target=stage_target)
            except Exception as exc:
                failed += 1
                errors.append({"task_id": task.task_id, "error": type(exc).__name__, "message": str(exc)})
            finally:
                self.store.delete_active_run(run.run_id)
            if self.store.load_task(task.task_id).status is TaskStatus.ACCEPTED:
                accepted += 1

        finished_at = datetime.now(timezone.utc)
        stats = RuntimeCycleStats(
            cycle_id=f"cycle-{uuid4().hex}",
            started_at=started_at,
            finished_at=finished_at,
            started=started,
            accepted=accepted,
            failed=failed,
            capacity_available=capacity_available,
            errors=errors,
        )
        self.store.append_runtime_cycle_stats(stats)
        self.store.save_runtime_heartbeat(finished_at)
        snapshot = build_runtime_snapshot(self.store, self.config, now=finished_at)
        self.store.save_runtime_snapshot(snapshot)
        return snapshot
```

- [ ] **Step 5: Run scheduler tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_local_runtime_scheduler.py tests/test_subagent_cycle.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit local runtime scheduler**

```bash
git add annotation_pipeline_skill/runtime/local_scheduler.py annotation_pipeline_skill/runtime/subagent_cycle.py tests/test_local_runtime_scheduler.py
git commit -m "feat: add monitored local runtime scheduler"
```

## Task 5: Runtime Config, CLI, And API

**Files:**
- Modify: `annotation_pipeline_skill/config/models.py`
- Modify: `annotation_pipeline_skill/config/loader.py`
- Modify: `annotation_pipeline_skill/interfaces/cli.py`
- Modify: `annotation_pipeline_skill/interfaces/api.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write failing CLI tests**

Append to `tests/test_cli.py`:

```python
def test_cli_init_writes_runtime_config(tmp_path):
    main(["init", "--project-root", str(tmp_path)])

    workflow = (tmp_path / ".annotation-pipeline" / "workflow.yaml").read_text(encoding="utf-8")

    assert "runtime:" in workflow
    assert "max_concurrent_tasks: 4" in workflow


def test_cli_runtime_status_returns_snapshot_after_init(tmp_path, capsys):
    main(["init", "--project-root", str(tmp_path)])

    exit_code = main(["runtime", "status", "--project-root", str(tmp_path)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert "runtime_status" in payload
    assert payload["capacity"]["max_concurrent_tasks"] == 4
```

- [ ] **Step 2: Write failing API tests**

Append to `tests/test_dashboard_api.py`:

```python
def test_dashboard_api_returns_runtime_snapshot(tmp_path):
    store = FileStore(tmp_path)
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/runtime")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert "runtime_status" in payload
    assert "queue_counts" in payload


def test_dashboard_api_runs_one_runtime_cycle_with_injected_runner(tmp_path):
    store = FileStore(tmp_path)
    called = {"count": 0}

    def run_once():
        called["count"] += 1
        from annotation_pipeline_skill.core.runtime import RuntimeConfig
        from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot

        snapshot = build_runtime_snapshot(store, RuntimeConfig())
        store.save_runtime_snapshot(snapshot)
        return snapshot

    api = DashboardApi(store, runtime_once=run_once)

    status, _headers, body = api.handle_post("/api/runtime/run-once", b"{}")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert called["count"] == 1
    assert payload["ok"] is True
    assert "runtime_status" in payload["snapshot"]


def test_dashboard_api_returns_runtime_cycles(tmp_path):
    from datetime import datetime, timezone
    from annotation_pipeline_skill.core.runtime import RuntimeCycleStats

    store = FileStore(tmp_path)
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    store.append_runtime_cycle_stats(
        RuntimeCycleStats(
            cycle_id="cycle-1",
            started_at=now,
            finished_at=now,
            started=0,
            accepted=0,
            failed=0,
            capacity_available=4,
            errors=[],
        )
    )
    api = DashboardApi(store)

    status, _headers, body = api.handle_get("/api/runtime/cycles")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert payload["cycles"][0]["cycle_id"] == "cycle-1"
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_cli.py::test_cli_init_writes_runtime_config tests/test_cli.py::test_cli_runtime_status_returns_snapshot_after_init tests/test_dashboard_api.py::test_dashboard_api_returns_runtime_snapshot tests/test_dashboard_api.py::test_dashboard_api_returns_runtime_cycles -q
```

Expected: FAIL because CLI runtime command, API routes, and runtime runner injection do not exist.

- [ ] **Step 4: Add runtime config model and loader**

Modify `annotation_pipeline_skill/config/models.py`:

```python
from annotation_pipeline_skill.core.runtime import RuntimeConfig
```

Add `runtime: RuntimeConfig` to `ProjectConfig`.

Modify `annotation_pipeline_skill/config/loader.py` so `load_project_config()` reads `workflow.yaml` and passes:

```python
runtime=RuntimeConfig.from_dict(workflow_data.get("runtime"))
```

If the current loader already reads `workflow.yaml` into a variable, reuse that variable. Do not add a second YAML read.

- [ ] **Step 5: Add default runtime YAML to init**

Modify the `workflow.yaml` value in `annotation_pipeline_skill/interfaces/cli.py`:

```yaml
runtime:
  max_concurrent_tasks: 4
  max_starts_per_cycle: 2
  stale_after_seconds: 600
  retry_delay_seconds: 3600
  loop_interval_seconds: 5
```

- [ ] **Step 6: Add runtime CLI commands**

Modify `build_parser()` in `annotation_pipeline_skill/interfaces/cli.py`:

```python
runtime_parser = subparsers.add_parser("runtime")
runtime_subparsers = runtime_parser.add_subparsers(required=True)

runtime_once = runtime_subparsers.add_parser("once")
runtime_once.add_argument("--project-root", type=Path, default=Path.cwd())
runtime_once.add_argument("--stage-target", default="annotation")
runtime_once.set_defaults(handler=handle_runtime_once)

runtime_run = runtime_subparsers.add_parser("run")
runtime_run.add_argument("--project-root", type=Path, default=Path.cwd())
runtime_run.add_argument("--stage-target", default="annotation")
runtime_run.add_argument("--max-cycles", type=int, default=None)
runtime_run.set_defaults(handler=handle_runtime_run)

runtime_status = runtime_subparsers.add_parser("status")
runtime_status.add_argument("--project-root", type=Path, default=Path.cwd())
runtime_status.set_defaults(handler=handle_runtime_status)
```

Add imports:

```python
import time

from annotation_pipeline_skill.core.runtime import RuntimeConfig
from annotation_pipeline_skill.runtime.local_scheduler import LocalRuntimeScheduler
from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot
```

Add handlers:

```python
def _runtime_config(args: argparse.Namespace) -> RuntimeConfig:
    return load_project_config(args.project_root).runtime


def _local_scheduler(args: argparse.Namespace) -> LocalRuntimeScheduler:
    store = FileStore(args.project_root / ".annotation-pipeline")
    registry = load_llm_registry(args.project_root / ".annotation-pipeline" / "llm_profiles.yaml")
    return LocalRuntimeScheduler(
        store=store,
        client_factory=lambda target: _build_llm_client(registry.resolve(target)),
        config=_runtime_config(args),
    )


def handle_runtime_once(args: argparse.Namespace) -> int:
    snapshot = _local_scheduler(args).run_once(stage_target=args.stage_target)
    print(json.dumps(snapshot.to_dict(), sort_keys=True))
    return 0


def handle_runtime_status(args: argparse.Namespace) -> int:
    config = _runtime_config(args)
    store = FileStore(args.project_root / ".annotation-pipeline")
    snapshot = store.load_runtime_snapshot() or build_runtime_snapshot(store, config)
    print(json.dumps(snapshot.to_dict(), sort_keys=True))
    return 0


def handle_runtime_run(args: argparse.Namespace) -> int:
    config = _runtime_config(args)
    scheduler = _local_scheduler(args)
    cycles = 0
    while args.max_cycles is None or cycles < args.max_cycles:
        scheduler.run_once(stage_target=args.stage_target)
        cycles += 1
        if args.max_cycles is not None and cycles >= args.max_cycles:
            break
        time.sleep(config.loop_interval_seconds)
    return 0
```

Update `handle_run_cycle()` to delegate:

```python
def handle_run_cycle(args: argparse.Namespace) -> int:
    scheduler = _local_scheduler(args)
    scheduler.run_once(stage_target=args.stage_target)
    return 0
```

Update `handle_serve()` to pass a runtime runner into the API:

```python
def handle_serve(args: argparse.Namespace) -> int:
    scheduler = _local_scheduler(args)
    serve_dashboard_api(
        FileStore(args.project_root / ".annotation-pipeline"),
        host=args.host,
        port=args.port,
        runtime_once=lambda: scheduler.run_once(),
        runtime_config=_runtime_config(args),
    )
    return 0
```

- [ ] **Step 7: Add runtime API routes**

Modify `annotation_pipeline_skill/interfaces/api.py` imports:

```python
from annotation_pipeline_skill.core.runtime import RuntimeConfig
from annotation_pipeline_skill.runtime.snapshot import build_runtime_snapshot
```

Modify `DashboardApi.__init__`:

```python
class DashboardApi:
    def __init__(self, store: FileStore, *, runtime_once=None, runtime_config: RuntimeConfig | None = None):
        self.store = store
        self.runtime_once = runtime_once
        self.runtime_config = runtime_config or RuntimeConfig()
```

In `handle_get`, add before task routes:

```python
if route == "/api/runtime":
    snapshot = self.store.load_runtime_snapshot() or build_runtime_snapshot(self.store, self.runtime_config)
    return self._json_response(200, snapshot.to_dict())
if route == "/api/runtime/cycles":
    return self._json_response(200, {"cycles": [stats.to_dict() for stats in self.store.list_runtime_cycle_stats()]})
```

In `handle_post`, add:

```python
if route == "/api/runtime/run-once":
    if self.runtime_once is None:
        return self._json_response(409, {"error": "runtime_runner_unavailable"})
    snapshot = self.runtime_once()
    return self._json_response(200, {"ok": True, "snapshot": snapshot.to_dict()})
```

Modify `serve_dashboard_api` to pass the runtime dependencies:

```python
def serve_dashboard_api(
    store: FileStore,
    host: str,
    port: int,
    *,
    runtime_once=None,
    runtime_config: RuntimeConfig | None = None,
) -> None:
    server = ThreadingHTTPServer(
        (host, port),
        make_handler(DashboardApi(store, runtime_once=runtime_once, runtime_config=runtime_config)),
    )
    server.serve_forever()
```

- [ ] **Step 8: Run CLI and API tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_cli.py tests/test_dashboard_api.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit CLI and API runtime interfaces**

```bash
git add annotation_pipeline_skill/config/models.py annotation_pipeline_skill/config/loader.py annotation_pipeline_skill/interfaces/cli.py annotation_pipeline_skill/interfaces/api.py tests/test_cli.py tests/test_dashboard_api.py
git commit -m "feat: expose local runtime status interfaces"
```

## Task 6: Monitor Validation Service For P0 Runtime Checks

**Files:**
- Create: `annotation_pipeline_skill/runtime/monitor.py`
- Test: `tests/test_runtime_monitor.py`

- [ ] **Step 1: Write failing monitor tests**

Create `tests/test_runtime_monitor.py`:

```python
from datetime import datetime, timezone

from annotation_pipeline_skill.core.runtime import (
    CapacitySnapshot,
    QueueCounts,
    RuntimeSnapshot,
    RuntimeStatus,
)
from annotation_pipeline_skill.runtime.monitor import validate_runtime_snapshot


def snapshot(**overrides):
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    data = {
        "generated_at": now,
        "runtime_status": RuntimeStatus(healthy=True, heartbeat_at=now, heartbeat_age_seconds=0, active=True),
        "queue_counts": QueueCounts(pending=0, annotating=0, validating=0, qc=0, human_review=0, accepted=0, rejected=0),
        "active_runs": [],
        "capacity": CapacitySnapshot(max_concurrent_tasks=4, max_starts_per_cycle=2, active_count=0, available_slots=4),
        "stale_tasks": [],
        "due_retries": [],
        "project_summaries": [],
        "cycle_stats": [],
    }
    data.update(overrides)
    return RuntimeSnapshot(**data)


def test_monitor_reports_runtime_down():
    report = validate_runtime_snapshot(
        snapshot(runtime_status=RuntimeStatus(healthy=False, heartbeat_at=None, heartbeat_age_seconds=None, active=False, errors=["heartbeat_missing"]))
    )

    assert report["ok"] is False
    assert "runtime_unhealthy" in report["failures"]


def test_monitor_reports_stale_active_tasks():
    report = validate_runtime_snapshot(snapshot(stale_tasks=["task-1"]))

    assert report["ok"] is False
    assert "stale_active_tasks" in report["failures"]


def test_monitor_reports_due_retries_without_capacity_progress():
    report = validate_runtime_snapshot(
        snapshot(
            due_retries=["task-1"],
            capacity=CapacitySnapshot(max_concurrent_tasks=4, max_starts_per_cycle=2, active_count=0, available_slots=4),
        )
    )

    assert report["ok"] is False
    assert "due_retries_waiting" in report["failures"]


def test_monitor_reports_capacity_exceeded():
    report = validate_runtime_snapshot(
        snapshot(
            capacity=CapacitySnapshot(max_concurrent_tasks=1, max_starts_per_cycle=2, active_count=2, available_slots=0),
        )
    )

    assert report["ok"] is False
    assert "capacity_exceeded" in report["failures"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_runtime_monitor.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `annotation_pipeline_skill.runtime.monitor`.

- [ ] **Step 3: Implement monitor validation**

Create `annotation_pipeline_skill/runtime/monitor.py`:

```python
from __future__ import annotations

from annotation_pipeline_skill.core.runtime import RuntimeSnapshot


def validate_runtime_snapshot(snapshot: RuntimeSnapshot) -> dict:
    failures: list[str] = []

    if not snapshot.runtime_status.healthy:
        failures.append("runtime_unhealthy")
    if snapshot.stale_tasks:
        failures.append("stale_active_tasks")
    if snapshot.capacity.active_count > snapshot.capacity.max_concurrent_tasks:
        failures.append("capacity_exceeded")
    if snapshot.due_retries and snapshot.capacity.available_slots > 0 and snapshot.capacity.active_count == 0:
        failures.append("due_retries_waiting")
    if snapshot.queue_counts.pending > 0 and snapshot.capacity.available_slots > 0 and snapshot.capacity.active_count == 0:
        failures.append("runnable_backlog_waiting")

    return {"ok": not failures, "failures": failures}
```

- [ ] **Step 4: Run monitor tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest tests/test_runtime_monitor.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit monitor validation**

```bash
git add annotation_pipeline_skill/runtime/monitor.py tests/test_runtime_monitor.py
git commit -m "feat: validate local runtime snapshots"
```

## Task 7: Documentation And Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/agent-operator-guide.md`
- Test: full suite

- [ ] **Step 1: Update README runtime section**

Add after the existing run-cycle section in `README.md`:

````markdown
Inspect and run the monitored local runtime:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline runtime status --project-root ./demo-project

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline runtime once --project-root ./demo-project

UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run \
  annotation-pipeline runtime run --project-root ./demo-project --max-cycles 3
```

The runtime writes `.annotation-pipeline/runtime/runtime_snapshot.json`, heartbeat data, active-run records, and cycle stats. The snapshot is the local read model for runtime health, queue counts, capacity, stale tasks, and due retries.
````

- [ ] **Step 2: Update agent operator guide**

Add a “Runtime Operations” section to `docs/agent-operator-guide.md`:

```markdown
## Runtime Operations

Use `annotation-pipeline runtime status --project-root <project>` before starting work. A healthy project has a fresh heartbeat, no stale active runs, and capacity that is not exceeded.

Use `annotation-pipeline runtime once --project-root <project>` for one monitored cycle. Use `annotation-pipeline runtime run --project-root <project>` when the agent should keep the local project moving.

If runtime status shows stale tasks or due retries that are not draining, inspect task detail and event logs before changing annotation rules or provider config.
```

- [ ] **Step 3: Run backend tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run pytest -q
```

Expected: all backend tests pass.

- [ ] **Step 4: Run frontend tests**

Run:

```bash
npm test -- --run
```

Expected: all frontend tests pass.

- [ ] **Step 5: Run frontend build**

Run:

```bash
npm run build
```

Expected: build exits 0 and writes `web/dist/`.

- [ ] **Step 6: Run integration smoke**

Run:

```bash
PROJECT_ROOT="$(mktemp -d)"
INPUT_FILE="$PROJECT_ROOT/input.jsonl"
printf '{"text":"alpha"}\n{"text":"beta"}\n' > "$INPUT_FILE"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline init --project-root "$PROJECT_ROOT"
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline create-tasks --project-root "$PROJECT_ROOT" --source "$INPUT_FILE" --pipeline-id smoke
UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy uv run --with-editable . annotation-pipeline runtime status --project-root "$PROJECT_ROOT"
```

Expected: final command prints JSON containing `runtime_status`, `queue_counts`, and `capacity`.

- [ ] **Step 7: Commit docs after verification**

```bash
git add README.md docs/agent-operator-guide.md
git commit -m "docs: document monitored local runtime"
```

## Self-Review Checklist

- Spec coverage:
  - Runtime snapshot: Tasks 1-3.
  - Heartbeat and cycle stats: Tasks 2-4.
  - Active runs: Tasks 1-4.
  - Capacity and max starts: Tasks 3-4.
  - Stale and due retry detection: Tasks 3 and 6.
  - CLI runtime once/run/status: Task 5.
  - API runtime/cycles/run-once: Task 5.
  - P0 monitor checks: Task 6.
  - Docs: Task 7.
- Placeholder scan:
  - No task uses unresolved placeholder terms.
  - Each code-changing step includes concrete code or exact code blocks.
- Type consistency:
  - `RuntimeConfig`, `ActiveRun`, `RuntimeCycleStats`, `RuntimeStatus`, `QueueCounts`, `CapacitySnapshot`, and `RuntimeSnapshot` are introduced in Task 1 and reused consistently.
  - `FileStore` runtime methods introduced in Task 2 are reused by Tasks 3-5.
  - `build_runtime_snapshot()` introduced in Task 3 is reused by CLI/API and monitor tests.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-04-runtime-reliability.md`. Two execution options:

1. **Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
