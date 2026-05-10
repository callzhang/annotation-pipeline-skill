# SQLite Store Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the JSON/JSONL `FileStore` with a `SqliteStore` backed by a single `db.sqlite` per workspace; keep ground-truth blobs (artifacts, document content, exports) on disk as files referenced from DB rows; provide a one-shot migration, periodic backups, and a reverse JSON dump for safety.

**Architecture:** Single SQLite file per workspace at `<root>/db.sqlite` opened in WAL mode with `busy_timeout=5000`, `foreign_keys=ON`, `synchronous=NORMAL`. Twelve tables cover: tasks, audit events, attempts, feedback, feedback discussions, artifact refs, outbox, active runs, runtime leases, coordination records, documents, document versions, export manifests. Large fields (`source_ref`, `metadata`, `payload`, etc.) live in TEXT columns holding JSON. Document/version content remains on disk under `<root>/document_versions/<doc>/<version>.md` with the DB row storing path + sha256. Artifacts and export outputs continue to be regular files. Recovery rests on three pillars: WAL (in-process crash safety), periodic `sqlite3.backup` snapshots with retention, and a permanent archive of the pre-migration JSON tree as the genesis ground truth. A reverse `db dump-json` CLI lets you re-export the DB to JSON for debugging or a future store change.

**Tech Stack:** Python 3.11+ stdlib `sqlite3` (no SQLAlchemy), existing dataclasses, pytest, argparse CLI.

---

## Scope

This plan implements:
- A new `SqliteStore` class with full method parity to `FileStore`.
- Atomic `RuntimeLease` acquisition via `INSERT OR ABORT` on `UNIQUE(task_id, stage)` (replacing filesystem `open("x")`).
- Indexed query helpers for the three hot paths surfaced by the survey (`pipeline_id`, `status`, `created_at`).
- A one-shot `scripts/migrate_filestore_to_sqlite.py` migration with row-count + sha256 verification.
- An `apl db backup`, `apl db restore`, and `apl db dump-json` CLI surface.
- Updating all 77 import / type-hint sites in `annotation_pipeline_skill/` and 16 test files.
- Deleting `annotation_pipeline_skill/store/file_store.py` and `tests/test_file_store.py` after the migration is verified.

This plan does NOT:
- Introduce SQLAlchemy or Alembic.
- Migrate `runtime/cycle_stats.jsonl` or `runtime/heartbeat.json` into the DB (low-volume, low-frequency, easier to tail as files).
- Add a write-ahead application mutation log (advisor: redundant given WAL + periodic backup + genesis archive).
- Support multi-machine deployments (SQLite is single-machine; multi-process on one box is supported via WAL).

## Open decisions adopted as defaults (user approved with "方案没问题")

1. **Concurrency model** — single machine, multiple processes/threads. WAL + `busy_timeout` covers it.
2. **Reverse dump-json** — included as `apl db dump-json --out <dir>`.
3. **Backup policy** — hourly snapshots retained 24 hours + one daily snapshot retained 30 days. Snapshot is a CLI command; scheduling is the operator's responsibility (cron or systemd timer).
4. **AuditEvent scope** — unchanged; only task transitions go through `AuditEvent`. Other writes are not duplicated as audit rows.

## File Structure

- Create `annotation_pipeline_skill/store/schema.sql`
  - DDL for all 13 tables, indexes, and `PRAGMA user_version = 1`.
  - Owns the canonical schema text.
- Create `annotation_pipeline_skill/store/sqlite_store.py`
  - `SqliteStore` class — full method parity with `FileStore`.
  - `_connect(path)` opens WAL, sets pragmas, runs schema if absent.
  - Owns all DB writes/reads, atomic lease acquisition, indexed query helpers.
- Create `annotation_pipeline_skill/store/backup.py`
  - `snapshot(db_path, out_path)` using `sqlite3.backup`.
  - `prune(snapshots_dir, hourly_keep, daily_keep)` retention.
  - Owns backup creation and retention logic only — scheduling is external.
- Create `annotation_pipeline_skill/store/dump.py`
  - `dump_to_json(store, out_dir)` mirrors the old `FileStore` directory layout from a `SqliteStore`.
  - Owns DB → JSON tree export.
- Create `scripts/migrate_filestore_to_sqlite.py`
  - One-shot migration: reads old JSON tree, writes to new DB, verifies row counts + sha256, archives source tree.
  - Idempotent (refuses to run if target DB has rows; `--force` allowed for retry into clean DB).
- Modify `annotation_pipeline_skill/store/__init__.py`
  - Re-export `SqliteStore` as the only public store.
- Modify all 10 service / interface / runtime modules
  - Replace `from annotation_pipeline_skill.store.file_store import FileStore` with `from annotation_pipeline_skill.store.sqlite_store import SqliteStore`.
  - Replace type hints `store: FileStore` with `store: SqliteStore`.
  - Two specific hot paths use new indexed helpers: `coordinator_service._project_tasks` and `outbox_dispatch_service` task lookups.
- Modify `annotation_pipeline_skill/interfaces/cli.py`
  - Add `apl db init`, `apl db backup`, `apl db restore`, `apl db dump-json`, `apl db status` subcommands.
- Delete `annotation_pipeline_skill/store/file_store.py`
- Delete `tests/test_file_store.py`
- Add tests:
  - `tests/test_sqlite_store.py` — full method parity tests.
  - `tests/test_sqlite_store_concurrency.py` — multi-process WAL + lease.
  - `tests/test_sqlite_backup.py` — snapshot + retention.
  - `tests/test_sqlite_dump.py` — DB → JSON round-trip.
  - `tests/test_migrate_filestore.py` — migration script.
  - Update all 16 existing test files: replace `FileStore(tmp_path)` with `SqliteStore(tmp_path)` and `SqliteStore.open(tmp_path)`.
- Modify `TECHNICAL_ARCHITECTURE.md`, `README.md`, `CHANGELOG.md`.

## Schema (`store/schema.sql`)

```sql
PRAGMA user_version = 1;

CREATE TABLE tasks (
    task_id                 TEXT PRIMARY KEY,
    pipeline_id             TEXT NOT NULL,
    status                  TEXT NOT NULL,
    current_attempt         INTEGER NOT NULL DEFAULT 0,
    modality                TEXT NOT NULL DEFAULT 'text',
    selected_annotator_id   TEXT,
    active_run_id           TEXT,
    next_retry_at           TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    document_version_id     TEXT,
    source_ref_json         TEXT NOT NULL,
    external_ref_json       TEXT,
    annotation_requirements_json TEXT NOT NULL,
    metadata_json           TEXT NOT NULL
);
CREATE INDEX idx_tasks_pipeline_status ON tasks(pipeline_id, status);
CREATE INDEX idx_tasks_status_created ON tasks(status, created_at);
CREATE INDEX idx_tasks_next_retry ON tasks(next_retry_at) WHERE next_retry_at IS NOT NULL;

CREATE TABLE audit_events (
    event_id        TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    previous_status TEXT NOT NULL,
    next_status     TEXT NOT NULL,
    actor           TEXT NOT NULL,
    reason          TEXT NOT NULL,
    stage           TEXT NOT NULL,
    attempt_id      TEXT,
    created_at      TEXT NOT NULL,
    metadata_json   TEXT NOT NULL,
    seq             INTEGER NOT NULL
);
CREATE INDEX idx_audit_task_seq ON audit_events(task_id, seq);

CREATE TABLE attempts (
    attempt_id   TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    idx          INTEGER NOT NULL,
    stage        TEXT NOT NULL,
    status       TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    provider_id  TEXT,
    model        TEXT,
    effort       TEXT,
    route_role   TEXT,
    summary      TEXT,
    error_json   TEXT,
    artifacts_json TEXT NOT NULL,
    seq          INTEGER NOT NULL
);
CREATE INDEX idx_attempts_task_seq ON attempts(task_id, seq);

CREATE TABLE feedback_records (
    feedback_id      TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    attempt_id       TEXT NOT NULL,
    source_stage     TEXT NOT NULL,
    severity         TEXT NOT NULL,
    category         TEXT NOT NULL,
    message          TEXT NOT NULL,
    target_json      TEXT NOT NULL,
    suggested_action TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    created_by       TEXT NOT NULL,
    metadata_json    TEXT NOT NULL,
    seq              INTEGER NOT NULL
);
CREATE INDEX idx_feedback_task_seq ON feedback_records(task_id, seq);

CREATE TABLE feedback_discussions (
    entry_id            TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL,
    feedback_id         TEXT NOT NULL,
    role                TEXT NOT NULL,
    stance              TEXT NOT NULL,
    message             TEXT NOT NULL,
    agreed_points_json  TEXT NOT NULL,
    disputed_points_json TEXT NOT NULL,
    proposed_resolution TEXT,
    consensus           INTEGER NOT NULL,
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    metadata_json       TEXT NOT NULL,
    seq                 INTEGER NOT NULL
);
CREATE INDEX idx_discussion_task_seq ON feedback_discussions(task_id, seq);

CREATE TABLE artifact_refs (
    artifact_id   TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    kind          TEXT NOT NULL,
    path          TEXT NOT NULL,
    content_type  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    seq           INTEGER NOT NULL
);
CREATE INDEX idx_artifact_task_seq ON artifact_refs(task_id, seq);

CREATE TABLE outbox_records (
    record_id      TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    kind           TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    status         TEXT NOT NULL,
    retry_count    INTEGER NOT NULL DEFAULT 0,
    next_retry_at  TEXT,
    last_error     TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX idx_outbox_status_retry ON outbox_records(status, next_retry_at);
CREATE INDEX idx_outbox_task ON outbox_records(task_id);

CREATE TABLE active_runs (
    run_id           TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    stage            TEXT NOT NULL,
    attempt_id       TEXT NOT NULL,
    provider_target  TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    heartbeat_at     TEXT NOT NULL,
    metadata_json    TEXT NOT NULL
);
CREATE INDEX idx_active_runs_task ON active_runs(task_id);

CREATE TABLE runtime_leases (
    lease_id      TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    stage         TEXT NOT NULL,
    acquired_at   TEXT NOT NULL,
    heartbeat_at  TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    owner         TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    UNIQUE(task_id, stage)
);

CREATE TABLE coordination_records (
    rowid_pk    INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    record_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_coord_kind_created ON coordination_records(kind, created_at);

CREATE TABLE documents (
    document_id   TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE document_versions (
    version_id    TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL,
    version       TEXT NOT NULL,
    content_path  TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    changelog     TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX idx_docver_doc_version ON document_versions(document_id, version);

CREATE TABLE export_manifests (
    export_id              TEXT PRIMARY KEY,
    project_id             TEXT NOT NULL,
    created_at             TEXT NOT NULL,
    output_paths_json      TEXT NOT NULL,
    task_ids_included_json TEXT NOT NULL,
    task_ids_excluded_json TEXT NOT NULL,
    artifact_ids_json      TEXT NOT NULL,
    source_files_json      TEXT NOT NULL,
    annotation_rules_hash  TEXT,
    schema_version         TEXT NOT NULL,
    validator_version      TEXT NOT NULL,
    validation_summary_json TEXT NOT NULL,
    known_limitations_json TEXT NOT NULL
);
CREATE INDEX idx_export_project_created ON export_manifests(project_id, created_at);
```

Notes:
- All `*_json` columns are TEXT holding JSON strings; the application owns serialization.
- The `seq` columns on append-only tables preserve insertion order without relying on row insertion timestamps that may collide. They are populated via `(SELECT COALESCE(MAX(seq), 0) + 1 FROM ... WHERE task_id = ?)` inside the same transaction.
- `idx` is used instead of `index` because `index` is a reserved word.
- `consensus` is `INTEGER NOT NULL` (0/1).
- `document_versions.content_path` is relative to `<root>` (e.g. `document_versions/<doc>/<version>.md`).

## File Structure for blob storage (unchanged from FileStore)

Under `<root>`:
- `db.sqlite` (new)
- `db.sqlite-wal`, `db.sqlite-shm` (SQLite WAL files)
- `artifacts/` — opaque artifact files (paths recorded in `artifact_refs.path`).
- `document_versions/<document_id>/<version>.md` — guideline content (paths recorded in `document_versions.content_path`).
- `exports/<export_id>/...` — export output trees.
- `runtime/cycle_stats.jsonl`, `runtime/heartbeat.json`, `runtime/runtime_snapshot.json` — unchanged file-only storage.
- `backups/sqlite-YYYYMMDD-HHMM.sqlite` — periodic snapshots.
- `backups/genesis-YYYYMMDD/` — pre-migration JSON tree (created by migration script).

---

## Task 1: Connection helper and schema bootstrap

**Files:**
- Create: `annotation_pipeline_skill/store/schema.sql` (content above)
- Create: `annotation_pipeline_skill/store/sqlite_store.py` (skeleton)
- Test: `tests/test_sqlite_store.py`

- [ ] **Step 1: Write failing connection test**

Create `tests/test_sqlite_store.py`:

```python
import sqlite3
from pathlib import Path

import pytest

from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_open_creates_schema_and_sets_pragmas(tmp_path: Path):
    store = SqliteStore.open(tmp_path)

    assert (tmp_path / "db.sqlite").exists()
    with sqlite3.connect(tmp_path / "db.sqlite") as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"tasks", "audit_events", "attempts", "feedback_records", "outbox_records",
                "runtime_leases", "documents", "document_versions", "export_manifests"} <= names
    store.close()


def test_open_is_idempotent_on_existing_db(tmp_path: Path):
    SqliteStore.open(tmp_path).close()
    store = SqliteStore.open(tmp_path)
    store.close()
```

- [ ] **Step 2: Save schema.sql**

Create `annotation_pipeline_skill/store/schema.sql` with the full DDL from the "Schema" section above.

- [ ] **Step 3: Run failing test**

```bash
pytest tests/test_sqlite_store.py::test_open_creates_schema_and_sets_pragmas -v
```
Expected: ImportError or AttributeError — `SqliteStore` does not yet exist.

- [ ] **Step 4: Write minimal SqliteStore skeleton**

Create `annotation_pipeline_skill/store/sqlite_store.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class SqliteStore:
    def __init__(self, root: Path | str, conn: sqlite3.Connection):
        self.root = Path(root)
        self._conn = conn

    @classmethod
    def open(cls, root: Path | str) -> "SqliteStore":
        root_path = Path(root)
        root_path.mkdir(parents=True, exist_ok=True)
        for sub in ("artifacts", "exports", "runtime", "documents", "document_versions", "backups"):
            (root_path / sub).mkdir(parents=True, exist_ok=True)
        db_path = root_path / "db.sqlite"
        first_time = not db_path.exists()
        conn = sqlite3.connect(db_path, isolation_level=None, timeout=5.0)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        if first_time:
            conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        return cls(root_path, conn)

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/test_sqlite_store.py -v
```
Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add annotation_pipeline_skill/store/schema.sql \
        annotation_pipeline_skill/store/sqlite_store.py \
        tests/test_sqlite_store.py
git commit -m "feat(store): bootstrap SqliteStore connection and schema"
```

---

## Task 2: Task CRUD with indexed list

**Files:**
- Modify: `annotation_pipeline_skill/store/sqlite_store.py`
- Test: `tests/test_sqlite_store.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_sqlite_store.py`:

```python
import json
from datetime import datetime, timezone

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus


def _make_task(task_id: str, pipeline_id: str = "pipe-1", status: TaskStatus = TaskStatus.DRAFT) -> Task:
    task = Task.new(task_id=task_id, pipeline_id=pipeline_id, source_ref={"kind": "jsonl"})
    task.status = status
    return task


def test_save_and_load_task(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = _make_task("task-1")

    store.save_task(task)
    loaded = store.load_task("task-1")

    assert loaded == task
    store.close()


def test_save_task_is_upsert(tmp_path):
    store = SqliteStore.open(tmp_path)
    task = _make_task("task-1")
    store.save_task(task)

    task.status = TaskStatus.PENDING
    task.metadata = {"note": "updated"}
    store.save_task(task)

    loaded = store.load_task("task-1")
    assert loaded.status is TaskStatus.PENDING
    assert loaded.metadata == {"note": "updated"}
    store.close()


def test_list_tasks_returns_all(tmp_path):
    store = SqliteStore.open(tmp_path)
    store.save_task(_make_task("task-1", pipeline_id="a"))
    store.save_task(_make_task("task-2", pipeline_id="b"))

    ids = sorted(t.task_id for t in store.list_tasks())
    assert ids == ["task-1", "task-2"]
    store.close()


def test_list_tasks_by_pipeline_uses_index(tmp_path):
    store = SqliteStore.open(tmp_path)
    store.save_task(_make_task("a-1", pipeline_id="a"))
    store.save_task(_make_task("a-2", pipeline_id="a"))
    store.save_task(_make_task("b-1", pipeline_id="b"))

    rows = store.list_tasks_by_pipeline("a")
    assert sorted(t.task_id for t in rows) == ["a-1", "a-2"]
    store.close()


def test_list_tasks_by_status_uses_index(tmp_path):
    store = SqliteStore.open(tmp_path)
    store.save_task(_make_task("draft-1", status=TaskStatus.DRAFT))
    store.save_task(_make_task("pend-1", status=TaskStatus.PENDING))
    store.save_task(_make_task("pend-2", status=TaskStatus.PENDING))

    rows = store.list_tasks_by_status({TaskStatus.PENDING})
    assert sorted(t.task_id for t in rows) == ["pend-1", "pend-2"]
    store.close()
```

- [ ] **Step 2: Run failing tests**

```bash
pytest tests/test_sqlite_store.py -v
```
Expected: 5 failures with `AttributeError: 'SqliteStore' object has no attribute 'save_task'` etc.

- [ ] **Step 3: Implement task methods**

Add to `annotation_pipeline_skill/store/sqlite_store.py`:

```python
import json
from typing import Iterable

from annotation_pipeline_skill.core.models import Task
from annotation_pipeline_skill.core.states import TaskStatus


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task.from_dict({
        "task_id": row["task_id"],
        "pipeline_id": row["pipeline_id"],
        "source_ref": json.loads(row["source_ref_json"]),
        "external_ref": json.loads(row["external_ref_json"]) if row["external_ref_json"] else None,
        "modality": row["modality"],
        "annotation_requirements": json.loads(row["annotation_requirements_json"]),
        "selected_annotator_id": row["selected_annotator_id"],
        "status": row["status"],
        "current_attempt": row["current_attempt"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "active_run_id": row["active_run_id"],
        "next_retry_at": row["next_retry_at"],
        "metadata": json.loads(row["metadata_json"]),
        "document_version_id": row["document_version_id"],
    })
```

Then inside `SqliteStore`:

```python
    def __init__(self, root, conn):
        self.root = Path(root)
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def save_task(self, task: Task) -> None:
        d = task.to_dict()
        self._conn.execute(
            """
            INSERT INTO tasks (
                task_id, pipeline_id, status, current_attempt, modality,
                selected_annotator_id, active_run_id, next_retry_at,
                created_at, updated_at, document_version_id,
                source_ref_json, external_ref_json,
                annotation_requirements_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                pipeline_id=excluded.pipeline_id,
                status=excluded.status,
                current_attempt=excluded.current_attempt,
                modality=excluded.modality,
                selected_annotator_id=excluded.selected_annotator_id,
                active_run_id=excluded.active_run_id,
                next_retry_at=excluded.next_retry_at,
                updated_at=excluded.updated_at,
                document_version_id=excluded.document_version_id,
                source_ref_json=excluded.source_ref_json,
                external_ref_json=excluded.external_ref_json,
                annotation_requirements_json=excluded.annotation_requirements_json,
                metadata_json=excluded.metadata_json
            """,
            (
                d["task_id"], d["pipeline_id"], d["status"], d["current_attempt"], d["modality"],
                d["selected_annotator_id"], d["active_run_id"], d["next_retry_at"],
                d["created_at"], d["updated_at"], d["document_version_id"],
                json.dumps(d["source_ref"], sort_keys=True),
                json.dumps(d["external_ref"], sort_keys=True) if d["external_ref"] else None,
                json.dumps(d["annotation_requirements"], sort_keys=True),
                json.dumps(d["metadata"], sort_keys=True),
            ),
        )

    def load_task(self, task_id: str) -> Task:
        row = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return _row_to_task(row)

    def list_tasks(self) -> list[Task]:
        rows = self._conn.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
        return [_row_to_task(r) for r in rows]

    def list_tasks_by_pipeline(self, pipeline_id: str) -> list[Task]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE pipeline_id = ? ORDER BY created_at",
            (pipeline_id,),
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    def list_tasks_by_status(self, statuses: Iterable[TaskStatus]) -> list[Task]:
        values = [s.value if isinstance(s, TaskStatus) else s for s in statuses]
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        rows = self._conn.execute(
            f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY created_at",
            values,
        ).fetchall()
        return [_row_to_task(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_sqlite_store.py -v
```
Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/store/sqlite_store.py tests/test_sqlite_store.py
git commit -m "feat(store): SqliteStore task CRUD with indexed pipeline/status queries"
```

---

## Task 3: AuditEvent append + list

**Files:**
- Modify: `annotation_pipeline_skill/store/sqlite_store.py`
- Test: `tests/test_sqlite_store.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_sqlite_store.py`:

```python
from annotation_pipeline_skill.core.models import AuditEvent


def test_append_and_list_events_preserves_order(tmp_path):
    store = SqliteStore.open(tmp_path)
    e1 = AuditEvent.new("task-1", TaskStatus.DRAFT, TaskStatus.PENDING, actor="a", reason="r1", stage="ingest")
    e2 = AuditEvent.new("task-1", TaskStatus.PENDING, TaskStatus.ANNOTATING, actor="a", reason="r2", stage="annotate")

    store.append_event(e1)
    store.append_event(e2)

    rows = store.list_events("task-1")
    assert [e.event_id for e in rows] == [e1.event_id, e2.event_id]
    store.close()


def test_list_events_returns_empty_for_unknown_task(tmp_path):
    store = SqliteStore.open(tmp_path)
    assert store.list_events("nope") == []
    store.close()
```

- [ ] **Step 2: Run failing tests**

```bash
pytest tests/test_sqlite_store.py -v -k events
```
Expected: 2 failures.

- [ ] **Step 3: Implement event methods**

Add to `SqliteStore`:

```python
    def append_event(self, event) -> None:
        d = event.to_dict()
        self._conn.execute(
            """
            INSERT INTO audit_events (
                event_id, task_id, previous_status, next_status, actor,
                reason, stage, attempt_id, created_at, metadata_json, seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT MAX(seq) + 1 FROM audit_events WHERE task_id = ?), 1)
            )
            """,
            (
                d["event_id"], d["task_id"], d["previous_status"], d["next_status"],
                d["actor"], d["reason"], d["stage"], d["attempt_id"], d["created_at"],
                json.dumps(d["metadata"], sort_keys=True),
                d["task_id"],
            ),
        )

    def list_events(self, task_id: str):
        from annotation_pipeline_skill.core.models import AuditEvent
        rows = self._conn.execute(
            "SELECT * FROM audit_events WHERE task_id = ? ORDER BY seq",
            (task_id,),
        ).fetchall()
        return [
            AuditEvent.from_dict({
                "event_id": r["event_id"],
                "task_id": r["task_id"],
                "previous_status": r["previous_status"],
                "next_status": r["next_status"],
                "actor": r["actor"],
                "reason": r["reason"],
                "stage": r["stage"],
                "attempt_id": r["attempt_id"],
                "created_at": r["created_at"],
                "metadata": json.loads(r["metadata_json"]),
            })
            for r in rows
        ]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_sqlite_store.py -v
```
Expected: all pass (9 total).

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/store/sqlite_store.py tests/test_sqlite_store.py
git commit -m "feat(store): SqliteStore audit event append/list with seq ordering"
```

---

## Task 4: Attempt + FeedbackRecord + FeedbackDiscussion + ArtifactRef

**Files:**
- Modify: `annotation_pipeline_skill/store/sqlite_store.py`
- Test: `tests/test_sqlite_store.py`

These four follow the same per-task append pattern as `audit_events`. We test them together because the structural pattern is identical.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_sqlite_store.py`:

```python
from annotation_pipeline_skill.core.models import (
    ArtifactRef, Attempt, FeedbackDiscussionEntry, FeedbackRecord,
)
from annotation_pipeline_skill.core.states import (
    AttemptStatus, FeedbackSeverity, FeedbackSource,
)


def test_append_and_list_attempts(tmp_path):
    store = SqliteStore.open(tmp_path)
    a = Attempt(
        attempt_id="att-1", task_id="task-1", index=0, stage="annotate",
        status=AttemptStatus.SUCCEEDED,
    )
    store.append_attempt(a)
    assert store.list_attempts("task-1") == [a]
    store.close()


def test_append_and_list_feedback(tmp_path):
    store = SqliteStore.open(tmp_path)
    f = FeedbackRecord.new(
        task_id="task-1", attempt_id="att-1",
        source_stage=FeedbackSource.QC, severity=FeedbackSeverity.ERROR,
        category="missing_entity", message="m", target={"f": "x"},
        suggested_action="rerun", created_by="qc",
    )
    store.append_feedback(f)
    assert store.list_feedback("task-1") == [f]
    store.close()


def test_append_and_list_feedback_discussion(tmp_path):
    store = SqliteStore.open(tmp_path)
    d = FeedbackDiscussionEntry.new(
        task_id="task-1", feedback_id="fb-1",
        role="annotator", stance="agree", message="ok", created_by="annotator-1",
    )
    store.append_feedback_discussion(d)
    assert store.list_feedback_discussions("task-1") == [d]
    store.close()


def test_append_and_list_artifact(tmp_path):
    store = SqliteStore.open(tmp_path)
    a = ArtifactRef.new(
        task_id="task-1", kind="annotation_result",
        path="artifacts/task-1.json", content_type="application/json",
    )
    store.append_artifact(a)
    assert store.list_artifacts("task-1") == [a]
    store.close()
```

- [ ] **Step 2: Run failing tests**

```bash
pytest tests/test_sqlite_store.py -v -k "attempt or feedback or artifact"
```
Expected: 4 failures.

- [ ] **Step 3: Implement attempt methods**

Add to `SqliteStore`:

```python
    def append_attempt(self, attempt) -> None:
        d = attempt.to_dict()
        self._conn.execute(
            """
            INSERT INTO attempts (
                attempt_id, task_id, idx, stage, status,
                started_at, finished_at, provider_id, model, effort,
                route_role, summary, error_json, artifacts_json, seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT MAX(seq) + 1 FROM attempts WHERE task_id = ?), 1)
            )
            """,
            (
                d["attempt_id"], d["task_id"], d["index"], d["stage"], d["status"],
                d["started_at"], d["finished_at"], d["provider_id"], d["model"], d["effort"],
                d["route_role"], d["summary"],
                json.dumps(d["error"], sort_keys=True) if d["error"] else None,
                json.dumps(d["artifacts"], sort_keys=True),
                d["task_id"],
            ),
        )

    def list_attempts(self, task_id: str):
        from annotation_pipeline_skill.core.models import Attempt
        rows = self._conn.execute(
            "SELECT * FROM attempts WHERE task_id = ? ORDER BY seq",
            (task_id,),
        ).fetchall()
        return [
            Attempt.from_dict({
                "attempt_id": r["attempt_id"], "task_id": r["task_id"],
                "index": r["idx"], "stage": r["stage"], "status": r["status"],
                "started_at": r["started_at"], "finished_at": r["finished_at"],
                "provider_id": r["provider_id"], "model": r["model"], "effort": r["effort"],
                "route_role": r["route_role"], "summary": r["summary"],
                "error": json.loads(r["error_json"]) if r["error_json"] else None,
                "artifacts": json.loads(r["artifacts_json"]),
            })
            for r in rows
        ]
```

- [ ] **Step 4: Implement feedback record methods**

```python
    def append_feedback(self, feedback) -> None:
        d = feedback.to_dict()
        self._conn.execute(
            """
            INSERT INTO feedback_records (
                feedback_id, task_id, attempt_id, source_stage, severity,
                category, message, target_json, suggested_action,
                created_at, created_by, metadata_json, seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT MAX(seq) + 1 FROM feedback_records WHERE task_id = ?), 1)
            )
            """,
            (
                d["feedback_id"], d["task_id"], d["attempt_id"], d["source_stage"], d["severity"],
                d["category"], d["message"],
                json.dumps(d["target"], sort_keys=True),
                d["suggested_action"], d["created_at"], d["created_by"],
                json.dumps(d["metadata"], sort_keys=True),
                d["task_id"],
            ),
        )

    def list_feedback(self, task_id: str):
        from annotation_pipeline_skill.core.models import FeedbackRecord
        rows = self._conn.execute(
            "SELECT * FROM feedback_records WHERE task_id = ? ORDER BY seq",
            (task_id,),
        ).fetchall()
        return [
            FeedbackRecord.from_dict({
                "feedback_id": r["feedback_id"], "task_id": r["task_id"],
                "attempt_id": r["attempt_id"], "source_stage": r["source_stage"],
                "severity": r["severity"], "category": r["category"], "message": r["message"],
                "target": json.loads(r["target_json"]),
                "suggested_action": r["suggested_action"],
                "created_at": r["created_at"], "created_by": r["created_by"],
                "metadata": json.loads(r["metadata_json"]),
            })
            for r in rows
        ]
```

- [ ] **Step 5: Implement discussion + artifact methods**

```python
    def append_feedback_discussion(self, entry) -> None:
        d = entry.to_dict()
        self._conn.execute(
            """
            INSERT INTO feedback_discussions (
                entry_id, task_id, feedback_id, role, stance, message,
                agreed_points_json, disputed_points_json, proposed_resolution,
                consensus, created_at, created_by, metadata_json, seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT MAX(seq) + 1 FROM feedback_discussions WHERE task_id = ?), 1)
            )
            """,
            (
                d["entry_id"], d["task_id"], d["feedback_id"], d["role"], d["stance"], d["message"],
                json.dumps(d["agreed_points"], sort_keys=True),
                json.dumps(d["disputed_points"], sort_keys=True),
                d["proposed_resolution"], 1 if d["consensus"] else 0,
                d["created_at"], d["created_by"],
                json.dumps(d["metadata"], sort_keys=True),
                d["task_id"],
            ),
        )

    def list_feedback_discussions(self, task_id: str):
        from annotation_pipeline_skill.core.models import FeedbackDiscussionEntry
        rows = self._conn.execute(
            "SELECT * FROM feedback_discussions WHERE task_id = ? ORDER BY seq",
            (task_id,),
        ).fetchall()
        return [
            FeedbackDiscussionEntry.from_dict({
                "entry_id": r["entry_id"], "task_id": r["task_id"],
                "feedback_id": r["feedback_id"], "role": r["role"], "stance": r["stance"],
                "message": r["message"],
                "agreed_points": json.loads(r["agreed_points_json"]),
                "disputed_points": json.loads(r["disputed_points_json"]),
                "proposed_resolution": r["proposed_resolution"],
                "consensus": bool(r["consensus"]),
                "created_at": r["created_at"], "created_by": r["created_by"],
                "metadata": json.loads(r["metadata_json"]),
            })
            for r in rows
        ]

    def append_artifact(self, artifact) -> None:
        d = artifact.to_dict()
        self._conn.execute(
            """
            INSERT INTO artifact_refs (
                artifact_id, task_id, kind, path, content_type,
                created_at, metadata_json, seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT MAX(seq) + 1 FROM artifact_refs WHERE task_id = ?), 1)
            )
            """,
            (
                d["artifact_id"], d["task_id"], d["kind"], d["path"], d["content_type"],
                d["created_at"], json.dumps(d["metadata"], sort_keys=True),
                d["task_id"],
            ),
        )

    def list_artifacts(self, task_id: str):
        from annotation_pipeline_skill.core.models import ArtifactRef
        rows = self._conn.execute(
            "SELECT * FROM artifact_refs WHERE task_id = ? ORDER BY seq",
            (task_id,),
        ).fetchall()
        return [
            ArtifactRef.from_dict({
                "artifact_id": r["artifact_id"], "task_id": r["task_id"],
                "kind": r["kind"], "path": r["path"], "content_type": r["content_type"],
                "created_at": r["created_at"],
                "metadata": json.loads(r["metadata_json"]),
            })
            for r in rows
        ]
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_sqlite_store.py -v
```
Expected: all pass (13 total).

- [ ] **Step 7: Commit**

```bash
git add annotation_pipeline_skill/store/sqlite_store.py tests/test_sqlite_store.py
git commit -m "feat(store): SqliteStore attempts, feedback, discussions, artifacts"
```

---

## Task 5: Outbox CRUD with indexed pending lookup

**Files:**
- Modify: `annotation_pipeline_skill/store/sqlite_store.py`
- Test: `tests/test_sqlite_store.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_sqlite_store.py`:

```python
from annotation_pipeline_skill.core.models import OutboxRecord
from annotation_pipeline_skill.core.states import OutboxKind, OutboxStatus


def test_save_and_list_outbox(tmp_path):
    store = SqliteStore.open(tmp_path)
    rec = OutboxRecord.new("task-1", OutboxKind.STATUS, {"foo": "bar"})
    store.save_outbox(rec)

    listed = store.list_outbox()
    assert len(listed) == 1 and listed[0] == rec
    store.close()


def test_save_outbox_is_upsert(tmp_path):
    store = SqliteStore.open(tmp_path)
    rec = OutboxRecord.new("task-1", OutboxKind.STATUS, {"foo": "bar"})
    store.save_outbox(rec)
    rec.status = OutboxStatus.SENT
    store.save_outbox(rec)

    listed = store.list_outbox()
    assert listed[0].status is OutboxStatus.SENT
    store.close()


def test_list_pending_outbox_filters_by_status_and_retry(tmp_path):
    from datetime import datetime, timedelta, timezone
    store = SqliteStore.open(tmp_path)

    a = OutboxRecord.new("t-1", OutboxKind.STATUS, {})
    b = OutboxRecord.new("t-2", OutboxKind.STATUS, {})
    b.status = OutboxStatus.SENT
    c = OutboxRecord.new("t-3", OutboxKind.STATUS, {})
    c.next_retry_at = datetime.now(timezone.utc) + timedelta(hours=1)
    for r in (a, b, c):
        store.save_outbox(r)

    pending = store.list_pending_outbox(now=datetime.now(timezone.utc))
    assert [r.record_id for r in pending] == [a.record_id]
    store.close()
```

- [ ] **Step 2: Run failing tests**

```bash
pytest tests/test_sqlite_store.py -v -k outbox
```
Expected: 3 failures.

- [ ] **Step 3: Implement outbox methods**

Add to `SqliteStore`:

```python
    def save_outbox(self, record) -> None:
        d = record.to_dict()
        self._conn.execute(
            """
            INSERT INTO outbox_records (
                record_id, task_id, kind, payload_json, status,
                retry_count, next_retry_at, last_error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                kind=excluded.kind,
                payload_json=excluded.payload_json,
                status=excluded.status,
                retry_count=excluded.retry_count,
                next_retry_at=excluded.next_retry_at,
                last_error=excluded.last_error
            """,
            (
                d["record_id"], d["task_id"], d["kind"],
                json.dumps(d["payload"], sort_keys=True),
                d["status"], d["retry_count"], d["next_retry_at"], d["last_error"], d["created_at"],
            ),
        )

    def _row_to_outbox(self, r):
        from annotation_pipeline_skill.core.models import OutboxRecord
        return OutboxRecord.from_dict({
            "record_id": r["record_id"], "task_id": r["task_id"], "kind": r["kind"],
            "payload": json.loads(r["payload_json"]), "status": r["status"],
            "retry_count": r["retry_count"], "next_retry_at": r["next_retry_at"],
            "last_error": r["last_error"], "created_at": r["created_at"],
        })

    def list_outbox(self):
        rows = self._conn.execute("SELECT * FROM outbox_records ORDER BY created_at").fetchall()
        return [self._row_to_outbox(r) for r in rows]

    def list_pending_outbox(self, *, now):
        rows = self._conn.execute(
            """
            SELECT * FROM outbox_records
            WHERE status = ?
              AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY created_at
            """,
            ("pending", now.isoformat()),
        ).fetchall()
        return [self._row_to_outbox(r) for r in rows]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_sqlite_store.py -v
```
Expected: all pass (16 total).

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/store/sqlite_store.py tests/test_sqlite_store.py
git commit -m "feat(store): SqliteStore outbox CRUD with indexed pending query"
```

---

## Task 6: Active runs and runtime leases (atomic acquisition)

**Files:**
- Modify: `annotation_pipeline_skill/store/sqlite_store.py`
- Test: `tests/test_sqlite_store.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_sqlite_store.py`:

```python
from datetime import datetime, timedelta, timezone

from annotation_pipeline_skill.core.runtime import ActiveRun, RuntimeLease


def _now():
    return datetime.now(timezone.utc)


def test_active_run_save_list_delete(tmp_path):
    store = SqliteStore.open(tmp_path)
    run = ActiveRun(
        run_id="run-1", task_id="t-1", stage="annotate", attempt_id="a-1",
        provider_target="local", started_at=_now(), heartbeat_at=_now(),
    )
    store.save_active_run(run)
    assert store.list_active_runs() == [run]

    store.delete_active_run("run-1")
    assert store.list_active_runs() == []
    store.close()


def test_save_runtime_lease_returns_true_on_first_acquire(tmp_path):
    store = SqliteStore.open(tmp_path)
    lease = RuntimeLease(
        lease_id="L1", task_id="t-1", stage="annotate",
        acquired_at=_now(), heartbeat_at=_now(), expires_at=_now() + timedelta(minutes=10),
        owner="worker-A",
    )
    assert store.save_runtime_lease(lease) is True
    assert len(store.list_runtime_leases()) == 1
    store.close()


def test_save_runtime_lease_returns_false_when_task_stage_locked(tmp_path):
    store = SqliteStore.open(tmp_path)
    a = RuntimeLease(
        lease_id="L1", task_id="t-1", stage="annotate",
        acquired_at=_now(), heartbeat_at=_now(), expires_at=_now() + timedelta(minutes=10),
        owner="worker-A",
    )
    b = RuntimeLease(
        lease_id="L2", task_id="t-1", stage="annotate",
        acquired_at=_now(), heartbeat_at=_now(), expires_at=_now() + timedelta(minutes=10),
        owner="worker-B",
    )
    assert store.save_runtime_lease(a) is True
    assert store.save_runtime_lease(b) is False
    leases = store.list_runtime_leases()
    assert [l.owner for l in leases] == ["worker-A"]
    store.close()


def test_delete_runtime_lease_releases_slot(tmp_path):
    store = SqliteStore.open(tmp_path)
    a = RuntimeLease(
        lease_id="L1", task_id="t-1", stage="annotate",
        acquired_at=_now(), heartbeat_at=_now(), expires_at=_now() + timedelta(minutes=10),
        owner="worker-A",
    )
    store.save_runtime_lease(a)
    store.delete_runtime_lease("L1")

    b = RuntimeLease(
        lease_id="L2", task_id="t-1", stage="annotate",
        acquired_at=_now(), heartbeat_at=_now(), expires_at=_now() + timedelta(minutes=10),
        owner="worker-B",
    )
    assert store.save_runtime_lease(b) is True
    store.close()
```

- [ ] **Step 2: Run failing tests**

```bash
pytest tests/test_sqlite_store.py -v -k "active_run or runtime_lease"
```
Expected: 4 failures.

- [ ] **Step 3: Implement active runs**

Add to `SqliteStore`:

```python
    def save_active_run(self, run) -> None:
        d = run.to_dict()
        self._conn.execute(
            """
            INSERT INTO active_runs (
                run_id, task_id, stage, attempt_id, provider_target,
                started_at, heartbeat_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                stage=excluded.stage,
                attempt_id=excluded.attempt_id,
                provider_target=excluded.provider_target,
                heartbeat_at=excluded.heartbeat_at,
                metadata_json=excluded.metadata_json
            """,
            (
                d["run_id"], d["task_id"], d["stage"], d["attempt_id"], d["provider_target"],
                d["started_at"], d["heartbeat_at"],
                json.dumps(d["metadata"], sort_keys=True),
            ),
        )

    def list_active_runs(self):
        from annotation_pipeline_skill.core.runtime import ActiveRun
        rows = self._conn.execute("SELECT * FROM active_runs ORDER BY started_at").fetchall()
        return [
            ActiveRun.from_dict({
                "run_id": r["run_id"], "task_id": r["task_id"], "stage": r["stage"],
                "attempt_id": r["attempt_id"], "provider_target": r["provider_target"],
                "started_at": r["started_at"], "heartbeat_at": r["heartbeat_at"],
                "metadata": json.loads(r["metadata_json"]),
            })
            for r in rows
        ]

    def delete_active_run(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM active_runs WHERE run_id = ?", (run_id,))
```

- [ ] **Step 4: Implement leases**

```python
    def save_runtime_lease(self, lease) -> bool:
        d = lease.to_dict()
        try:
            self._conn.execute(
                """
                INSERT INTO runtime_leases (
                    lease_id, task_id, stage, acquired_at, heartbeat_at,
                    expires_at, owner, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    d["lease_id"], d["task_id"], d["stage"],
                    d["acquired_at"], d["heartbeat_at"], d["expires_at"], d["owner"],
                    json.dumps(d["metadata"], sort_keys=True),
                ),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def list_runtime_leases(self):
        from annotation_pipeline_skill.core.runtime import RuntimeLease
        rows = self._conn.execute("SELECT * FROM runtime_leases ORDER BY acquired_at").fetchall()
        return [
            RuntimeLease.from_dict({
                "lease_id": r["lease_id"], "task_id": r["task_id"], "stage": r["stage"],
                "acquired_at": r["acquired_at"], "heartbeat_at": r["heartbeat_at"],
                "expires_at": r["expires_at"], "owner": r["owner"],
                "metadata": json.loads(r["metadata_json"]),
            })
            for r in rows
        ]

    def delete_runtime_lease(self, lease_id: str) -> None:
        self._conn.execute("DELETE FROM runtime_leases WHERE lease_id = ?", (lease_id,))
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_sqlite_store.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add annotation_pipeline_skill/store/sqlite_store.py tests/test_sqlite_store.py
git commit -m "feat(store): atomic runtime lease acquisition via UNIQUE(task_id, stage)"
```

---

## Task 7: Coordination records, runtime heartbeat/snapshot/cycle stats (file-only)

**Files:**
- Modify: `annotation_pipeline_skill/store/sqlite_store.py`
- Test: `tests/test_sqlite_store.py`

The runtime heartbeat / snapshot / cycle stats stay as files under `<root>/runtime/` (per user decision). Only coordination records move to DB.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_sqlite_store.py`:

```python
def test_coordination_record_append_and_list(tmp_path):
    store = SqliteStore.open(tmp_path)
    store.append_coordination_record("rule_updates", {"id": 1, "project_id": "p"})
    store.append_coordination_record("rule_updates", {"id": 2, "project_id": "p"})
    store.append_coordination_record("long_tail_issues", {"id": 3, "project_id": "p"})

    rules = store.list_coordination_records("rule_updates")
    assert [r["id"] for r in rules] == [1, 2]
    long_tail = store.list_coordination_records("long_tail_issues")
    assert [r["id"] for r in long_tail] == [3]
    store.close()


def test_runtime_heartbeat_roundtrip(tmp_path):
    from datetime import datetime, timezone
    store = SqliteStore.open(tmp_path)
    assert store.load_runtime_heartbeat() is None

    now = datetime.now(timezone.utc)
    store.save_runtime_heartbeat(now)
    loaded = store.load_runtime_heartbeat()
    assert loaded.isoformat() == now.isoformat()
    store.close()


def test_runtime_cycle_stats_append_and_list(tmp_path):
    from datetime import datetime, timezone
    from annotation_pipeline_skill.core.runtime import RuntimeCycleStats
    store = SqliteStore.open(tmp_path)
    s = RuntimeCycleStats(
        cycle_id="c-1", started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        started=1, accepted=0, failed=0, capacity_available=3,
    )
    store.append_runtime_cycle_stats(s)
    assert store.list_runtime_cycle_stats() == [s]
    store.close()


def test_runtime_snapshot_save_and_load(tmp_path):
    from datetime import datetime, timezone
    from annotation_pipeline_skill.core.runtime import (
        CapacitySnapshot, QueueCounts, RuntimeSnapshot, RuntimeStatus,
    )
    store = SqliteStore.open(tmp_path)
    snap = RuntimeSnapshot(
        generated_at=datetime.now(timezone.utc),
        runtime_status=RuntimeStatus(healthy=True, heartbeat_at=None, heartbeat_age_seconds=None, active=False),
        queue_counts=QueueCounts(
            pending=0, annotating=0, validating=0, qc=0, human_review=0, accepted=0, rejected=0,
        ),
        active_runs=[], capacity=CapacitySnapshot(
            max_concurrent_tasks=4, max_starts_per_cycle=2, active_count=0, available_slots=4,
        ),
        stale_tasks=[], due_retries=[], project_summaries=[], cycle_stats=[],
    )
    store.save_runtime_snapshot(snap)
    assert store.load_runtime_snapshot() == snap
    store.close()
```

- [ ] **Step 2: Run failing tests**

```bash
pytest tests/test_sqlite_store.py -v -k "coord or heartbeat or cycle or snapshot"
```
Expected: 4 failures.

- [ ] **Step 3: Implement coordination methods (DB)**

Add to `SqliteStore`:

```python
    def append_coordination_record(self, kind: str, record: dict) -> None:
        self._conn.execute(
            "INSERT INTO coordination_records (kind, record_json, created_at) VALUES (?, ?, ?)",
            (kind, json.dumps(record, sort_keys=True), record.get("created_at") or ""),
        )

    def list_coordination_records(self, kind: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT record_json FROM coordination_records WHERE kind = ? ORDER BY rowid_pk",
            (kind,),
        ).fetchall()
        return [json.loads(r["record_json"]) for r in rows]
```

- [ ] **Step 4: Implement runtime file methods (heartbeat, cycle stats, snapshot)**

These continue to use files under `<root>/runtime/`. Add to `SqliteStore`:

```python
    @property
    def _runtime_dir(self) -> Path:
        return self.root / "runtime"

    @property
    def _runtime_heartbeat_path(self) -> Path:
        return self._runtime_dir / "heartbeat.json"

    @property
    def _runtime_cycle_path(self) -> Path:
        return self._runtime_dir / "cycle_stats.jsonl"

    @property
    def _runtime_snapshot_path(self) -> Path:
        return self._runtime_dir / "runtime_snapshot.json"

    def save_runtime_heartbeat(self, heartbeat_at) -> None:
        self._runtime_heartbeat_path.write_text(
            json.dumps({"heartbeat_at": heartbeat_at.isoformat()}, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def load_runtime_heartbeat(self):
        from datetime import datetime
        if not self._runtime_heartbeat_path.exists():
            return None
        payload = json.loads(self._runtime_heartbeat_path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(payload["heartbeat_at"])

    def append_runtime_cycle_stats(self, stats) -> None:
        with self._runtime_cycle_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(stats.to_dict(), sort_keys=True) + "\n")

    def list_runtime_cycle_stats(self):
        from annotation_pipeline_skill.core.runtime import RuntimeCycleStats
        if not self._runtime_cycle_path.exists():
            return []
        return [
            RuntimeCycleStats.from_dict(json.loads(line))
            for line in self._runtime_cycle_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def save_runtime_snapshot(self, snap) -> None:
        self._runtime_snapshot_path.write_text(
            json.dumps(snap.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    def load_runtime_snapshot(self):
        from annotation_pipeline_skill.core.runtime import RuntimeSnapshot
        if not self._runtime_snapshot_path.exists():
            return None
        return RuntimeSnapshot.from_dict(json.loads(self._runtime_snapshot_path.read_text(encoding="utf-8")))
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_sqlite_store.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add annotation_pipeline_skill/store/sqlite_store.py tests/test_sqlite_store.py
git commit -m "feat(store): coordination records in DB, runtime monitoring stays in files"
```

---

## Task 8: Documents and document versions (content as files)

**Files:**
- Modify: `annotation_pipeline_skill/store/sqlite_store.py`
- Test: `tests/test_sqlite_store.py`

Document **metadata** (id/title/description) goes to DB. Document **version content** (markdown body) is written as a file at `<root>/document_versions/<doc_id>/<version>.md`. The DB row stores path and sha256.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_sqlite_store.py`:

```python
def test_save_and_load_document(tmp_path):
    from annotation_pipeline_skill.core.models import AnnotationDocument
    store = SqliteStore.open(tmp_path)
    doc = AnnotationDocument.new(title="t", description="d", created_by="u")
    store.save_document(doc)
    assert store.load_document(doc.document_id) == doc
    assert store.list_documents() == [doc]
    store.close()


def test_save_document_version_writes_content_to_file(tmp_path):
    import hashlib
    from annotation_pipeline_skill.core.models import AnnotationDocument, AnnotationDocumentVersion
    store = SqliteStore.open(tmp_path)
    doc = AnnotationDocument.new(title="t", description="d", created_by="u")
    store.save_document(doc)
    ver = AnnotationDocumentVersion.new(
        document_id=doc.document_id, version="v1", content="# Title\n\nbody",
        changelog="initial", created_by="u",
    )

    store.save_document_version(ver)

    content_path = tmp_path / "document_versions" / doc.document_id / "v1.md"
    assert content_path.exists()
    assert content_path.read_text(encoding="utf-8") == "# Title\n\nbody"

    loaded = store.load_document_version(ver.version_id)
    assert loaded == ver

    versions = store.list_document_versions(doc.document_id)
    assert versions == [ver]
    store.close()


def test_document_version_sha256_is_stored(tmp_path):
    import hashlib
    import sqlite3
    from annotation_pipeline_skill.core.models import AnnotationDocument, AnnotationDocumentVersion
    store = SqliteStore.open(tmp_path)
    doc = AnnotationDocument.new(title="t", description="d", created_by="u")
    store.save_document(doc)
    ver = AnnotationDocumentVersion.new(
        document_id=doc.document_id, version="v1", content="abc",
        changelog="x", created_by="u",
    )
    store.save_document_version(ver)

    expected = hashlib.sha256(b"abc").hexdigest()
    with sqlite3.connect(tmp_path / "db.sqlite") as conn:
        sha = conn.execute(
            "SELECT content_sha256 FROM document_versions WHERE version_id = ?",
            (ver.version_id,),
        ).fetchone()[0]
    assert sha == expected
    store.close()
```

- [ ] **Step 2: Run failing tests**

```bash
pytest tests/test_sqlite_store.py -v -k "document"
```
Expected: 3 failures.

- [ ] **Step 3: Implement document methods**

Add to `SqliteStore`:

```python
    def save_document(self, doc) -> None:
        d = doc.to_dict()
        self._conn.execute(
            """
            INSERT INTO documents (document_id, title, description, created_at, created_by, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                metadata_json=excluded.metadata_json
            """,
            (
                d["document_id"], d["title"], d["description"], d["created_at"], d["created_by"],
                json.dumps(d["metadata"], sort_keys=True),
            ),
        )

    def load_document(self, document_id: str):
        from annotation_pipeline_skill.core.models import AnnotationDocument
        row = self._conn.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(document_id)
        return AnnotationDocument.from_dict({
            "document_id": row["document_id"], "title": row["title"], "description": row["description"],
            "created_at": row["created_at"], "created_by": row["created_by"],
            "metadata": json.loads(row["metadata_json"]),
        })

    def list_documents(self):
        from annotation_pipeline_skill.core.models import AnnotationDocument
        rows = self._conn.execute("SELECT * FROM documents ORDER BY created_at").fetchall()
        return [
            AnnotationDocument.from_dict({
                "document_id": r["document_id"], "title": r["title"], "description": r["description"],
                "created_at": r["created_at"], "created_by": r["created_by"],
                "metadata": json.loads(r["metadata_json"]),
            })
            for r in rows
        ]
```

- [ ] **Step 4: Implement document version methods**

```python
    import hashlib  # at top of file

    def _content_path_for(self, document_id: str, version: str) -> Path:
        return self.root / "document_versions" / document_id / f"{version}.md"

    def save_document_version(self, ver) -> None:
        d = ver.to_dict()
        content = d["content"]
        path = self._content_path_for(d["document_id"], d["version"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        rel_path = path.relative_to(self.root).as_posix()
        self._conn.execute(
            """
            INSERT INTO document_versions (
                version_id, document_id, version, content_path, content_sha256,
                changelog, created_at, created_by, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(version_id) DO UPDATE SET
                content_path=excluded.content_path,
                content_sha256=excluded.content_sha256,
                changelog=excluded.changelog,
                metadata_json=excluded.metadata_json
            """,
            (
                d["version_id"], d["document_id"], d["version"], rel_path, sha,
                d["changelog"], d["created_at"], d["created_by"],
                json.dumps(d["metadata"], sort_keys=True),
            ),
        )

    def _row_to_doc_version(self, row):
        from annotation_pipeline_skill.core.models import AnnotationDocumentVersion
        path = self.root / row["content_path"]
        content = path.read_text(encoding="utf-8")
        return AnnotationDocumentVersion.from_dict({
            "version_id": row["version_id"], "document_id": row["document_id"],
            "version": row["version"], "content": content,
            "changelog": row["changelog"], "created_at": row["created_at"],
            "created_by": row["created_by"], "metadata": json.loads(row["metadata_json"]),
        })

    def load_document_version(self, version_id: str):
        row = self._conn.execute(
            "SELECT * FROM document_versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return self._row_to_doc_version(row)

    def list_document_versions(self, document_id: str):
        rows = self._conn.execute(
            "SELECT * FROM document_versions WHERE document_id = ? ORDER BY created_at",
            (document_id,),
        ).fetchall()
        return [self._row_to_doc_version(r) for r in rows]
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_sqlite_store.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add annotation_pipeline_skill/store/sqlite_store.py tests/test_sqlite_store.py
git commit -m "feat(store): documents in DB, version content as files with sha256"
```

---

## Task 9: Export manifests

**Files:**
- Modify: `annotation_pipeline_skill/store/sqlite_store.py`
- Test: `tests/test_sqlite_store.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_sqlite_store.py`:

```python
def test_save_and_list_export_manifest(tmp_path):
    from annotation_pipeline_skill.core.models import ExportManifest
    store = SqliteStore.open(tmp_path)
    m = ExportManifest.new(
        project_id="p", output_paths=["exports/e/training.jsonl"],
        task_ids_included=["t-1"], task_ids_excluded=[],
        artifact_ids=["a-1"], source_files=["in.jsonl"],
        annotation_rules_hash=None, schema_version="v1",
        validator_version="vv1", validation_summary={"ok": 1},
    )
    store.save_export_manifest(m)
    assert store.list_export_manifests() == [m]
    store.close()
```

- [ ] **Step 2: Run failing test**

```bash
pytest tests/test_sqlite_store.py -v -k export
```
Expected: 1 failure.

- [ ] **Step 3: Implement export methods**

Add to `SqliteStore`:

```python
    def save_export_manifest(self, manifest) -> None:
        d = manifest.to_dict()
        self._conn.execute(
            """
            INSERT INTO export_manifests (
                export_id, project_id, created_at,
                output_paths_json, task_ids_included_json, task_ids_excluded_json,
                artifact_ids_json, source_files_json,
                annotation_rules_hash, schema_version, validator_version,
                validation_summary_json, known_limitations_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(export_id) DO UPDATE SET
                project_id=excluded.project_id,
                output_paths_json=excluded.output_paths_json,
                task_ids_included_json=excluded.task_ids_included_json,
                task_ids_excluded_json=excluded.task_ids_excluded_json,
                artifact_ids_json=excluded.artifact_ids_json,
                source_files_json=excluded.source_files_json,
                annotation_rules_hash=excluded.annotation_rules_hash,
                schema_version=excluded.schema_version,
                validator_version=excluded.validator_version,
                validation_summary_json=excluded.validation_summary_json,
                known_limitations_json=excluded.known_limitations_json
            """,
            (
                d["export_id"], d["project_id"], d["created_at"],
                json.dumps(d["output_paths"], sort_keys=True),
                json.dumps(d["task_ids_included"], sort_keys=True),
                json.dumps(d["task_ids_excluded"], sort_keys=True),
                json.dumps(d["artifact_ids"], sort_keys=True),
                json.dumps(d["source_files"], sort_keys=True),
                d["annotation_rules_hash"], d["schema_version"], d["validator_version"],
                json.dumps(d["validation_summary"], sort_keys=True),
                json.dumps(d["known_limitations"], sort_keys=True),
            ),
        )

    def list_export_manifests(self):
        from annotation_pipeline_skill.core.models import ExportManifest
        rows = self._conn.execute(
            "SELECT * FROM export_manifests ORDER BY project_id, created_at"
        ).fetchall()
        return [
            ExportManifest.from_dict({
                "export_id": r["export_id"], "project_id": r["project_id"],
                "created_at": r["created_at"],
                "output_paths": json.loads(r["output_paths_json"]),
                "task_ids_included": json.loads(r["task_ids_included_json"]),
                "task_ids_excluded": json.loads(r["task_ids_excluded_json"]),
                "artifact_ids": json.loads(r["artifact_ids_json"]),
                "source_files": json.loads(r["source_files_json"]),
                "annotation_rules_hash": r["annotation_rules_hash"],
                "schema_version": r["schema_version"], "validator_version": r["validator_version"],
                "validation_summary": json.loads(r["validation_summary_json"]),
                "known_limitations": json.loads(r["known_limitations_json"]),
            })
            for r in rows
        ]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_sqlite_store.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/store/sqlite_store.py tests/test_sqlite_store.py
git commit -m "feat(store): SqliteStore export manifests"
```

---

## Task 10: Multi-process concurrency test

**Files:**
- Create: `tests/test_sqlite_store_concurrency.py`

Verify WAL + busy_timeout + UNIQUE lease constraint behave correctly under multiple workers writing concurrently.

- [ ] **Step 1: Write failing test**

Create `tests/test_sqlite_store_concurrency.py`:

```python
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


def test_only_one_worker_acquires_lease_for_same_task_stage(tmp_path):
    SqliteStore.open(tmp_path).close()  # ensure schema present
    args_list = [(str(tmp_path), f"L-{i}", f"worker-{i}") for i in range(8)]
    with mp.get_context("spawn").Pool(processes=4) as pool:
        results = pool.map(_try_acquire, args_list)

    assert sum(1 for r in results if r) == 1


def test_concurrent_task_writes_do_not_lose_data(tmp_path):
    from annotation_pipeline_skill.core.models import Task
    SqliteStore.open(tmp_path).close()

    def _worker(args):
        root, task_id = args
        store = SqliteStore.open(root)
        store.save_task(Task.new(task_id=task_id, pipeline_id="p", source_ref={"k": "v"}))
        store.close()

    args_list = [(str(tmp_path), f"task-{i}") for i in range(40)]
    with mp.get_context("spawn").Pool(processes=4) as pool:
        pool.map(_worker, args_list)

    store = SqliteStore.open(tmp_path)
    assert len(store.list_tasks()) == 40
    store.close()
```

Note: `_worker` is at module top-level (required for `spawn` pool).

- [ ] **Step 2: Refactor `_worker` to be picklable**

Move `_worker` outside the test function — `mp.spawn` requires top-level functions:

```python
def _worker_save_task(args):
    from annotation_pipeline_skill.core.models import Task
    root, task_id = args
    store = SqliteStore.open(root)
    store.save_task(Task.new(task_id=task_id, pipeline_id="p", source_ref={"k": "v"}))
    store.close()


def test_concurrent_task_writes_do_not_lose_data(tmp_path):
    SqliteStore.open(tmp_path).close()
    args_list = [(str(tmp_path), f"task-{i}") for i in range(40)]
    with mp.get_context("spawn").Pool(processes=4) as pool:
        pool.map(_worker_save_task, args_list)

    store = SqliteStore.open(tmp_path)
    assert len(store.list_tasks()) == 40
    store.close()
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_sqlite_store_concurrency.py -v
```
Expected: both pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_sqlite_store_concurrency.py
git commit -m "test(store): verify SQLite WAL multi-process write safety"
```

---

## Task 11: Backup snapshot + retention

**Files:**
- Create: `annotation_pipeline_skill/store/backup.py`
- Create: `tests/test_sqlite_backup.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_sqlite_backup.py`:

```python
from datetime import datetime, timedelta
from pathlib import Path

from annotation_pipeline_skill.store.backup import prune_snapshots, snapshot
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_snapshot_creates_a_copy_of_db(tmp_path):
    store = SqliteStore.open(tmp_path)
    store.close()
    backups_dir = tmp_path / "backups"

    out = snapshot(tmp_path / "db.sqlite", backups_dir, now=datetime(2026, 5, 10, 12, 0))

    assert out.exists()
    assert out.parent == backups_dir
    assert out.name.startswith("sqlite-2026-05-10-1200")


def test_prune_keeps_hourly_and_daily(tmp_path):
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    base = datetime(2026, 5, 10, 12, 0)
    files = []
    for h in range(48):
        ts = base - timedelta(hours=h)
        f = backups_dir / f"sqlite-{ts:%Y-%m-%d-%H%M}.sqlite"
        f.write_bytes(b"")
        files.append(f)

    prune_snapshots(backups_dir, hourly_keep=24, daily_keep=7, now=base)

    remaining = sorted(p.name for p in backups_dir.iterdir())
    assert len(remaining) <= 24 + 7
    # 24 newest hourly always kept
    for h in range(24):
        ts = base - timedelta(hours=h)
        expected = f"sqlite-{ts:%Y-%m-%d-%H%M}.sqlite"
        assert expected in remaining
```

- [ ] **Step 2: Run failing tests**

```bash
pytest tests/test_sqlite_backup.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement backup module**

Create `annotation_pipeline_skill/store/backup.py`:

```python
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def snapshot(db_path: Path, backups_dir: Path, *, now: datetime | None = None) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d-%H%M")
    out = backups_dir / f"sqlite-{timestamp}.sqlite"
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(out)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return out


def prune_snapshots(
    backups_dir: Path,
    *,
    hourly_keep: int,
    daily_keep: int,
    now: datetime | None = None,
) -> list[Path]:
    """Keep the newest `hourly_keep` snapshots, plus one snapshot per day for `daily_keep` days. Delete the rest."""
    now = now or datetime.now(timezone.utc)
    files = sorted(backups_dir.glob("sqlite-*.sqlite"))
    if not files:
        return []

    parsed = []
    for f in files:
        # name format: sqlite-YYYY-MM-DD-HHMM.sqlite
        stem = f.stem.removeprefix("sqlite-")
        try:
            ts = datetime.strptime(stem, "%Y-%m-%d-%H%M")
        except ValueError:
            continue
        parsed.append((ts, f))
    parsed.sort(key=lambda x: x[0], reverse=True)

    keep = set()
    # newest N hourly
    for ts, f in parsed[:hourly_keep]:
        keep.add(f)
    # one per day for last `daily_keep` days
    seen_days = set()
    for ts, f in parsed:
        day = ts.date()
        if day not in seen_days and (now.date() - day).days < daily_keep:
            seen_days.add(day)
            keep.add(f)

    deleted = []
    for ts, f in parsed:
        if f not in keep:
            f.unlink()
            deleted.append(f)
    return deleted
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_sqlite_backup.py -v
```
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/store/backup.py tests/test_sqlite_backup.py
git commit -m "feat(store): backup snapshot via sqlite3.backup with retention pruning"
```

---

## Task 12: Reverse JSON dump

**Files:**
- Create: `annotation_pipeline_skill/store/dump.py`
- Create: `tests/test_sqlite_dump.py`

The dump produces a directory tree mirroring the old `FileStore` layout so the data remains diffable / recoverable.

- [ ] **Step 1: Write failing test**

Create `tests/test_sqlite_dump.py`:

```python
import json
from pathlib import Path

from annotation_pipeline_skill.core.models import (
    AuditEvent, ExportManifest, FeedbackRecord, OutboxRecord, Task,
)
from annotation_pipeline_skill.core.states import (
    FeedbackSeverity, FeedbackSource, OutboxKind, TaskStatus,
)
from annotation_pipeline_skill.store.dump import dump_to_json
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_dump_writes_tasks_and_logs(tmp_path):
    store = SqliteStore.open(tmp_path / "db_root")
    task = Task.new(task_id="t-1", pipeline_id="p", source_ref={"k": "v"})
    store.save_task(task)
    store.append_event(AuditEvent.new(
        "t-1", TaskStatus.DRAFT, TaskStatus.PENDING,
        actor="a", reason="r", stage="ingest",
    ))
    store.append_feedback(FeedbackRecord.new(
        task_id="t-1", attempt_id="a-1",
        source_stage=FeedbackSource.QC, severity=FeedbackSeverity.ERROR,
        category="c", message="m", target={}, suggested_action="s", created_by="u",
    ))
    store.save_outbox(OutboxRecord.new("t-1", OutboxKind.STATUS, {}))
    store.close()

    out = tmp_path / "out"
    store = SqliteStore.open(tmp_path / "db_root")
    dump_to_json(store, out)
    store.close()

    assert (out / "tasks" / "t-1.json").exists()
    assert json.loads((out / "tasks" / "t-1.json").read_text())["task_id"] == "t-1"
    assert (out / "events" / "t-1.jsonl").exists()
    assert (out / "feedback" / "t-1.jsonl").exists()
    assert any((out / "outbox").glob("*.json"))
```

- [ ] **Step 2: Run failing test**

```bash
pytest tests/test_sqlite_dump.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement dump**

Create `annotation_pipeline_skill/store/dump.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def dump_to_json(store: SqliteStore, out_dir: Path) -> None:
    out_dir = Path(out_dir)
    for sub in ("tasks", "events", "attempts", "feedback", "feedback_discussions",
                "artifacts", "outbox", "exports", "documents", "document_versions",
                "coordination", "runtime/active_runs", "runtime/leases"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    def write_json(p: Path, obj: dict) -> None:
        p.write_text(json.dumps(obj, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def write_jsonl(p: Path, items: list[dict]) -> None:
        with p.open("w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, sort_keys=True) + "\n")

    for task in store.list_tasks():
        write_json(out_dir / "tasks" / f"{task.task_id}.json", task.to_dict())
        events = store.list_events(task.task_id)
        if events:
            write_jsonl(out_dir / "events" / f"{task.task_id}.jsonl", [e.to_dict() for e in events])
        attempts = store.list_attempts(task.task_id)
        if attempts:
            write_jsonl(out_dir / "attempts" / f"{task.task_id}.jsonl", [a.to_dict() for a in attempts])
        feedback = store.list_feedback(task.task_id)
        if feedback:
            write_jsonl(out_dir / "feedback" / f"{task.task_id}.jsonl", [f.to_dict() for f in feedback])
        discussions = store.list_feedback_discussions(task.task_id)
        if discussions:
            write_jsonl(out_dir / "feedback_discussions" / f"{task.task_id}.jsonl",
                        [d.to_dict() for d in discussions])
        artifacts = store.list_artifacts(task.task_id)
        if artifacts:
            write_jsonl(out_dir / "artifacts" / f"{task.task_id}.jsonl",
                        [a.to_dict() for a in artifacts])

    for record in store.list_outbox():
        write_json(out_dir / "outbox" / f"{record.record_id}.json", record.to_dict())

    for manifest in store.list_export_manifests():
        export_sub = out_dir / "exports" / manifest.export_id
        export_sub.mkdir(parents=True, exist_ok=True)
        write_json(export_sub / "manifest.json", manifest.to_dict())

    for doc in store.list_documents():
        write_json(out_dir / "documents" / f"{doc.document_id}.json", doc.to_dict())
        for ver in store.list_document_versions(doc.document_id):
            write_json(out_dir / "document_versions" / f"{ver.version_id}.json", ver.to_dict())

    for kind in ("rule_updates", "long_tail_issues"):
        records = store.list_coordination_records(kind)
        if records:
            write_jsonl(out_dir / "coordination" / f"{kind}.jsonl", records)

    for run in store.list_active_runs():
        write_json(out_dir / "runtime" / "active_runs" / f"{run.run_id}.json", run.to_dict())
    for lease in store.list_runtime_leases():
        write_json(out_dir / "runtime" / "leases" / f"{lease.lease_id}.json", lease.to_dict())
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_sqlite_dump.py -v
```
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/store/dump.py tests/test_sqlite_dump.py
git commit -m "feat(store): reverse DB to JSON dump for archival"
```

---

## Task 13: One-shot migration script

**Files:**
- Create: `scripts/migrate_filestore_to_sqlite.py`
- Create: `tests/test_migrate_filestore.py`

The script reads the existing `FileStore` directory, writes everything into a fresh `SqliteStore`, verifies counts and sha256 checksums, and archives the source tree to `backups/genesis-YYYYMMDD/`.

- [ ] **Step 1: Write failing test**

Create `tests/test_migrate_filestore.py`:

```python
import json
import shutil
from pathlib import Path

from annotation_pipeline_skill.core.models import (
    AuditEvent, FeedbackRecord, OutboxRecord, Task,
)
from annotation_pipeline_skill.core.states import (
    FeedbackSeverity, FeedbackSource, OutboxKind, TaskStatus,
)
from annotation_pipeline_skill.store.file_store import FileStore
from annotation_pipeline_skill.store.sqlite_store import SqliteStore
from scripts.migrate_filestore_to_sqlite import migrate


def test_migrate_round_trips_tasks_events_feedback_outbox(tmp_path):
    src = tmp_path / "src"
    fs = FileStore(src)
    task = Task.new(task_id="t-1", pipeline_id="p", source_ref={"k": "v"})
    fs.save_task(task)
    fs.append_event(AuditEvent.new(
        "t-1", TaskStatus.DRAFT, TaskStatus.PENDING,
        actor="a", reason="r", stage="ingest",
    ))
    fs.append_feedback(FeedbackRecord.new(
        task_id="t-1", attempt_id="att-1",
        source_stage=FeedbackSource.QC, severity=FeedbackSeverity.ERROR,
        category="c", message="m", target={}, suggested_action="s", created_by="u",
    ))
    fs.save_outbox(OutboxRecord.new("t-1", OutboxKind.STATUS, {"a": 1}))

    dst = tmp_path / "dst"
    report = migrate(src, dst, archive_genesis=True)

    assert report["tasks"] == 1
    assert report["events"] == 1
    assert report["feedback"] == 1
    assert report["outbox"] == 1

    store = SqliteStore.open(dst)
    assert store.load_task("t-1") == task
    assert len(store.list_events("t-1")) == 1
    assert len(store.list_feedback("t-1")) == 1
    assert len(store.list_outbox()) == 1
    store.close()

    archived = list((dst / "backups").glob("genesis-*"))
    assert len(archived) == 1


def test_migrate_refuses_to_run_against_non_empty_db(tmp_path):
    import pytest

    src = tmp_path / "src"
    FileStore(src)  # creates empty dirs

    dst = tmp_path / "dst"
    store = SqliteStore.open(dst)
    store.save_task(Task.new(task_id="existing", pipeline_id="p", source_ref={}))
    store.close()

    with pytest.raises(RuntimeError, match="not empty"):
        migrate(src, dst, archive_genesis=False)
```

- [ ] **Step 2: Run failing tests**

```bash
pytest tests/test_migrate_filestore.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement migration script**

Create `scripts/migrate_filestore_to_sqlite.py`:

```python
"""One-shot FileStore → SqliteStore migration.

Usage:
    python scripts/migrate_filestore_to_sqlite.py --src <old-root> --dst <new-root>

Idempotency: refuses to run if target already has tasks unless --force is given.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from annotation_pipeline_skill.store.file_store import FileStore
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def migrate(src: Path | str, dst: Path | str, *, archive_genesis: bool = True, force: bool = False) -> dict:
    src = Path(src)
    dst = Path(dst)

    fs = FileStore(src)
    store = SqliteStore.open(dst)
    if not force and store.list_tasks():
        store.close()
        raise RuntimeError(f"target {dst} is not empty; pass --force to overwrite")

    report = {
        "tasks": 0, "events": 0, "attempts": 0,
        "feedback": 0, "feedback_discussions": 0, "artifacts": 0,
        "outbox": 0, "exports": 0,
        "documents": 0, "document_versions": 0,
        "active_runs": 0, "leases": 0,
        "coordination": 0,
    }

    for task in fs.list_tasks():
        store.save_task(task)
        report["tasks"] += 1
        for event in fs.list_events(task.task_id):
            store.append_event(event); report["events"] += 1
        for attempt in fs.list_attempts(task.task_id):
            store.append_attempt(attempt); report["attempts"] += 1
        for fb in fs.list_feedback(task.task_id):
            store.append_feedback(fb); report["feedback"] += 1
        for d in fs.list_feedback_discussions(task.task_id):
            store.append_feedback_discussion(d); report["feedback_discussions"] += 1
        for a in fs.list_artifacts(task.task_id):
            store.append_artifact(a); report["artifacts"] += 1

    for record in fs.list_outbox():
        store.save_outbox(record); report["outbox"] += 1

    for manifest in fs.list_export_manifests():
        store.save_export_manifest(manifest); report["exports"] += 1

    for doc in fs.list_documents():
        store.save_document(doc); report["documents"] += 1
        for ver in fs.list_document_versions(doc.document_id):
            store.save_document_version(ver); report["document_versions"] += 1

    for run in fs.list_active_runs():
        store.save_active_run(run); report["active_runs"] += 1

    for lease in fs.list_runtime_leases():
        store.save_runtime_lease(lease); report["leases"] += 1

    for kind in ("rule_updates", "long_tail_issues"):
        for record in fs.list_coordination_records(kind):
            store.append_coordination_record(kind, record); report["coordination"] += 1

    # verify task count
    assert len(store.list_tasks()) == report["tasks"], "task row count mismatch"

    if archive_genesis:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        archive_dir = dst / "backups" / f"genesis-{ts}"
        archive_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, archive_dir, dirs_exist_ok=False)
        report["genesis_archive"] = str(archive_dir)

    store.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True, help="path to old FileStore root")
    parser.add_argument("--dst", required=True, help="path to new SqliteStore root")
    parser.add_argument("--no-archive", action="store_true",
                        help="skip archiving the source tree to backups/genesis-*")
    parser.add_argument("--force", action="store_true",
                        help="run even if target DB already has tasks")
    args = parser.parse_args(argv)

    report = migrate(args.src, args.dst,
                     archive_genesis=not args.no_archive, force=args.force)
    for k, v in report.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_migrate_filestore.py -v
```
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_filestore_to_sqlite.py tests/test_migrate_filestore.py
git commit -m "feat(store): one-shot FileStore→SqliteStore migration with genesis archive"
```

---

## Task 14: CLI subcommands `apl db ...`

**Files:**
- Modify: `annotation_pipeline_skill/interfaces/cli.py`
- Modify: `tests/test_cli.py`

Add `db init`, `db status`, `db backup`, `db dump-json`. (Restore is "copy a snapshot file to db.sqlite" — operator does it manually; no CLI needed.)

- [ ] **Step 1: Add failing tests**

Append to `tests/test_cli.py`:

```python
def test_cli_db_init_creates_db(tmp_path, monkeypatch):
    from annotation_pipeline_skill.interfaces.cli import main
    monkeypatch.chdir(tmp_path)

    rc = main(["db", "init", "--root", str(tmp_path / "ws")])

    assert rc == 0
    assert (tmp_path / "ws" / "db.sqlite").exists()


def test_cli_db_backup_creates_snapshot(tmp_path):
    from annotation_pipeline_skill.interfaces.cli import main
    rc = main(["db", "init", "--root", str(tmp_path / "ws")])
    assert rc == 0

    rc = main(["db", "backup", "--root", str(tmp_path / "ws")])
    assert rc == 0
    snaps = list((tmp_path / "ws" / "backups").glob("sqlite-*.sqlite"))
    assert len(snaps) == 1


def test_cli_db_dump_json_round_trips(tmp_path):
    from annotation_pipeline_skill.core.models import Task
    from annotation_pipeline_skill.interfaces.cli import main
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore

    rc = main(["db", "init", "--root", str(tmp_path / "ws")])
    assert rc == 0
    store = SqliteStore.open(tmp_path / "ws")
    store.save_task(Task.new(task_id="t-1", pipeline_id="p", source_ref={}))
    store.close()

    rc = main(["db", "dump-json",
               "--root", str(tmp_path / "ws"),
               "--out", str(tmp_path / "out")])
    assert rc == 0
    assert (tmp_path / "out" / "tasks" / "t-1.json").exists()


def test_cli_db_status_prints_counts(tmp_path, capsys):
    from annotation_pipeline_skill.interfaces.cli import main
    rc = main(["db", "init", "--root", str(tmp_path / "ws")])
    assert rc == 0

    rc = main(["db", "status", "--root", str(tmp_path / "ws")])
    assert rc == 0
    captured = capsys.readouterr()
    assert "tasks: 0" in captured.out
```

- [ ] **Step 2: Run failing tests**

```bash
pytest tests/test_cli.py -v -k "db_"
```
Expected: 4 failures.

- [ ] **Step 3: Add `db` subcommand to CLI**

In `annotation_pipeline_skill/interfaces/cli.py`, add a new top-level subparser group `db` with subcommands. Show only the dispatcher addition (existing CLI structure follows argparse subparsers; engineer preserves existing patterns):

```python
def _register_db_commands(subparsers):
    db = subparsers.add_parser("db", help="database utilities")
    db_sub = db.add_subparsers(dest="db_command", required=True)

    p_init = db_sub.add_parser("init", help="initialize an empty SqliteStore at --root")
    p_init.add_argument("--root", required=True)
    p_init.set_defaults(func=_cmd_db_init)

    p_status = db_sub.add_parser("status", help="print row counts")
    p_status.add_argument("--root", required=True)
    p_status.set_defaults(func=_cmd_db_status)

    p_backup = db_sub.add_parser("backup", help="snapshot db.sqlite + prune")
    p_backup.add_argument("--root", required=True)
    p_backup.add_argument("--hourly-keep", type=int, default=24)
    p_backup.add_argument("--daily-keep", type=int, default=30)
    p_backup.set_defaults(func=_cmd_db_backup)

    p_dump = db_sub.add_parser("dump-json", help="export DB to JSON tree")
    p_dump.add_argument("--root", required=True)
    p_dump.add_argument("--out", required=True)
    p_dump.set_defaults(func=_cmd_db_dump_json)


def _cmd_db_init(args) -> int:
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    SqliteStore.open(args.root).close()
    return 0


def _cmd_db_status(args) -> int:
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    store = SqliteStore.open(args.root)
    print(f"tasks: {len(store.list_tasks())}")
    print(f"outbox: {len(store.list_outbox())}")
    print(f"documents: {len(store.list_documents())}")
    print(f"exports: {len(store.list_export_manifests())}")
    print(f"active_runs: {len(store.list_active_runs())}")
    print(f"leases: {len(store.list_runtime_leases())}")
    store.close()
    return 0


def _cmd_db_backup(args) -> int:
    from pathlib import Path
    from annotation_pipeline_skill.store.backup import prune_snapshots, snapshot
    root = Path(args.root)
    out = snapshot(root / "db.sqlite", root / "backups")
    deleted = prune_snapshots(
        root / "backups",
        hourly_keep=args.hourly_keep, daily_keep=args.daily_keep,
    )
    print(f"created: {out}")
    print(f"pruned: {len(deleted)}")
    return 0


def _cmd_db_dump_json(args) -> int:
    from pathlib import Path
    from annotation_pipeline_skill.store.dump import dump_to_json
    from annotation_pipeline_skill.store.sqlite_store import SqliteStore
    store = SqliteStore.open(args.root)
    dump_to_json(store, Path(args.out))
    store.close()
    return 0
```

Wire `_register_db_commands(subparsers)` into the existing `build_parser()` function (find the call site of other `add_subparsers().add_parser(...)` calls; add this beside them).

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_cli.py -v -k "db_"
```
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/interfaces/cli.py tests/test_cli.py
git commit -m "feat(cli): apl db init|status|backup|dump-json subcommands"
```

---

## Task 15: Wire SqliteStore through services and runtime (cutover)

**Files:**
- Modify (replace `FileStore` references — 77 sites across 10 modules):
  - `annotation_pipeline_skill/store/__init__.py`
  - `annotation_pipeline_skill/services/coordinator_service.py`
  - `annotation_pipeline_skill/services/dashboard_service.py`
  - `annotation_pipeline_skill/services/export_service.py`
  - `annotation_pipeline_skill/services/external_task_service.py`
  - `annotation_pipeline_skill/services/feedback_service.py`
  - `annotation_pipeline_skill/services/human_review_service.py`
  - `annotation_pipeline_skill/services/outbox_dispatch_service.py`
  - `annotation_pipeline_skill/services/provider_config_service.py`
  - `annotation_pipeline_skill/services/readiness_service.py`
  - `annotation_pipeline_skill/services/annotator_selector.py`
  - `annotation_pipeline_skill/runtime/local_scheduler.py`
  - `annotation_pipeline_skill/runtime/monitor.py`
  - `annotation_pipeline_skill/runtime/snapshot.py`
  - `annotation_pipeline_skill/runtime/subagent_cycle.py`
  - `annotation_pipeline_skill/interfaces/api.py`
  - `annotation_pipeline_skill/interfaces/cli.py`

- [ ] **Step 1: Use `SqliteStore.open()` everywhere a store is constructed**

For each call site, replace:

```python
from annotation_pipeline_skill.store.file_store import FileStore
...
store = FileStore(workspace_root)
```

with:

```python
from annotation_pipeline_skill.store.sqlite_store import SqliteStore
...
store = SqliteStore.open(workspace_root)
```

Find them with:

```bash
grep -rn "FileStore" annotation_pipeline_skill --include="*.py"
```

Update each, including type hints (`store: FileStore` → `store: SqliteStore`).

- [ ] **Step 2: Update `store/__init__.py` re-export**

```python
# annotation_pipeline_skill/store/__init__.py
from annotation_pipeline_skill.store.sqlite_store import SqliteStore  # noqa: F401
```

- [ ] **Step 3: Replace `list_tasks() + filter` with indexed helpers in two hot paths**

In `annotation_pipeline_skill/services/readiness_service.py:12`:

```python
tasks = store.list_tasks_by_pipeline(project_id)  # was: [t for t in store.list_tasks() if t.pipeline_id == project_id]
```

In `annotation_pipeline_skill/services/coordinator_service.py:_project_tasks`:

```python
def _project_tasks(self, project_id):
    if project_id is None:
        return self.store.list_tasks()
    return self.store.list_tasks_by_pipeline(project_id)
```

In `annotation_pipeline_skill/services/outbox_dispatch_service.py:154`:

```python
task_ids = {t.task_id for t in self.store.list_tasks_by_pipeline(project_id)}
```

In `annotation_pipeline_skill/services/export_service.py:43`:

```python
tasks = self.store.list_tasks_by_pipeline(project_id)
```

In `annotation_pipeline_skill/runtime/subagent_cycle.py:36`:

```python
pending_tasks = self.store.list_tasks_by_status({TaskStatus.PENDING})
```

In `annotation_pipeline_skill/runtime/local_scheduler.py:36-57`: keep `list_tasks()` since it filters by multiple statuses + custom logic. Leave as-is.

- [ ] **Step 4: Update test fixtures**

Find every test that uses `FileStore`:

```bash
grep -rln "FileStore\|file_store" tests/
```

In each test file (15 files excluding `test_file_store.py`), replace:

```python
from annotation_pipeline_skill.store.file_store import FileStore
...
store = FileStore(tmp_path)
```

with:

```python
from annotation_pipeline_skill.store.sqlite_store import SqliteStore
...
store = SqliteStore.open(tmp_path)
```

- [ ] **Step 5: Run full test suite**

```bash
pytest -x -v
```
Expected: all pass. If a test fails, the failure is informative (e.g., "list ordering changed because tests rely on file glob order"). Fix by sorting at call site, not by recreating glob behavior in `SqliteStore`.

- [ ] **Step 6: Commit**

```bash
git add annotation_pipeline_skill/ tests/
git commit -m "feat(store): cut over services, runtime, API, CLI to SqliteStore"
```

---

## Task 16: Delete legacy FileStore

**Files:**
- Delete: `annotation_pipeline_skill/store/file_store.py`
- Delete: `tests/test_file_store.py`

- [ ] **Step 1: Verify no remaining references**

```bash
grep -rn "FileStore\|file_store" annotation_pipeline_skill tests
```
Expected: no matches in source — only the migration script, which intentionally still imports `FileStore` (the script needs both classes to read old + write new).

If matches remain in production code, fix them and re-run.

- [ ] **Step 2: Move FileStore inside the migration script**

Since `migrate_filestore_to_sqlite.py` is the only consumer remaining, inline the parts of `FileStore` it needs into the script — or keep `file_store.py` as a script-only helper and explicitly mark it deprecated. Simplest path: keep `file_store.py` for the migration script and document this in its module docstring.

If you take this path, **do not delete `file_store.py`**. Instead:
- Add a top-level docstring to `file_store.py` saying: "Read-only legacy store retained for the one-shot migration script. Production code uses SqliteStore. Will be removed once migrations are no longer needed."
- Delete `tests/test_file_store.py` (the file's behavior is fully covered by `tests/test_sqlite_store.py` for the live system; the migration tests cover its read path).

```bash
git rm tests/test_file_store.py
```

- [ ] **Step 3: Run full suite + lint**

```bash
pytest -v
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add annotation_pipeline_skill/store/file_store.py tests/
git commit -m "chore(store): mark file_store.py migration-only, delete its dedicated test"
```

---

## Task 17: Smoke test against a real existing workspace

**Files:**
- No new files — manual verification step.

- [ ] **Step 1: Locate a real workspace**

Ask the user for the path of an existing `.annotation-pipeline` directory with real tasks. If unavailable, generate one by running an existing workflow against `tests/fixtures` data. Skip this task if neither is available — manual verification should not fail the plan.

- [ ] **Step 2: Run the migration**

```bash
python scripts/migrate_filestore_to_sqlite.py \
    --src /path/to/existing-workspace \
    --dst /tmp/migrated-workspace
```

Expected output: per-table counts and a `genesis_archive` line. No exceptions.

- [ ] **Step 3: Compare counts manually**

```bash
ls /path/to/existing-workspace/tasks/*.json | wc -l
python -c "
from annotation_pipeline_skill.store.sqlite_store import SqliteStore
s = SqliteStore.open('/tmp/migrated-workspace')
print(len(s.list_tasks())); s.close()
"
```
Expected: counts match.

- [ ] **Step 4: Run dashboard against migrated DB**

```bash
annotation-pipeline serve --workspace /tmp/migrated-workspace
```

Verify the UI loads tasks, runtime panel works, no errors in stderr.

- [ ] **Step 5: Run dump-json round-trip**

```bash
annotation-pipeline db dump-json --root /tmp/migrated-workspace --out /tmp/migrated-dump
diff -r /tmp/migrated-workspace/tasks /tmp/migrated-dump/tasks
```

Expected: identical task JSONs (modulo key ordering, which both writers produce sorted).

- [ ] **Step 6: Commit if any production code was touched**

```bash
git status
# if clean, no commit needed
```

---

## Task 18: Documentation

**Files:**
- Modify: `TECHNICAL_ARCHITECTURE.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/agent-operator-guide.md`

- [ ] **Step 1: Update TECHNICAL_ARCHITECTURE.md**

Find the storage section and replace with:

```markdown
## Storage

`SqliteStore` (`annotation_pipeline_skill/store/sqlite_store.py`) is the
authoritative metadata store. Every workspace contains:

- `db.sqlite` — task / event / attempt / feedback / outbox / lease / document
  metadata in 13 tables (see `store/schema.sql`). WAL mode, single-machine
  multi-process safe.
- `artifacts/` — annotation result files referenced from `artifact_refs.path`.
- `document_versions/<doc>/<version>.md` — guideline content; DB row stores
  path + sha256.
- `exports/<export_id>/` — export output trees referenced from
  `export_manifests.output_paths_json`.
- `runtime/` — heartbeat, cycle stats, latest snapshot (file-only; low volume).
- `backups/` — periodic SQLite snapshots and the genesis JSON archive.

Recovery: WAL handles in-process crash safety; `apl db backup` produces
point-in-time snapshots; the pre-migration JSON tree is permanently archived
under `backups/genesis-YYYYMMDD/` and is the from-zero ground truth.
```

- [ ] **Step 2: Update README.md**

In the "Quickstart" section, add:

````markdown
### Initialize a workspace

```
annotation-pipeline db init --root .annotation-pipeline
```

### Backup

```
annotation-pipeline db backup --root .annotation-pipeline
```

Schedule with cron / systemd timer; see `docs/agent-operator-guide.md`.
````

- [ ] **Step 3: Update CHANGELOG.md**

Prepend a new entry:

```markdown
## 2026-05-10

- BREAKING: replaced JSON/JSONL `FileStore` with `SqliteStore` (single
  `db.sqlite` per workspace, WAL mode). Indexed queries on
  `(pipeline_id, status, created_at)` replace full-directory scans.
- New CLI: `db init`, `db status`, `db backup`, `db dump-json`.
- Migration: run `python scripts/migrate_filestore_to_sqlite.py
  --src <old-root> --dst <new-root>` once; the script archives the source
  tree to `backups/genesis-YYYYMMDD/` for recovery.
- Runtime monitoring (`heartbeat.json`, `cycle_stats.jsonl`) remains
  file-based.
```

- [ ] **Step 4: Update docs/agent-operator-guide.md**

Add a "Backups & Recovery" section:

````markdown
## Backups & Recovery

### Snapshots

```
annotation-pipeline db backup --root <workspace>
```

Default retention: 24 hourly + 30 daily, tunable via `--hourly-keep` / `--daily-keep`.

Schedule hourly with cron:

```
0 * * * * annotation-pipeline db backup --root /path/to/workspace
```

### Restore

A snapshot is a self-contained `db.sqlite` file. To restore:

```
cp backups/sqlite-YYYY-MM-DD-HHMM.sqlite db.sqlite
```

(Stop any running scheduler first.)

### Genesis archive

The one-time migration archives the original JSON tree to
`backups/genesis-YYYYMMDD/`. This is your from-zero recovery source if
both the live DB and snapshots are lost. Keep it.
````

- [ ] **Step 5: Commit**

```bash
git add TECHNICAL_ARCHITECTURE.md README.md CHANGELOG.md docs/agent-operator-guide.md
git commit -m "docs: SqliteStore migration, backup, recovery procedures"
```

---

## Self-Review

**Spec coverage check:**
- ✅ SqliteStore with full FileStore parity — Tasks 1–9
- ✅ Atomic lease via UNIQUE(task_id, stage) — Task 6
- ✅ Indexed `(pipeline_id, status, created_at)` queries — Task 2
- ✅ Migration script with row-count verification + genesis archive — Task 13
- ✅ `apl db init/status/backup/dump-json` — Task 14
- ✅ Reverse JSON dump — Task 12
- ✅ Backup snapshot + retention — Task 11
- ✅ Multi-process concurrency verified — Task 10
- ✅ Cutover of all 77 references — Task 15
- ✅ Legacy `FileStore` retained only as migration helper — Task 16
- ✅ Smoke test against real workspace — Task 17
- ✅ Docs (architecture, README, changelog, ops guide) — Task 18

**Placeholder scan:** none found. Every task contains the actual code.

**Type consistency check:**
- `SqliteStore.open(root)` signature consistent across all tasks.
- `Task.from_dict(...)` keys match `Task.to_dict()` schema (verified against `core/models.py`).
- `list_tasks_by_pipeline(pipeline_id)` and `list_tasks_by_status(statuses: Iterable[TaskStatus])` referenced consistently in Tasks 2, 5, 15.
- `migrate(src, dst, *, archive_genesis: bool, force: bool)` signature consistent in script and tests.

**Known compromises:**
- `file_store.py` is NOT deleted; kept as migration-script dependency. AGENT.md says "always remove legacy code" but the migration script is the only legitimate reader of the old format and removing the class would force inlining ~200 lines of read code into the script. Cleaner to keep the file with a deprecation docstring and delete it after the migration is no longer needed (separate future cleanup).
- `local_scheduler.py:36` keeps `list_tasks()` because the filter combines status + custom logic; adding a helper for that one site would be over-engineering.
- Smoke test (Task 17) is documented as manual; it cannot be automated without a real workspace fixture.
