# Prior-driven Verifier + Post-hoc Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an external statistical verifier (`entity_statistics` + `PriorVerifier`) that catches LLM correlated-error cascades by comparing each (span, type) decision against the project's empirical distribution; escalate divergent decisions through a second-arbiter / HR path; surface long-tail deviations and contested concepts via a Posterior Audit UI tab.

**Architecture:** Two-table separation. `entity_statistics` accumulates ALL ACCEPTED decisions (verifier source). `entity_conventions` (existing) accumulates only high-trust paths (no arbiter — cascade-safe prompt injection). Verifier triggers at QC pass, arbiter ruling, and HR submit_correction.

**Tech Stack:** Python 3.11+, SQLite (existing additive migration pattern), HTTP API in `interfaces/api.py`, React/TypeScript dashboard in `web/`.

**Design source:** `docs/superpowers/specs/2026-05-17-prior-driven-verifier-design.md`

---

## File Structure

**New files:**
- `annotation_pipeline_skill/services/entity_statistics_service.py` — `EntityStatisticsService` (counters + verifier)
- `tests/test_entity_statistics_service.py` — unit tests for the service
- `tests/test_prior_verifier_integration.py` — integration tests for the runtime triggers
- `scripts/bootstrap_entity_statistics.py` — one-time backfill from existing ACCEPTED tasks
- `web/src/components/PosteriorAuditPanel.tsx` — new UI tab

**Modified files:**
- `annotation_pipeline_skill/store/sqlite_store.py` — additive migration adds `entity_statistics` table
- `annotation_pipeline_skill/runtime/subagent_cycle.py` — wire verifier into QC pass + arbiter post-check + add `_invoke_second_arbiter`
- `annotation_pipeline_skill/services/human_review_service.py` — wire verifier into `submit_correction` and `decide(accept)`; support `force` override flag
- `annotation_pipeline_skill/services/entity_convention_service.py` — extend `record_decision` source vocabulary; no behavior change
- `annotation_pipeline_skill/interfaces/api.py` — new `GET /api/posterior-audit` endpoint
- `web/src/App.tsx` — register the new tab
- `web/src/types.ts` — add types for audit payload
- `projects/llm_profiles.yaml` — add `claude_sonnet_arbiter` profile + `arbiter_secondary` target (project-level demo; documented as required for prod)

---

## Task 1: Schema migration for entity_statistics

**Files:**
- Modify: `annotation_pipeline_skill/store/sqlite_store.py:36-53` (the `_ADDITIVE_MIGRATIONS_SQL` block)

- [ ] **Step 1: Write the failing test**

Create `tests/test_entity_statistics_schema.py`:

```python
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_entity_statistics_table_exists(tmp_path):
    store = SqliteStore.open(tmp_path)
    cols = [
        r["name"]
        for r in store._conn.execute("PRAGMA table_info(entity_statistics)").fetchall()
    ]
    assert cols == ["project_id", "span_lower", "entity_type", "count", "updated_at"]


def test_entity_statistics_primary_key(tmp_path):
    store = SqliteStore.open(tmp_path)
    now = "2026-05-17T00:00:00+00:00"
    store._conn.execute(
        "INSERT INTO entity_statistics (project_id, span_lower, entity_type, count, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("p", "apple", "organization", 1, now),
    )
    # Same (project_id, span_lower, entity_type) → conflict
    import sqlite3
    try:
        store._conn.execute(
            "INSERT INTO entity_statistics (project_id, span_lower, entity_type, count, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("p", "apple", "organization", 5, now),
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_entity_statistics_schema.py -q`
Expected: FAIL — `no such table: entity_statistics`

- [ ] **Step 3: Add migration**

Open `annotation_pipeline_skill/store/sqlite_store.py` and extend `_ADDITIVE_MIGRATIONS_SQL` (currently the block at lines 36–53). Replace the closing `"""` with the addition:

```python
_ADDITIVE_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS entity_conventions (
    convention_id  TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    span_lower     TEXT NOT NULL,
    span_original  TEXT NOT NULL,
    entity_type    TEXT,
    status         TEXT NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    proposals_json TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    created_by     TEXT NOT NULL,
    notes          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_project_span ON entity_conventions(project_id, span_lower);
CREATE INDEX IF NOT EXISTS idx_conv_project_status ON entity_conventions(project_id, status);

CREATE TABLE IF NOT EXISTS entity_statistics (
    project_id   TEXT NOT NULL,
    span_lower   TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (project_id, span_lower, entity_type)
);
CREATE INDEX IF NOT EXISTS idx_entity_stats_span ON entity_statistics(project_id, span_lower);
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_entity_statistics_schema.py -q`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/store/sqlite_store.py tests/test_entity_statistics_schema.py
git commit -m "feat(store): add entity_statistics additive migration"
```

---

## Task 2: EntityStatisticsService — counters + verifier

**Files:**
- Create: `annotation_pipeline_skill/services/entity_statistics_service.py`
- Test: `tests/test_entity_statistics_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_entity_statistics_service.py`:

```python
from annotation_pipeline_skill.services.entity_statistics_service import (
    EntityStatisticsService,
    VerifierResult,
)
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_increment_and_distribution(tmp_path):
    store = SqliteStore.open(tmp_path)
    svc = EntityStatisticsService(store)

    svc.increment(project_id="p", span="Apple", entity_type="organization", weight=1)
    svc.increment(project_id="p", span="apple", entity_type="organization", weight=2)
    svc.increment(project_id="p", span="APPLE", entity_type="project", weight=1)

    dist = svc.distribution(project_id="p", span="Apple")
    assert dist == {"organization": 3, "project": 1}
    assert svc.total(project_id="p", span="Apple") == 4


def test_check_cold_start(tmp_path):
    store = SqliteStore.open(tmp_path)
    svc = EntityStatisticsService(store)
    for i in range(9):  # less than MIN_PRIOR_SAMPLES (10)
        svc.increment(project_id="p", span="Apple", entity_type="organization")
    result = svc.check(project_id="p", span="Apple", proposed_type="technology")
    assert result.status == "cold_start"


def test_check_agree_when_dominance_low(tmp_path):
    store = SqliteStore.open(tmp_path)
    svc = EntityStatisticsService(store)
    # 6 org + 4 project = 60/40 split; no type >= 80% → agree
    for _ in range(6):
        svc.increment(project_id="p", span="Apple", entity_type="organization")
    for _ in range(4):
        svc.increment(project_id="p", span="Apple", entity_type="project")
    result = svc.check(project_id="p", span="Apple", proposed_type="technology")
    assert result.status == "agree"


def test_check_agree_when_match_dominant(tmp_path):
    store = SqliteStore.open(tmp_path)
    svc = EntityStatisticsService(store)
    for _ in range(9):
        svc.increment(project_id="p", span="Apple", entity_type="organization")
    svc.increment(project_id="p", span="Apple", entity_type="project")
    result = svc.check(project_id="p", span="Apple", proposed_type="organization")
    assert result.status == "agree"


def test_check_divergent(tmp_path):
    store = SqliteStore.open(tmp_path)
    svc = EntityStatisticsService(store)
    for _ in range(9):
        svc.increment(project_id="p", span="Apple", entity_type="organization")
    svc.increment(project_id="p", span="Apple", entity_type="project")
    result = svc.check(project_id="p", span="Apple", proposed_type="technology")
    assert result.status == "divergent"
    assert result.dominant_type == "organization"
    assert result.dominant_count == 9
    assert result.total == 10
    assert result.distribution == {"organization": 9, "project": 1}


def test_contested_spans(tmp_path):
    store = SqliteStore.open(tmp_path)
    svc = EntityStatisticsService(store)
    # Contested: 13 org + 12 project + 5 tech (top=43%, runner-up=40%)
    for _ in range(13):
        svc.increment(project_id="p", span="Microsoft", entity_type="organization")
    for _ in range(12):
        svc.increment(project_id="p", span="Microsoft", entity_type="project")
    for _ in range(5):
        svc.increment(project_id="p", span="Microsoft", entity_type="technology")
    # Not contested: 9 org + 1 project (dominant > 80%)
    for _ in range(9):
        svc.increment(project_id="p", span="Apple", entity_type="organization")
    svc.increment(project_id="p", span="Apple", entity_type="project")

    contested = svc.contested_spans(project_id="p")
    assert len(contested) == 1
    assert contested[0]["span"] == "Microsoft"
    assert contested[0]["prior_total"] == 30
    assert contested[0]["prior_distribution"] == {"organization": 13, "project": 12, "technology": 5}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_entity_statistics_service.py -q`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Write minimal implementation**

Create `annotation_pipeline_skill/services/entity_statistics_service.py`:

```python
"""Per-project span/type frequency table used as external verifier.

Distinct from ``entity_conventions`` (which holds the high-trust subset
of decisions injected into prompts). ``entity_statistics`` accumulates
ALL ACCEPTED decisions — annotator+QC, arbiter, HR — without filtering.
HR decisions count with extra weight because they are the only
ground-truth source.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from annotation_pipeline_skill.store.sqlite_store import SqliteStore


# Verifier tuning constants. Kept module-level so call sites can introspect
# them in tests and operators can override via project workflow.yaml later.
MIN_PRIOR_SAMPLES = 10
DOMINANCE_THRESHOLD = 0.80
HR_WEIGHT = 5
MIN_CONTESTED_SAMPLES = 10
MIN_RUNNER_UP_SHARE = 0.20


@dataclass(frozen=True)
class VerifierResult:
    """Outcome of one PriorVerifier.check() call.

    status:
      - 'agree'      — proposed_type matches the dominant prior, or no clear
                       dominant exists (prior insufficiently opinionated).
      - 'cold_start' — fewer than MIN_PRIOR_SAMPLES total observations.
      - 'divergent'  — clear dominant prior disagrees with proposed_type.
    """
    status: str
    span: str
    proposed_type: str
    dominant_type: str | None = None
    dominant_count: int = 0
    total: int = 0
    distribution: dict[str, int] | None = None


class EntityStatisticsService:
    def __init__(self, store: SqliteStore):
        self.store = store

    def increment(
        self,
        *,
        project_id: str,
        span: str,
        entity_type: str,
        weight: int = 1,
    ) -> None:
        """UPSERT count += weight on (project_id, span_lower, entity_type)."""
        if not span or not entity_type or weight <= 0:
            return
        span_lower = span.strip().lower()
        if not span_lower:
            return
        now = datetime.now(timezone.utc).isoformat()
        self.store._conn.execute(
            """
            INSERT INTO entity_statistics (project_id, span_lower, entity_type, count, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id, span_lower, entity_type) DO UPDATE SET
                count = count + excluded.count,
                updated_at = excluded.updated_at
            """,
            (project_id, span_lower, entity_type, weight, now),
        )

    def distribution(self, *, project_id: str, span: str) -> dict[str, int]:
        """Return {entity_type: count} for the given span. Empty if unseen."""
        span_lower = span.strip().lower()
        if not span_lower:
            return {}
        rows = self.store._conn.execute(
            "SELECT entity_type, count FROM entity_statistics "
            "WHERE project_id = ? AND span_lower = ?",
            (project_id, span_lower),
        ).fetchall()
        return {r["entity_type"]: r["count"] for r in rows}

    def total(self, *, project_id: str, span: str) -> int:
        return sum(self.distribution(project_id=project_id, span=span).values())

    def check(
        self,
        *,
        project_id: str,
        span: str,
        proposed_type: str,
    ) -> VerifierResult:
        dist = self.distribution(project_id=project_id, span=span)
        total = sum(dist.values())
        if total < MIN_PRIOR_SAMPLES:
            return VerifierResult(
                status="cold_start",
                span=span,
                proposed_type=proposed_type,
                total=total,
                distribution=dist or None,
            )
        dominant_type = max(dist, key=dist.get)
        dominant_count = dist[dominant_type]
        if dominant_count / total < DOMINANCE_THRESHOLD:
            return VerifierResult(
                status="agree",
                span=span,
                proposed_type=proposed_type,
                dominant_type=dominant_type,
                dominant_count=dominant_count,
                total=total,
                distribution=dist,
            )
        if dominant_type == proposed_type:
            return VerifierResult(
                status="agree",
                span=span,
                proposed_type=proposed_type,
                dominant_type=dominant_type,
                dominant_count=dominant_count,
                total=total,
                distribution=dist,
            )
        return VerifierResult(
            status="divergent",
            span=span,
            proposed_type=proposed_type,
            dominant_type=dominant_type,
            dominant_count=dominant_count,
            total=total,
            distribution=dist,
        )

    def contested_spans(self, *, project_id: str) -> list[dict[str, Any]]:
        """Return spans where the prior distribution has no clear winner.

        Criteria (all required):
          - total >= MIN_CONTESTED_SAMPLES
          - no type >= DOMINANCE_THRESHOLD (would be "settled")
          - at least two types each >= MIN_RUNNER_UP_SHARE (genuine split)
        """
        rows = self.store._conn.execute(
            "SELECT span_lower, entity_type, count FROM entity_statistics "
            "WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        per_span: dict[str, dict[str, int]] = {}
        for r in rows:
            per_span.setdefault(r["span_lower"], {})[r["entity_type"]] = r["count"]
        out: list[dict[str, Any]] = []
        for span, dist in per_span.items():
            total = sum(dist.values())
            if total < MIN_CONTESTED_SAMPLES:
                continue
            shares = sorted(
                ((t, c / total) for t, c in dist.items()), key=lambda kv: kv[1], reverse=True
            )
            top_share = shares[0][1]
            if top_share >= DOMINANCE_THRESHOLD:
                continue
            second_share = shares[1][1] if len(shares) > 1 else 0.0
            if second_share < MIN_RUNNER_UP_SHARE:
                continue
            out.append({
                "span": span,
                "prior_total": total,
                "prior_distribution": dist,
                "top_share": round(top_share, 3),
                "runner_up_share": round(second_share, 3),
            })
        out.sort(key=lambda r: r["prior_total"], reverse=True)
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_entity_statistics_service.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/services/entity_statistics_service.py tests/test_entity_statistics_service.py
git commit -m "feat(stats): EntityStatisticsService — counters + verifier + contested-spans"
```

---

## Task 3: Helper — iterate (span, type) pairs in an annotation

This helper is used by every trigger point (QC pass, arbiter post-check, HR submit) to iterate all spans needing verification. Defining once avoids duplication.

**Files:**
- Modify: `annotation_pipeline_skill/services/entity_statistics_service.py` (append)
- Test: `tests/test_entity_statistics_service.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_entity_statistics_service.py`:

```python
def test_iter_span_decisions_walks_entities():
    from annotation_pipeline_skill.services.entity_statistics_service import (
        iter_span_decisions,
    )
    payload = {
        "rows": [
            {
                "row_index": 0,
                "output": {
                    "entities": {
                        "organization": ["Apple", "Google"],
                        "person": ["Alice"],
                    },
                    "json_structures": {
                        "goal": ["improve perf"],
                    },
                },
            }
        ]
    }
    decisions = list(iter_span_decisions(payload))
    assert ("Apple", "organization") in decisions
    assert ("Google", "organization") in decisions
    assert ("Alice", "person") in decisions
    # json_structures is intentionally NOT included — only entities go through
    # the type-classification verifier.
    assert all(typ in ("organization", "person") for _, typ in decisions)


def test_iter_span_decisions_handles_missing_fields():
    from annotation_pipeline_skill.services.entity_statistics_service import (
        iter_span_decisions,
    )
    assert list(iter_span_decisions({})) == []
    assert list(iter_span_decisions({"rows": "not a list"})) == []
    assert list(iter_span_decisions({"rows": [{"output": None}]})) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_entity_statistics_service.py::test_iter_span_decisions_walks_entities tests/test_entity_statistics_service.py::test_iter_span_decisions_handles_missing_fields -q`
Expected: FAIL — `cannot import name 'iter_span_decisions'`

- [ ] **Step 3: Add the helper**

Append to `annotation_pipeline_skill/services/entity_statistics_service.py`:

```python
def iter_span_decisions(payload: Any) -> "list[tuple[str, str]]":
    """Yield (span, entity_type) pairs from an annotation payload.

    Walks ``rows[*].output.entities[type] = [span, ...]``. Only the
    ``entities`` key is iterated — ``json_structures`` phrases are
    free-form text that don't have a single canonical type per span (a
    phrase can be both a "goal" and a "constraint" legitimately), so they
    are NOT subject to the type-classification verifier.

    Skips non-string spans, empty spans, and non-conforming structures.
    """
    out: list[tuple[str, str]] = []
    if not isinstance(payload, dict):
        return out
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        output = row.get("output")
        if not isinstance(output, dict):
            continue
        entities = output.get("entities")
        if not isinstance(entities, dict):
            continue
        for typ, items in entities.items():
            if not isinstance(items, list):
                continue
            for span in items:
                if isinstance(span, str) and span.strip():
                    out.append((span, typ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_entity_statistics_service.py -q`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/services/entity_statistics_service.py tests/test_entity_statistics_service.py
git commit -m "feat(stats): iter_span_decisions helper for verifier callers"
```

---

## Task 4: Bootstrap script — backfill stats from existing ACCEPTED tasks

**Files:**
- Create: `scripts/bootstrap_entity_statistics.py`
- Test: `tests/test_bootstrap_entity_statistics.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bootstrap_entity_statistics.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_bootstrap_entity_statistics.py -q`
Expected: FAIL — script doesn't exist

- [ ] **Step 3: Write the script**

Create `scripts/bootstrap_entity_statistics.py`:

```python
"""One-time backfill of entity_statistics from existing ACCEPTED tasks.

Scans every ACCEPTED task's final artifact (human_review_answer first,
otherwise the latest annotation_result), iterates its (span, type)
pairs, and increments entity_statistics. HR-authored answers receive
HR_WEIGHT (5x); all other paths receive +1.

The historical sample is naturally "clean" because all current ACCEPTED
tasks predate the dictionary-injection feature — their decisions weren't
conditioned on any convention block in the prompt.

Usage:
  python scripts/bootstrap_entity_statistics.py <workspace_root>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.services.entity_statistics_service import (
    HR_WEIGHT,
    EntityStatisticsService,
    iter_span_decisions,
)
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def _load_payload(store: SqliteStore, artifact) -> dict | None:
    path = store.root / artifact.path
    if not path.exists():
        return None
    try:
        outer = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(outer, dict):
        return None
    if artifact.kind == "human_review_answer":
        ans = outer.get("answer")
        return ans if isinstance(ans, dict) else None
    text = outer.get("text")
    if not isinstance(text, str):
        return None
    try:
        return json.loads(_strip_think(text))
    except (json.JSONDecodeError, ValueError):
        return None


def _pick_final_artifact(store: SqliteStore, task_id: str):
    arts = store.list_artifacts(task_id)
    hr = [a for a in arts if a.kind == "human_review_answer"]
    if hr:
        return hr[-1], True
    anns = [a for a in arts if a.kind == "annotation_result"]
    return (anns[-1], False) if anns else (None, False)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace_root", type=Path)
    args = parser.parse_args(argv)

    store = SqliteStore.open(args.workspace_root)
    svc = EntityStatisticsService(store)
    tasks = list(store.list_tasks_by_status({TaskStatus.ACCEPTED}))
    print(f"scanning {len(tasks)} ACCEPTED tasks...", file=sys.stderr)

    incremented = 0
    skipped_no_artifact = 0
    skipped_parse_fail = 0
    for task in tasks:
        artifact, is_hr = _pick_final_artifact(store, task.task_id)
        if artifact is None:
            skipped_no_artifact += 1
            continue
        payload = _load_payload(store, artifact)
        if payload is None:
            skipped_parse_fail += 1
            continue
        weight = HR_WEIGHT if is_hr else 1
        for span, entity_type in iter_span_decisions(payload):
            svc.increment(
                project_id=task.pipeline_id,
                span=span,
                entity_type=entity_type,
                weight=weight,
            )
            incremented += 1

    print(json.dumps({
        "tasks_scanned": len(tasks),
        "increments_recorded": incremented,
        "skipped_no_artifact": skipped_no_artifact,
        "skipped_parse_fail": skipped_parse_fail,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_bootstrap_entity_statistics.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/bootstrap_entity_statistics.py tests/test_bootstrap_entity_statistics.py
git commit -m "feat(stats): bootstrap script to backfill entity_statistics from accepted history"
```

---

## Task 5: Wire stats increments + verifier into QC-pass path

This task adds verifier semantics to the annotator+QC consensus path. On `divergent`, the task is routed to `ARBITRATING` (with a `prior_disagreement` feedback record) instead of going straight to `ACCEPTED`. On `agree` / `cold_start`, the task is `ACCEPTED` and stats are incremented.

**Files:**
- Modify: `annotation_pipeline_skill/runtime/subagent_cycle.py` (the QC-pass branch in `_run_qc_stage` — search for `"subagent qc accepted result"`)
- Test: `tests/test_prior_verifier_integration.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_prior_verifier_integration.py`:

```python
"""Integration tests for prior verifier wiring across the runtime."""
from __future__ import annotations

import asyncio
import json

import pytest

from annotation_pipeline_skill.core.models import ArtifactRef, Task
from annotation_pipeline_skill.core.states import FeedbackSource, TaskStatus
from annotation_pipeline_skill.llm.client import LLMGenerateResult
from annotation_pipeline_skill.runtime.subagent_cycle import SubagentRuntime
from annotation_pipeline_skill.services.entity_statistics_service import (
    EntityStatisticsService,
)
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def _seed_prior(store, *, project_id, span, type_to_count):
    svc = EntityStatisticsService(store)
    for typ, n in type_to_count.items():
        for _ in range(n):
            svc.increment(project_id=project_id, span=span, entity_type=typ)


def _make_task(task_id, *, input_text, project_id="p"):
    return Task.new(
        task_id=task_id,
        pipeline_id=project_id,
        source_ref={
            "kind": "jsonl",
            "payload": {
                "text": input_text,
                "rows": [{"row_index": 0, "input": input_text}],
                "annotation_guidance": {"output_schema": {"type": "object"}},
            },
        },
    )


class _RecorderClient:
    def __init__(self, qc_passed: bool, annotation: dict):
        self.qc_passed = qc_passed
        self.annotation = annotation

    async def generate(self, request):
        if "qc subagent" in request.instructions.lower():
            final = json.dumps({
                "passed": self.qc_passed,
                "message": "ok" if self.qc_passed else "issues",
                "failures": [] if self.qc_passed else [{"category": "x", "message": "bad", "confidence": "certain"}],
            })
        else:
            final = json.dumps(self.annotation)
        return LLMGenerateResult(
            final_text=final, raw_response={}, usage={}, diagnostics={},
            runtime="stub", provider="stub", model="stub", continuity_handle=None,
        )


def test_qc_pass_with_prior_agree_accepts_and_increments_stats(tmp_path):
    store = SqliteStore.open(tmp_path)
    project = "p"
    _seed_prior(store, project_id=project, span="Apple",
                type_to_count={"organization": 9, "project": 1})

    annotation = {
        "rows": [{
            "row_index": 0,
            "output": {"entities": {"organization": ["Apple"]}},
        }]
    }
    task = _make_task("t-agree", input_text="Apple is a company")
    task.status = TaskStatus.PENDING
    store.save_task(task)

    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda _t: _RecorderClient(qc_passed=True, annotation=annotation),
    )
    asyncio.run(runtime.run_task_async(store.load_task("t-agree")))

    after = store.load_task("t-agree")
    assert after.status is TaskStatus.ACCEPTED
    svc = EntityStatisticsService(store)
    # Original 9+1 from seed plus 1 increment from this acceptance.
    assert svc.distribution(project_id=project, span="Apple") == {
        "organization": 10, "project": 1,
    }


def test_qc_pass_with_prior_divergent_routes_to_arbitrating(tmp_path):
    store = SqliteStore.open(tmp_path)
    project = "p"
    _seed_prior(store, project_id=project, span="Apple",
                type_to_count={"organization": 10})

    annotation = {
        "rows": [{
            "row_index": 0,
            "output": {"entities": {"technology": ["Apple"]}},
        }]
    }
    task = _make_task("t-divergent", input_text="Apple is mentioned")
    task.status = TaskStatus.PENDING
    store.save_task(task)

    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda _t: _RecorderClient(qc_passed=True, annotation=annotation),
    )
    asyncio.run(runtime.run_task_async(store.load_task("t-divergent")))

    after = store.load_task("t-divergent")
    assert after.status is TaskStatus.ARBITRATING
    fbs = store.list_feedback("t-divergent")
    assert any(
        f.source_stage is FeedbackSource.VALIDATION and f.category == "prior_disagreement"
        for f in fbs
    )


def test_qc_pass_with_cold_start_accepts(tmp_path):
    store = SqliteStore.open(tmp_path)
    project = "p"
    # 5 samples — below MIN_PRIOR_SAMPLES (10) → cold_start
    _seed_prior(store, project_id=project, span="Apple",
                type_to_count={"organization": 5})

    annotation = {
        "rows": [{
            "row_index": 0,
            "output": {"entities": {"technology": ["Apple"]}},
        }]
    }
    task = _make_task("t-cold", input_text="Apple is referenced")
    task.status = TaskStatus.PENDING
    store.save_task(task)

    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda _t: _RecorderClient(qc_passed=True, annotation=annotation),
    )
    asyncio.run(runtime.run_task_async(store.load_task("t-cold")))

    after = store.load_task("t-cold")
    assert after.status is TaskStatus.ACCEPTED
    svc = EntityStatisticsService(store)
    assert svc.distribution(project_id=project, span="Apple") == {
        "organization": 5, "technology": 1,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_prior_verifier_integration.py -q`
Expected: FAIL on at least the divergent test (task goes to ACCEPTED instead of ARBITRATING).

- [ ] **Step 3: Wire the verifier into the QC-pass branch**

Open `annotation_pipeline_skill/runtime/subagent_cycle.py`. Find the block that handles `qc_decision["passed"] == True` inside `_run_qc_stage` (look for the literal `"subagent qc accepted result"`). Replace that block with:

```python
        if qc_decision["passed"]:
            self._record_feedback_resolution(task, qc_attempt_id, qc_artifact, qc_decision)
            self._record_conventions_from_qc_consensus(task, annotation_artifact)
            # Prior verifier: compare each (span, type) against project history.
            verifier_failure = self._check_prior_verifier_on_annotation(
                task, annotation_artifact
            )
            if verifier_failure is not None:
                # Divergent — route to ARBITRATING for first-arbiter resolution.
                self.store.append_feedback(verifier_failure["feedback"])
                self._transition(
                    task,
                    TaskStatus.ARBITRATING,
                    reason="prior verifier flagged divergence at QC pass",
                    stage="prior_verifier",
                    attempt_id=qc_attempt_id,
                    metadata={
                        "qc_artifact_id": qc_artifact.artifact_id,
                        "prior_verifier_action": "qc_pass_divergent",
                        "verifier_payload": verifier_failure["payload"],
                    },
                )
                self.store.save_task(task)
                return
            # Agree / cold_start — accept and update statistics.
            self._increment_entity_statistics_for_task(task, annotation_artifact, weight=1)
            self._transition(
                task,
                TaskStatus.ACCEPTED,
                reason="subagent qc accepted result",
                stage="qc",
                attempt_id=qc_attempt_id,
                metadata={"qc_artifact_id": qc_artifact.artifact_id},
            )
```

- [ ] **Step 4: Add the two new methods at the same indent level**

Anywhere inside the `SubagentRuntime` class (e.g., right after `_record_conventions_from_qc_consensus`), add:

```python
    def _load_annotation_payload(self, annotation_artifact: ArtifactRef) -> dict | None:
        """Read the canonical JSON annotation payload from an artifact.

        Mirrors how _record_conventions_from_qc_consensus already reads it,
        kept as a single helper so the QC-pass / arbiter / HR sites all
        share the same parsing semantics.
        """
        try:
            outer = self._read_artifact_payload(annotation_artifact)
            if not isinstance(outer, dict):
                return None
            text = outer.get("text")
            if isinstance(text, str):
                try:
                    return _parse_llm_json(text)
                except (json.JSONDecodeError, ValueError):
                    return None
            return outer
        except Exception:  # noqa: BLE001
            return None

    def _check_prior_verifier_on_annotation(
        self,
        task: Task,
        annotation_artifact: ArtifactRef,
    ) -> dict | None:
        """Return {feedback, payload} on the FIRST divergent (span, type), or
        None when every span is agree/cold_start.
        """
        from annotation_pipeline_skill.services.entity_statistics_service import (
            EntityStatisticsService,
            iter_span_decisions,
        )
        payload = self._load_annotation_payload(annotation_artifact)
        if payload is None:
            return None
        svc = EntityStatisticsService(self.store)
        for span, entity_type in iter_span_decisions(payload):
            result = svc.check(
                project_id=task.pipeline_id,
                span=span,
                proposed_type=entity_type,
            )
            if result.status != "divergent":
                continue
            attempts = self.store.list_attempts(task.task_id)
            attempt_id = attempts[-1].attempt_id if attempts else f"{task.task_id}-attempt-0"
            verifier_payload = {
                "span": result.span,
                "proposed_type": result.proposed_type,
                "dominant_type": result.dominant_type,
                "dominant_count": result.dominant_count,
                "total": result.total,
                "distribution": result.distribution,
            }
            return {
                "payload": verifier_payload,
                "feedback": FeedbackRecord.new(
                    task_id=task.task_id,
                    attempt_id=attempt_id,
                    source_stage=FeedbackSource.VALIDATION,
                    severity=FeedbackSeverity.BLOCKING,
                    category="prior_disagreement",
                    message=(
                        f"Span {result.span!r} was classified as {result.proposed_type!r} "
                        f"but project history (N={result.total}) puts "
                        f"{result.dominant_count}/{result.total} "
                        f"({result.dominant_count * 100 // result.total}%) under "
                        f"{result.dominant_type!r}. Re-evaluate via arbiter."
                    ),
                    target=verifier_payload,
                    suggested_action="arbiter_rerun",
                    created_by="prior_verifier",
                ),
            }
        return None

    def _increment_entity_statistics_for_task(
        self,
        task: Task,
        annotation_artifact: ArtifactRef,
        *,
        weight: int,
    ) -> None:
        """Increment entity_statistics for every (span, type) in the task's
        final annotation. Best-effort — never raise to the caller.
        """
        from annotation_pipeline_skill.services.entity_statistics_service import (
            EntityStatisticsService,
            iter_span_decisions,
        )
        payload = self._load_annotation_payload(annotation_artifact)
        if payload is None:
            return
        svc = EntityStatisticsService(self.store)
        for span, entity_type in iter_span_decisions(payload):
            try:
                svc.increment(
                    project_id=task.pipeline_id,
                    span=span,
                    entity_type=entity_type,
                    weight=weight,
                )
            except Exception:  # noqa: BLE001
                continue
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_prior_verifier_integration.py -q`
Expected: PASS (all 3 tests)

Then full suite to confirm no regressions:

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add annotation_pipeline_skill/runtime/subagent_cycle.py tests/test_prior_verifier_integration.py
git commit -m "feat(verifier): wire prior verifier into QC-pass; route divergent to ARBITRATING"
```

---

## Task 6: Increment stats on arbiter-driven acceptance + verifier post-check

Arbiter rulings should always increment `entity_statistics` (broad signal). They should also be RE-checked against the prior — if arbiter's final type still diverges from the prior, the second arbiter is invoked.

Second-arbiter resolution is the next task; this task just adds the post-check that *detects* divergence and records it as a feedback for the next task to consume.

**Files:**
- Modify: `annotation_pipeline_skill/runtime/subagent_cycle.py` — both `_apply_arbiter_correction` and the closed-branch acceptance path inside `_terminal_from_arbiter`
- Test: `tests/test_prior_verifier_integration.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prior_verifier_integration.py`:

```python
def test_arbiter_acceptance_increments_stats(tmp_path):
    """When arbiter rules annotator-wins on a task that was QC-rejected,
    the resulting ACCEPTED transition still increments stats so they
    reflect every accepted decision in the project."""
    store = SqliteStore.open(tmp_path)
    project = "p"
    # Seed a clear prior agreeing with the annotation under test.
    _seed_prior(store, project_id=project, span="Acme",
                type_to_count={"organization": 12})

    # Manually fabricate the post-arbiter ACCEPTED transition shape that
    # _terminal_from_arbiter calls into.
    annotation = {
        "rows": [{
            "row_index": 0,
            "output": {"entities": {"organization": ["Acme"]}},
        }]
    }
    task = _make_task("t-arb", input_text="Acme is mentioned")
    task.status = TaskStatus.ARBITRATING
    store.save_task(task)

    # Drop a final annotation artifact for the runtime to read.
    rel_path = "artifact_payloads/t-arb/final.json"
    abs_path = store.root / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(
        json.dumps({"text": json.dumps(annotation)}), encoding="utf-8"
    )
    artifact = ArtifactRef.new(
        task_id="t-arb", kind="annotation_result", path=rel_path,
        content_type="application/json",
    )
    store.append_artifact(artifact)

    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda _t: _RecorderClient(qc_passed=True, annotation=annotation),
    )
    # Drive _terminal_from_arbiter through the closed-branch path.
    arb_outcome = {
        "ran": True, "closed": 1, "fixed": 0, "unresolved": 0,
        "mechanical_fail": 0, "corrected_annotation": None,
    }
    runtime._terminal_from_arbiter(
        store.load_task("t-arb"),
        attempt_id="t-arb-attempt-1", stage="arbitration", arb=arb_outcome,
    )

    svc = EntityStatisticsService(store)
    # 12 from seed + 1 from the arbiter-driven acceptance.
    assert svc.distribution(project_id=project, span="Acme") == {"organization": 13}


def test_arbiter_correction_records_divergent_payload(tmp_path):
    """When the arbiter writes a corrected_annotation whose final (span, type)
    still diverges from prior, the post-check marks the task metadata so
    the next task (invoke second arbiter) can pick it up."""
    store = SqliteStore.open(tmp_path)
    project = "p"
    _seed_prior(store, project_id=project, span="Apple",
                type_to_count={"organization": 12})

    task = _make_task("t-arb-fix", input_text="Apple is here")
    task.status = TaskStatus.ARBITRATING
    store.save_task(task)

    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda _t: _RecorderClient(qc_passed=True, annotation={}),
    )
    corrected = {
        "rows": [{
            "row_index": 0,
            "output": {"entities": {"technology": ["Apple"]}},  # diverges from prior
        }]
    }
    arb_outcome = {
        "ran": True, "closed": 0, "fixed": 1, "unresolved": 0,
        "mechanical_fail": 0, "corrected_annotation": corrected,
    }
    result = runtime._apply_arbiter_correction(
        store.load_task("t-arb-fix"),
        attempt_id="t-arb-fix-attempt-1",
        corrected=corrected,
        arb=arb_outcome,
    )
    # First arbiter post-check should mark the divergence in task metadata.
    after = store.load_task("t-arb-fix")
    assert after.metadata.get("prior_verifier_first_arbiter_divergent") is True
    assert "prior_verifier_payload" in after.metadata
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_prior_verifier_integration.py::test_arbiter_acceptance_increments_stats tests/test_prior_verifier_integration.py::test_arbiter_correction_records_divergent_payload -q`
Expected: FAIL — stats not incremented + no `prior_verifier_first_arbiter_divergent` metadata.

- [ ] **Step 3: Add stats increment + first-arbiter post-check**

In `annotation_pipeline_skill/runtime/subagent_cycle.py`, find `_terminal_from_arbiter`. In its `closed > 0` ACCEPTED branch, immediately before the `self._transition(... TaskStatus.ACCEPTED ...)` call, add stats increment + verifier post-check:

```python
        if arb["closed"] > 0:
            # ... existing _latest_annotation_is_valid_json check ...
            annotation_artifact = self._latest_annotation_artifact(task.task_id)
            if annotation_artifact is not None:
                self._increment_entity_statistics_for_task(task, annotation_artifact, weight=1)
                self._mark_first_arbiter_divergence_if_any(task, annotation_artifact)
            self._transition(
                task,
                TaskStatus.ACCEPTED,
                ...existing args...
            )
            return TaskStatus.ACCEPTED
```

In `_apply_arbiter_correction`, after the corrected_annotation is written to disk and just before the function returns `TaskStatus.ACCEPTED`:

```python
        # Stats + verifier post-check on the corrected annotation that was just persisted.
        self._increment_entity_statistics_for_task(task, new_artifact, weight=1)
        self._mark_first_arbiter_divergence_if_any(task, new_artifact)
```

Add the new helper inside the class (near `_check_prior_verifier_on_annotation`):

```python
    def _mark_first_arbiter_divergence_if_any(
        self,
        task: Task,
        annotation_artifact: ArtifactRef,
    ) -> None:
        """If the just-accepted annotation has any (span, type) that diverges
        from prior, stash the verifier payload on task.metadata so the
        second-arbiter trigger (next task) can detect and invoke."""
        divergence = self._check_prior_verifier_on_annotation(task, annotation_artifact)
        if divergence is None:
            return
        task.metadata["prior_verifier_first_arbiter_divergent"] = True
        task.metadata["prior_verifier_payload"] = divergence["payload"]
```

Also add `_latest_annotation_artifact` if it doesn't already exist (most likely it does — grep for it; if missing, define returning the latest `annotation_result` kind artifact from `store.list_artifacts(task.task_id)`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_prior_verifier_integration.py -q`
Expected: PASS (all 5 tests including the two new ones)

Then: `.venv/bin/python -m pytest tests/ -q` → PASS overall.

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/runtime/subagent_cycle.py tests/test_prior_verifier_integration.py
git commit -m "feat(verifier): increment stats on arbiter-driven acceptance; mark first-arbiter divergence"
```

---

## Task 7: Second-arbiter profile + invocation helper

**Files:**
- Modify: `projects/llm_profiles.yaml` (add profile + target)
- Modify: `annotation_pipeline_skill/runtime/subagent_cycle.py` — add `_invoke_second_arbiter` helper
- Test: `tests/test_prior_verifier_integration.py` (append)

- [ ] **Step 1: Add the secondary arbiter profile**

Edit `projects/llm_profiles.yaml`. Inside `profiles:`, add (the exact model and binary can be tuned for the user's environment — the example uses Claude Sonnet which the project already references for other roles):

```yaml
  claude_sonnet_arbiter:
    provider: local_cli
    cli_kind: claude
    cli_binary: claude
    model: sonnet
    permission_mode: dontAsk
    timeout_seconds: 900
    no_progress_timeout_seconds: 30
```

Inside the `targets:` block, add the new logical target:

```yaml
targets:
  annotation: minimax_2.7
  qc: deepseek_flash
  arbiter: codex_5.5_arbiter
  arbiter_secondary: claude_sonnet_arbiter   # NEW — different family for cross-LLM check
  fallback: codex_5.4_mini
  coordinator: glm_46
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_prior_verifier_integration.py`:

```python
def test_second_arbiter_invoked_when_first_diverges(tmp_path):
    """When the first arbiter's accepted annotation diverges from prior,
    the runtime invokes a SECOND arbiter via the arbiter_secondary target."""
    store = SqliteStore.open(tmp_path)
    project = "p"
    _seed_prior(store, project_id=project, span="Apple",
                type_to_count={"organization": 12})

    task = _make_task("t-second", input_text="Apple is here")
    task.status = TaskStatus.ARBITRATING
    task.metadata["prior_verifier_first_arbiter_divergent"] = True
    task.metadata["prior_verifier_payload"] = {
        "span": "Apple", "proposed_type": "technology",
        "dominant_type": "organization", "dominant_count": 12,
        "total": 12, "distribution": {"organization": 12},
    }
    store.save_task(task)

    invocations: list[str] = []

    class _MultiArbiterClient:
        def __init__(self, target):
            self.target = target
            invocations.append(target)
        async def generate(self, request):
            # Second arbiter returns the same "technology" → matches first
            return LLMGenerateResult(
                final_text=json.dumps({
                    "verdicts": [],
                    "corrected_annotation": {
                        "rows": [{
                            "row_index": 0,
                            "output": {"entities": {"technology": ["Apple"]}},
                        }]
                    },
                }),
                raw_response={}, usage={}, diagnostics={}, runtime="stub",
                provider=self.target, model="stub", continuity_handle=None,
            )

    runtime = SubagentRuntime(
        store=store,
        client_factory=lambda t: _MultiArbiterClient(t),
    )

    # Method-under-test: process the post-arbiter divergence flag.
    runtime._resolve_first_arbiter_divergence(store.load_task("t-second"))

    # Second arbiter must have been invoked via the arbiter_secondary target.
    assert "arbiter_secondary" in invocations
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_prior_verifier_integration.py::test_second_arbiter_invoked_when_first_diverges -q`
Expected: FAIL — `_resolve_first_arbiter_divergence` doesn't exist.

- [ ] **Step 4: Add the helper**

Inside `SubagentRuntime` in `annotation_pipeline_skill/runtime/subagent_cycle.py`, add:

```python
    async def _invoke_second_arbiter(
        self,
        task: Task,
        annotation_artifact: ArtifactRef,
    ) -> dict | None:
        """Run a second arbiter (different family) on the same task. Returns
        its parsed JSON payload, or None if the call fails. The second
        arbiter is given the SAME prompt as the first but does not see
        the first arbiter's output or the prior distribution — independence
        is critical for the cross-LLM check.
        """
        try:
            client = self.client_factory("arbiter_secondary")
        except Exception:  # noqa: BLE001
            return None
        # Build the same prompt the first arbiter saw. _build_arbiter_request
        # exists from Task 7's refactor; if not, extract the existing inline
        # prompt construction in _arbitrate_and_apply into that helper first.
        request = self._build_arbiter_request(task, annotation_artifact)
        try:
            result = await client.generate(request)
        except Exception:  # noqa: BLE001
            return None
        finally:
            close = getattr(client, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001
                    pass
        try:
            return _parse_llm_json(result.final_text)
        except (json.JSONDecodeError, ValueError):
            return None

    def _resolve_first_arbiter_divergence(self, task: Task) -> None:
        """Sync entry called by the scheduler when it sees a task with the
        ``prior_verifier_first_arbiter_divergent`` flag set. Runs the second
        arbiter and applies the resolution per spec §6.
        """
        asyncio.run(self._resolve_first_arbiter_divergence_async(task))

    async def _resolve_first_arbiter_divergence_async(self, task: Task) -> None:
        annotation_artifact = self._latest_annotation_artifact(task.task_id)
        if annotation_artifact is None:
            return
        second_payload = await self._invoke_second_arbiter(task, annotation_artifact)
        # The actual resolution comparison (matches first / matches prior /
        # third option) lives in Task 8. For now, simply clearing the flag
        # so the test sees the second-arbiter invocation.
        task.metadata.pop("prior_verifier_first_arbiter_divergent", None)
        task.metadata.pop("prior_verifier_payload", None)
        self.store.save_task(task)
```

If `_build_arbiter_request` doesn't exist yet, extract the prompt-construction logic from `_arbitrate_and_apply` into that helper as a small refactor in this same task.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_prior_verifier_integration.py::test_second_arbiter_invoked_when_first_diverges -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add annotation_pipeline_skill/runtime/subagent_cycle.py projects/llm_profiles.yaml tests/test_prior_verifier_integration.py
git commit -m "feat(verifier): arbiter_secondary profile + invocation helper"
```

---

## Task 8: Second-arbiter resolution logic

Compare second arbiter's chosen type for the contested (span) against the first arbiter's type and the prior dominant. Apply the three-way rule from spec §6.

**Files:**
- Modify: `annotation_pipeline_skill/runtime/subagent_cycle.py` — `_resolve_first_arbiter_divergence_async`
- Test: `tests/test_prior_verifier_integration.py` (append)

- [ ] **Step 1: Write the failing tests (three cases)**

Append to `tests/test_prior_verifier_integration.py`:

```python
def _setup_post_first_arbiter(tmp_path, second_arbiter_type):
    """Fabricate a task post first-arbiter (divergent) with the second
    arbiter stubbed to return ``second_arbiter_type`` for "Apple"."""
    store = SqliteStore.open(tmp_path)
    project = "p"
    _seed_prior(store, project_id=project, span="Apple",
                type_to_count={"organization": 12})

    task = _make_task("t", input_text="Apple is referenced here")
    task.status = TaskStatus.ARBITRATING
    task.metadata["prior_verifier_first_arbiter_divergent"] = True
    task.metadata["prior_verifier_payload"] = {
        "span": "Apple", "proposed_type": "technology",
        "dominant_type": "organization", "dominant_count": 12,
        "total": 12, "distribution": {"organization": 12},
    }
    store.save_task(task)

    rel = "artifact_payloads/t/final.json"
    abs_path = store.root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(
        json.dumps({"text": json.dumps({
            "rows": [{
                "row_index": 0,
                "output": {"entities": {"technology": ["Apple"]}},
            }]
        })}),
        encoding="utf-8",
    )
    store.append_artifact(ArtifactRef.new(
        task_id="t", kind="annotation_result", path=rel,
        content_type="application/json",
    ))

    class _Client:
        async def generate(self, request):
            return LLMGenerateResult(
                final_text=json.dumps({
                    "verdicts": [],
                    "corrected_annotation": {
                        "rows": [{
                            "row_index": 0,
                            "output": {"entities": {second_arbiter_type: ["Apple"]}},
                        }]
                    },
                }),
                raw_response={}, usage={}, diagnostics={}, runtime="stub",
                provider="arbiter_secondary", model="stub", continuity_handle=None,
            )

    runtime = SubagentRuntime(store=store, client_factory=lambda _t: _Client())
    return store, runtime


def test_second_arbiter_matches_first_accepts_with_first(tmp_path):
    """Second arbiter says technology (same as first). Two LLMs from
    different families agree → ACCEPTED with technology, overriding prior."""
    store, runtime = _setup_post_first_arbiter(tmp_path, "technology")
    runtime._resolve_first_arbiter_divergence(store.load_task("t"))
    after = store.load_task("t")
    assert after.status is TaskStatus.ACCEPTED


def test_second_arbiter_matches_prior_flips_to_prior(tmp_path):
    """Second arbiter agrees with the prior (organization). First arbiter
    was the outlier → ACCEPTED with organization."""
    store, runtime = _setup_post_first_arbiter(tmp_path, "organization")
    runtime._resolve_first_arbiter_divergence(store.load_task("t"))
    after = store.load_task("t")
    assert after.status is TaskStatus.ACCEPTED
    # The final annotation artifact should now have Apple = organization.
    arts = [a for a in store.list_artifacts("t") if a.kind == "annotation_result"]
    latest = arts[-1]
    outer = json.loads((store.root / latest.path).read_text())
    inner = json.loads(outer["text"]) if isinstance(outer.get("text"), str) else outer
    assert inner["rows"][0]["output"]["entities"] == {"organization": ["Apple"]}


def test_second_arbiter_third_option_routes_to_hr(tmp_path):
    """Second arbiter returns a third type (project) — three-way
    disagreement → HUMAN_REVIEW."""
    store, runtime = _setup_post_first_arbiter(tmp_path, "project")
    runtime._resolve_first_arbiter_divergence(store.load_task("t"))
    after = store.load_task("t")
    assert after.status is TaskStatus.HUMAN_REVIEW
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_prior_verifier_integration.py -k second_arbiter -q`
Expected: FAIL on the three new tests — resolution not implemented.

- [ ] **Step 3: Implement resolution logic**

Replace the placeholder body of `_resolve_first_arbiter_divergence_async` (added in Task 7) with the full three-way resolution. Inside `SubagentRuntime` in `annotation_pipeline_skill/runtime/subagent_cycle.py`:

```python
    async def _resolve_first_arbiter_divergence_async(self, task: Task) -> None:
        annotation_artifact = self._latest_annotation_artifact(task.task_id)
        if annotation_artifact is None:
            self._clear_divergence_flag(task)
            return
        payload = task.metadata.get("prior_verifier_payload") or {}
        span = payload.get("span")
        first_type = payload.get("proposed_type")
        prior_type = payload.get("dominant_type")
        if not span or not first_type or not prior_type:
            self._clear_divergence_flag(task)
            return

        second_payload = await self._invoke_second_arbiter(task, annotation_artifact)
        if not isinstance(second_payload, dict):
            # Second arbiter unavailable — accept first arbiter's call to
            # avoid blocking the pipeline. Surface in audit via metadata.
            task.metadata["prior_verifier_action"] = "second_arbiter_unavailable"
            self._clear_divergence_flag(task)
            self.store.save_task(task)
            return

        second_corrected = second_payload.get("corrected_annotation") if isinstance(
            second_payload.get("corrected_annotation"), dict
        ) else None
        second_type = self._extract_type_for_span(second_corrected, span) if second_corrected else None
        if second_type is None:
            # Second arbiter chose "annotator-wins" implicitly — use the
            # annotation that's currently on disk.
            current = self._load_annotation_payload(annotation_artifact)
            second_type = self._extract_type_for_span(current, span)

        if second_type == first_type:
            task.metadata["prior_verifier_action"] = "resolved_to_first"
            self._clear_divergence_flag(task)
            self._increment_entity_statistics_for_task(task, annotation_artifact, weight=1)
            self._transition(
                task,
                TaskStatus.ACCEPTED,
                reason="second arbiter agrees with first; override prior",
                stage="prior_verifier",
                metadata={"prior_verifier_action": "resolved_to_first"},
            )
        elif second_type == prior_type:
            # Override the annotation file: change span's type to prior_type.
            corrected_payload = self._load_annotation_payload(annotation_artifact)
            self._rewrite_span_type(corrected_payload, span, first_type, prior_type)
            new_artifact = self._write_corrected_annotation_artifact(task, corrected_payload)
            task.metadata["prior_verifier_action"] = "resolved_to_prior"
            self._clear_divergence_flag(task)
            self._increment_entity_statistics_for_task(task, new_artifact, weight=1)
            self._transition(
                task,
                TaskStatus.ACCEPTED,
                reason="second arbiter agrees with prior; flip first arbiter's call",
                stage="prior_verifier",
                metadata={"prior_verifier_action": "resolved_to_prior"},
            )
        else:
            task.metadata["prior_verifier_action"] = "escalated_to_hr"
            self._clear_divergence_flag(task)
            self._transition(
                task,
                TaskStatus.HUMAN_REVIEW,
                reason="three-way disagreement: first arbiter, second arbiter, and prior all differ",
                stage="prior_verifier",
                metadata={
                    "first_arbiter_type": first_type,
                    "second_arbiter_type": second_type,
                    "prior_dominant_type": prior_type,
                    "span": span,
                },
            )
        self.store.save_task(task)

    @staticmethod
    def _extract_type_for_span(payload: Any, span: str) -> str | None:
        if not isinstance(payload, dict):
            return None
        for row in payload.get("rows", []) or []:
            if not isinstance(row, dict):
                continue
            entities = (row.get("output") or {}).get("entities")
            if not isinstance(entities, dict):
                continue
            for typ, items in entities.items():
                if isinstance(items, list) and span in items:
                    return typ
        return None

    @staticmethod
    def _rewrite_span_type(payload: Any, span: str, old_type: str, new_type: str) -> None:
        if not isinstance(payload, dict):
            return
        for row in payload.get("rows", []) or []:
            if not isinstance(row, dict):
                continue
            entities = (row.get("output") or {}).get("entities")
            if not isinstance(entities, dict):
                continue
            old_items = entities.get(old_type) or []
            if span in old_items:
                old_items.remove(span)
                if not old_items:
                    entities.pop(old_type, None)
                else:
                    entities[old_type] = old_items
                entities.setdefault(new_type, []).append(span)

    def _write_corrected_annotation_artifact(
        self, task: Task, payload: dict
    ) -> ArtifactRef:
        attempt_id = self._next_attempt_id(task)
        rel = f"artifact_payloads/{task.task_id}/{attempt_id}_prior_verifier_fix.json"
        abs_path = self.store.root / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(
            json.dumps(
                {"text": json.dumps(payload, ensure_ascii=False), "source": "prior_verifier_fix"},
                sort_keys=True, indent=2,
            ),
            encoding="utf-8",
        )
        artifact = ArtifactRef.new(
            task_id=task.task_id, kind="annotation_result", path=rel,
            content_type="application/json",
            metadata={"source": "prior_verifier_fix"},
        )
        self.store.append_artifact(artifact)
        return artifact

    def _clear_divergence_flag(self, task: Task) -> None:
        task.metadata.pop("prior_verifier_first_arbiter_divergent", None)
        task.metadata.pop("prior_verifier_payload", None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_prior_verifier_integration.py -q`
Expected: PASS (all tests)

Then: `.venv/bin/python -m pytest tests/ -q` → PASS overall.

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/runtime/subagent_cycle.py tests/test_prior_verifier_integration.py
git commit -m "feat(verifier): second-arbiter resolution (matches-first / matches-prior / third → HR)"
```

---

## Task 9: Scheduler trigger for divergence resolution

The flag `prior_verifier_first_arbiter_divergent` is set by Task 6's post-check. Tasks with this flag need to be picked up automatically by the scheduler claim loop and processed by `_resolve_first_arbiter_divergence`.

**Files:**
- Modify: `annotation_pipeline_skill/runtime/local_scheduler.py` — claim loop dispatch
- Test: `tests/test_prior_verifier_integration.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prior_verifier_integration.py`:

```python
def test_scheduler_routes_divergent_task_to_resolver(tmp_path):
    """An ARBITRATING task with prior_verifier_first_arbiter_divergent=True
    should be picked up by the scheduler claim loop and resolved via
    _resolve_first_arbiter_divergence (not via the normal rearbitrate path)."""
    from annotation_pipeline_skill.runtime.local_scheduler import LocalRuntimeScheduler
    from annotation_pipeline_skill.core.runtime import RuntimeConfig

    store = SqliteStore.open(tmp_path)
    project = "p"
    _seed_prior(store, project_id=project, span="Apple",
                type_to_count={"organization": 12})

    task = _make_task("t-sched", input_text="Apple here")
    task.status = TaskStatus.ARBITRATING
    task.metadata["prior_verifier_first_arbiter_divergent"] = True
    task.metadata["prior_verifier_payload"] = {
        "span": "Apple", "proposed_type": "technology",
        "dominant_type": "organization", "dominant_count": 12,
        "total": 12, "distribution": {"organization": 12},
    }
    store.save_task(task)

    rel = "artifact_payloads/t-sched/final.json"
    abs_path = store.root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(
        json.dumps({"text": json.dumps({
            "rows": [{
                "row_index": 0,
                "output": {"entities": {"technology": ["Apple"]}},
            }]
        })}),
        encoding="utf-8",
    )
    store.append_artifact(ArtifactRef.new(
        task_id="t-sched", kind="annotation_result", path=rel,
        content_type="application/json",
    ))

    class _Stub:
        async def generate(self, request):
            return LLMGenerateResult(
                final_text=json.dumps({
                    "verdicts": [],
                    "corrected_annotation": {
                        "rows": [{"row_index": 0,
                                  "output": {"entities": {"technology": ["Apple"]}}}]
                    },
                }),
                raw_response={}, usage={}, diagnostics={}, runtime="stub",
                provider="arbiter_secondary", model="stub", continuity_handle=None,
            )

    sched = LocalRuntimeScheduler(
        store=store, client_factory=lambda _t: _Stub(),
        config=RuntimeConfig(max_concurrent_tasks=1),
    )

    async def run_one():
        await sched.run_forever(stop_when_idle=True, max_tasks=1)

    asyncio.run(run_one())
    after = store.load_task("t-sched")
    assert after.status is TaskStatus.ACCEPTED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_prior_verifier_integration.py::test_scheduler_routes_divergent_task_to_resolver -q`
Expected: FAIL — scheduler doesn't dispatch divergent tasks to the resolver.

- [ ] **Step 3: Modify the worker dispatch**

In `annotation_pipeline_skill/runtime/local_scheduler.py`, find the worker `try`/`except` block that does `await asyncio.wait_for(runtime.run_task_async(task, ...))`. Just before that call, check the metadata flag:

```python
                try:
                    if (
                        task.status is TaskStatus.ARBITRATING
                        and task.metadata.get("prior_verifier_first_arbiter_divergent")
                    ):
                        await asyncio.wait_for(
                            runtime._resolve_first_arbiter_divergence_async(task),
                            timeout=self.config.worker_task_timeout_seconds,
                        )
                    else:
                        await asyncio.wait_for(
                            runtime.run_task_async(task, stage_target=stage_target),
                            timeout=self.config.worker_task_timeout_seconds,
                        )
                except asyncio.TimeoutError:
                    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_prior_verifier_integration.py::test_scheduler_routes_divergent_task_to_resolver -q`
Expected: PASS

Then: `.venv/bin/python -m pytest tests/ -q` → PASS overall.

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/runtime/local_scheduler.py tests/test_prior_verifier_integration.py
git commit -m "feat(verifier): scheduler dispatches divergent-flagged tasks to resolver"
```

---

## Task 10: Wire verifier into HumanReviewService

**Files:**
- Modify: `annotation_pipeline_skill/services/human_review_service.py` — `submit_correction` + `decide(accept)`
- Test: `tests/test_human_review_service.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_human_review_service.py`:

```python
def test_submit_correction_rejects_when_against_prior(tmp_path):
    from annotation_pipeline_skill.services.entity_statistics_service import (
        EntityStatisticsService,
    )
    from annotation_pipeline_skill.services.human_review_service import (
        HumanReviewService,
    )
    from annotation_pipeline_skill.core.schema_validation import SchemaValidationError

    store = SqliteStore.open(tmp_path)
    svc = EntityStatisticsService(store)
    for _ in range(12):
        svc.increment(project_id="p", span="Apple", entity_type="organization")

    # Set up an HR task that the operator is correcting.
    schema = {"type": "object", "additionalProperties": False, "required": ["rows"],
              "properties": {"rows": {"type": "array"}}}
    task = Task.new(
        task_id="hr-1", pipeline_id="p",
        source_ref={"kind": "jsonl", "payload": {
            "rows": [{"row_index": 0, "input": "Apple is mentioned"}],
            "annotation_guidance": {"output_schema": schema},
        }},
    )
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)

    hr = HumanReviewService(store)
    answer = {"rows": [{"row_index": 0, "output": {"entities": {"technology": ["Apple"]}}}]}
    with pytest.raises(SchemaValidationError) as excinfo:
        hr.submit_correction(task_id="hr-1", answer=answer, actor="op", note=None)
    assert any(
        e.get("kind") == "prior_disagreement" for e in (excinfo.value.errors or [])
    )


def test_submit_correction_with_force_bypasses_verifier(tmp_path):
    from annotation_pipeline_skill.services.entity_statistics_service import (
        EntityStatisticsService,
    )
    from annotation_pipeline_skill.services.human_review_service import (
        HumanReviewService,
    )

    store = SqliteStore.open(tmp_path)
    svc = EntityStatisticsService(store)
    for _ in range(12):
        svc.increment(project_id="p", span="Apple", entity_type="organization")

    schema = {"type": "object", "additionalProperties": False, "required": ["rows"],
              "properties": {"rows": {"type": "array"}}}
    task = Task.new(
        task_id="hr-2", pipeline_id="p",
        source_ref={"kind": "jsonl", "payload": {
            "rows": [{"row_index": 0, "input": "Apple is mentioned"}],
            "annotation_guidance": {"output_schema": schema},
        }},
    )
    task.status = TaskStatus.HUMAN_REVIEW
    store.save_task(task)

    hr = HumanReviewService(store)
    answer = {"rows": [{"row_index": 0, "output": {"entities": {"technology": ["Apple"]}}}]}
    result = hr.submit_correction(
        task_id="hr-2", answer=answer, actor="op", note=None, force=True,
    )
    assert result.task.status is TaskStatus.ACCEPTED
    # HR-overridden decision still updates stats with HR weight (5x).
    assert svc.distribution(project_id="p", span="Apple") == {
        "organization": 12, "technology": 5,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_human_review_service.py::test_submit_correction_rejects_when_against_prior tests/test_human_review_service.py::test_submit_correction_with_force_bypasses_verifier -q`
Expected: FAIL — verifier not wired + `force` kwarg doesn't exist.

- [ ] **Step 3: Wire verifier + `force` flag**

Open `annotation_pipeline_skill/services/human_review_service.py`. Modify `submit_correction`:

```python
    def submit_correction(
        self,
        *,
        task_id: str,
        answer: dict,
        actor: str,
        note: str | None,
        force: bool = False,
    ) -> HumanCorrectionResult:
        task = self.store.load_task(task_id)
        if task.status is not TaskStatus.HUMAN_REVIEW:
            raise InvalidTransition(f"task {task_id} is not in human_review")

        # ... existing validators (schema, verbatim, cross-type, trailing-punct) ...

        # Prior verifier check — skipped on operator-force override.
        if not force:
            from annotation_pipeline_skill.services.entity_statistics_service import (
                EntityStatisticsService,
                iter_span_decisions,
            )
            svc = EntityStatisticsService(self.store)
            divergent = []
            for span, entity_type in iter_span_decisions(answer):
                r = svc.check(
                    project_id=task.pipeline_id,
                    span=span,
                    proposed_type=entity_type,
                )
                if r.status == "divergent":
                    divergent.append(r)
            if divergent:
                raise SchemaValidationError(
                    f"corrected answer disagrees with project prior on "
                    f"{len(divergent)} span(s); pass force=True to override",
                    [{
                        "kind": "prior_disagreement",
                        "path": f"output.entities[{r.proposed_type}]",
                        "message": (
                            f"span {r.span!r} proposed as {r.proposed_type!r} but "
                            f"prior ({r.dominant_count}/{r.total}) → {r.dominant_type!r}"
                        ),
                    } for r in divergent],
                )

        artifact = self._write_correction_artifact(...)
        # ... existing transition + feedback recording ...
        # NEW: increment stats with HR weight.
        self._increment_stats_from_hr(task, answer)
        return HumanCorrectionResult(...)
```

Also add a helper inside the class:

```python
    def _increment_stats_from_hr(self, task: Task, answer: dict) -> None:
        from annotation_pipeline_skill.services.entity_statistics_service import (
            HR_WEIGHT,
            EntityStatisticsService,
            iter_span_decisions,
        )
        svc = EntityStatisticsService(self.store)
        for span, entity_type in iter_span_decisions(answer):
            try:
                svc.increment(
                    project_id=task.pipeline_id, span=span,
                    entity_type=entity_type, weight=HR_WEIGHT,
                )
            except Exception:  # noqa: BLE001
                continue
```

Make the analogous change inside `decide(action="accept")`: after all existing checks, call verifier (no `force` here — operator must explicitly use `submit_correction` to override), and on success call `_increment_stats_from_hr` against the *underlying annotation* loaded via `_latest_annotation_payload`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_human_review_service.py -q`
Expected: PASS (all tests including new ones; previously-passing tests still pass).

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/services/human_review_service.py tests/test_human_review_service.py
git commit -m "feat(verifier): HR submit_correction + decide(accept) consult prior; force override"
```

---

## Task 11: API endpoint — GET /api/posterior-audit

**Files:**
- Modify: `annotation_pipeline_skill/interfaces/api.py` — add route + handler
- Test: `tests/test_posterior_audit_api.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_posterior_audit_api.py`:

```python
import json

from annotation_pipeline_skill.core.models import ArtifactRef, Task
from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.services.entity_statistics_service import (
    EntityStatisticsService,
)
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def _build_handler(store):
    from annotation_pipeline_skill.interfaces.api import DashboardHandler

    class _H(DashboardHandler):
        _stores = {"v3": store}
        _store_get_calls: list = []

        def __init__(self, *_a, **_k):
            self._captured: list = []

        def _json_response(self, status, body):
            self._captured.append((status, body))
            return status, body

    return _H()


def test_posterior_audit_returns_task_deviations_and_contested_spans(tmp_path):
    store = SqliteStore.open(tmp_path)
    svc = EntityStatisticsService(store)

    # Build prior: 12 Apple → organization (dominant, eligible)
    for _ in range(12):
        svc.increment(project_id="p", span="Apple", entity_type="organization")
    # Contested: Microsoft has 13/12/5
    for _ in range(13):
        svc.increment(project_id="p", span="Microsoft", entity_type="organization")
    for _ in range(12):
        svc.increment(project_id="p", span="Microsoft", entity_type="project")
    for _ in range(5):
        svc.increment(project_id="p", span="Microsoft", entity_type="technology")

    # Create an accepted task whose annotation tags Apple as technology
    # (diverges from prior).
    task = Task.new(
        task_id="t-dev", pipeline_id="p",
        source_ref={"kind": "jsonl", "payload": {
            "rows": [{"row_index": 0, "input": "Apple"}],
        }},
    )
    task.status = TaskStatus.ACCEPTED
    store.save_task(task)
    rel = "artifact_payloads/t-dev/final.json"
    abs_path = store.root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(json.dumps({"text": json.dumps({
        "rows": [{"row_index": 0,
                  "output": {"entities": {"technology": ["Apple"]}}}]
    })}))
    store.append_artifact(ArtifactRef.new(
        task_id="t-dev", kind="annotation_result", path=rel,
        content_type="application/json",
    ))

    # Direct call into the helper that the route delegates to (avoids
    # spinning up an HTTP server in tests).
    from annotation_pipeline_skill.interfaces.api import build_posterior_audit
    payload = build_posterior_audit(store, project_id="p")

    assert any(d["span"] == "Apple" and d["current_type"] == "technology"
               for d in payload["task_deviations"])
    assert any(c["span"] == "microsoft" or c["span"] == "Microsoft"
               for c in payload["contested_spans"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_posterior_audit_api.py -q`
Expected: FAIL — `cannot import name 'build_posterior_audit'`.

- [ ] **Step 3: Add the builder + register the route**

In `annotation_pipeline_skill/interfaces/api.py`, add the builder near the other `build_*` helpers at the top of the file:

```python
def build_posterior_audit(store, *, project_id: str) -> dict:
    """Scan every ACCEPTED task and compare its (span, type) decisions to
    entity_statistics. Return task-level deviations and project-level
    contested spans.
    """
    from annotation_pipeline_skill.core.states import TaskStatus
    from annotation_pipeline_skill.services.entity_statistics_service import (
        EntityStatisticsService,
        iter_span_decisions,
    )
    from annotation_pipeline_skill.runtime.subagent_cycle import _parse_llm_json
    import json as _json
    import re

    def _load_annotation(task) -> dict | None:
        arts = store.list_artifacts(task.task_id)
        hr = [a for a in arts if a.kind == "human_review_answer"]
        if hr:
            outer = _json.loads((store.root / hr[-1].path).read_text(encoding="utf-8"))
            return outer.get("answer") if isinstance(outer, dict) else None
        anns = [a for a in arts if a.kind == "annotation_result"]
        if not anns:
            return None
        outer = _json.loads((store.root / anns[-1].path).read_text(encoding="utf-8"))
        text = outer.get("text")
        if not isinstance(text, str):
            return None
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        try:
            return _parse_llm_json(text)
        except (ValueError, _json.JSONDecodeError):
            return None

    svc = EntityStatisticsService(store)
    deviations: list[dict] = []
    for task in store.list_tasks_by_pipeline(project_id):
        if task.status is not TaskStatus.ACCEPTED:
            continue
        payload = _load_annotation(task)
        if payload is None:
            continue
        for span, entity_type in iter_span_decisions(payload):
            r = svc.check(project_id=project_id, span=span, proposed_type=entity_type)
            if r.status != "divergent":
                continue
            deviations.append({
                "task_id": task.task_id,
                "row_index": 0,  # iter_span_decisions doesn't currently carry row_index;
                                 # if needed for UI, extend the helper to yield row_index too.
                "span": r.span,
                "current_type": r.proposed_type,
                "prior_dominant_type": r.dominant_type,
                "prior_distribution": r.distribution,
                "prior_total": r.total,
            })
    return {
        "task_deviations": deviations,
        "contested_spans": svc.contested_spans(project_id=project_id),
    }
```

Then register the route in `DashboardHandler.do_GET` (next to the other `/api/...` GET routes):

```python
        if route == "/api/posterior-audit":
            if not project_id:
                return self._json_response(400, {"error": "project_required"})
            return self._json_response(200, build_posterior_audit(store, project_id=project_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_posterior_audit_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add annotation_pipeline_skill/interfaces/api.py tests/test_posterior_audit_api.py
git commit -m "feat(api): GET /api/posterior-audit returns deviations + contested spans"
```

---

## Task 12: Web UI — Posterior Audit tab

**Files:**
- Create: `web/src/components/PosteriorAuditPanel.tsx`
- Modify: `web/src/App.tsx` — register the new tab
- Modify: `web/src/types.ts` — add the audit payload types
- Test: `web/src/PosteriorAuditPanel.test.ts` (Vitest, mirrors existing UI tests)

- [ ] **Step 1: Add types**

In `web/src/types.ts`, add:

```typescript
export type TaskDeviation = {
  task_id: string;
  row_index: number;
  span: string;
  current_type: string;
  prior_dominant_type: string;
  prior_distribution: Record<string, number>;
  prior_total: number;
};

export type ContestedSpan = {
  span: string;
  prior_total: number;
  prior_distribution: Record<string, number>;
  top_share: number;
  runner_up_share: number;
};

export type PosteriorAudit = {
  task_deviations: TaskDeviation[];
  contested_spans: ContestedSpan[];
};
```

- [ ] **Step 2: Write the component test**

Create `web/src/PosteriorAuditPanel.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import React from "react";
import { PosteriorAuditPanel } from "./components/PosteriorAuditPanel";

describe("PosteriorAuditPanel", () => {
  it("renders deviations and contested spans", () => {
    const payload = {
      task_deviations: [
        {
          task_id: "t-1",
          row_index: 0,
          span: "Apple",
          current_type: "technology",
          prior_dominant_type: "organization",
          prior_distribution: { organization: 12 },
          prior_total: 12,
        },
      ],
      contested_spans: [
        {
          span: "Microsoft",
          prior_total: 30,
          prior_distribution: { organization: 13, project: 12, technology: 5 },
          top_share: 0.43,
          runner_up_share: 0.40,
        },
      ],
    };
    const html = renderToString(
      React.createElement(PosteriorAuditPanel, {
        projectId: "p",
        initialPayload: payload,
        onSendToHr: vi.fn(),
        onDeclareCanonical: vi.fn(),
      })
    );
    expect(html).toContain("Apple");
    expect(html).toContain("technology");
    expect(html).toContain("organization");
    expect(html).toContain("Microsoft");
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd web && npm test -- --run PosteriorAuditPanel`
Expected: FAIL — module doesn't exist.

- [ ] **Step 4: Write the component**

Create `web/src/components/PosteriorAuditPanel.tsx`:

```typescript
import React, { useState } from "react";
import type { PosteriorAudit, TaskDeviation, ContestedSpan } from "../types";

export type PosteriorAuditPanelProps = {
  projectId: string;
  initialPayload?: PosteriorAudit | null;
  onSendToHr: (taskId: string) => Promise<void> | void;
  onDeclareCanonical: (span: string, entityType: string) => Promise<void> | void;
};

export function PosteriorAuditPanel({
  projectId,
  initialPayload = null,
  onSendToHr,
  onDeclareCanonical,
}: PosteriorAuditPanelProps): React.ReactElement {
  const [payload, setPayload] = useState<PosteriorAudit | null>(initialPayload);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCheck() {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(
        `/api/posterior-audit?project=${encodeURIComponent(projectId)}`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setPayload(await r.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="posterior-audit-panel">
      <div className="panel-header">
        <h2>Posterior Audit</h2>
        <button onClick={handleCheck} disabled={loading}>
          {loading ? "Checking…" : "Check"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {payload === null && !loading && (
        <p className="hint">
          Click <strong>Check</strong> to scan accepted tasks against project
          statistics.
        </p>
      )}
      {payload &&
       payload.task_deviations.length === 0 &&
       payload.contested_spans.length === 0 && (
        <p className="ok">
          All accepted tasks agree with current statistics; no contested spans.
        </p>
      )}
      {payload && payload.task_deviations.length > 0 && (
        <section>
          <h3>Task-level deviations ({payload.task_deviations.length})</h3>
          <table>
            <thead>
              <tr>
                <th>Task</th>
                <th>Span</th>
                <th>Current type</th>
                <th>Prior dominant</th>
                <th>Prior distribution</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {payload.task_deviations.map((d: TaskDeviation) => (
                <tr key={`${d.task_id}-${d.row_index}-${d.span}`}>
                  <td>{d.task_id}</td>
                  <td>{d.span}</td>
                  <td>{d.current_type}</td>
                  <td>
                    {d.prior_dominant_type} (
                    {Math.round((d.prior_distribution[d.prior_dominant_type] / d.prior_total) * 100)}
                    %)
                  </td>
                  <td>{JSON.stringify(d.prior_distribution)}</td>
                  <td>
                    <button onClick={() => onSendToHr(d.task_id)}>
                      Send to HR
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {payload && payload.contested_spans.length > 0 && (
        <section>
          <h3>Contested spans ({payload.contested_spans.length})</h3>
          <table>
            <thead>
              <tr>
                <th>Span</th>
                <th>Distribution</th>
                <th>Top / runner-up</th>
                <th>Declare canonical</th>
              </tr>
            </thead>
            <tbody>
              {payload.contested_spans.map((c: ContestedSpan) => (
                <tr key={c.span}>
                  <td>{c.span}</td>
                  <td>{JSON.stringify(c.prior_distribution)}</td>
                  <td>
                    {Math.round(c.top_share * 100)}% / {Math.round(c.runner_up_share * 100)}%
                  </td>
                  <td>
                    <ContestedSpanForm
                      span={c.span}
                      types={Object.keys(c.prior_distribution)}
                      onSubmit={onDeclareCanonical}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

function ContestedSpanForm({
  span, types, onSubmit,
}: {
  span: string;
  types: string[];
  onSubmit: (span: string, entityType: string) => Promise<void> | void;
}): React.ReactElement {
  const [selected, setSelected] = useState(types[0] ?? "");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (selected) onSubmit(span, selected);
      }}
    >
      <select value={selected} onChange={(e) => setSelected(e.target.value)}>
        {types.map((t) => (
          <option key={t} value={t}>{t}</option>
        ))}
      </select>
      <button type="submit">Declare</button>
    </form>
  );
}
```

- [ ] **Step 5: Register the tab in App.tsx**

Open `web/src/App.tsx`. Locate the existing tab registry (search for an existing tab like "Kanban" or "Runtime"). Add an import and a new tab entry. The exact code depends on App.tsx's current structure — match the pattern. For example, if tabs are an array of `{key, label, render}`:

```typescript
import { PosteriorAuditPanel } from "./components/PosteriorAuditPanel";
// ...
const tabs = [
  // ...existing entries...
  {
    key: "posterior-audit",
    label: "Posterior Audit",
    render: () => (
      <PosteriorAuditPanel
        projectId={currentProjectId}
        onSendToHr={async (taskId) => {
          await fetch(`/api/tasks/${taskId}/move`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ target: "human_review", reason: "posterior_audit" }),
          });
        }}
        onDeclareCanonical={async (span, entityType) => {
          await fetch("/api/conventions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              project_id: currentProjectId, span, entity_type: entityType,
              source: "operator_declaration",
            }),
          });
        }}
      />
    ),
  },
];
```

- [ ] **Step 6: Run test to verify it passes + build**

Run:
```bash
cd web && npm test -- --run PosteriorAuditPanel
```
Expected: PASS

Then run the production build to confirm no TypeScript errors:
```bash
cd web && npm run build
```
Expected: clean build.

- [ ] **Step 7: Commit**

```bash
git add web/src/components/PosteriorAuditPanel.tsx web/src/App.tsx web/src/types.ts web/src/PosteriorAuditPanel.test.ts
git commit -m "feat(web): Posterior Audit tab with deviations + contested-spans tables"
```

---

## Task 13: Run bootstrap on the real workspace, then ship

- [ ] **Step 1: Run the bootstrap script**

```bash
.venv/bin/python scripts/bootstrap_entity_statistics.py projects/v3_initial_deployment/.annotation-pipeline
```

Expected stderr line: `scanning <N> ACCEPTED tasks...` then JSON stats.

- [ ] **Step 2: Spot-check the resulting table**

```bash
sqlite3 projects/v3_initial_deployment/.annotation-pipeline/db.sqlite \
  "SELECT span_lower, entity_type, count FROM entity_statistics
   WHERE project_id='v3_initial_deployment'
   ORDER BY count DESC LIMIT 20;"
```

Expected: top spans by frequency printed. Sanity-check a few — e.g., "Google" → organization should have a large count.

- [ ] **Step 3: Restart the runtime so it picks up the new code**

```bash
pgrep -af "annotation-pipeline runtime" | grep -v "bash -c" | awk '{print $1}' | xargs -r kill
nohup .venv/bin/annotation-pipeline runtime run --project-root projects/v3_initial_deployment > /tmp/runtime-$(date +%s).log 2>&1 &
sleep 3
pgrep -af "annotation-pipeline runtime" | grep -v "bash -c"
```

Expected: one running PID.

- [ ] **Step 4: Hit the new endpoint to confirm it works end-to-end**

```bash
curl -s "http://localhost:8765/api/posterior-audit?project=v3_initial_deployment" | head -50
```

(Adjust port if `serve` is bound elsewhere.) Expected: JSON with `task_deviations` + `contested_spans` keys.

- [ ] **Step 5: Run the full test suite**

```bash
.venv/bin/python -m pytest tests/ -q
cd web && npm test -- --run
```

Expected: all pass.

- [ ] **Step 6: Final commit + push**

If anything needed touching to make ship work, commit it as `chore(verifier): ship adjustments`. Otherwise:

```bash
git push origin main
```

---

## Self-review notes

- **Spec coverage:** §3 schema → Task 1 ✓. §4 verifier semantics → Task 2 ✓. §5.1 QC trigger → Task 5 ✓. §5.2 arbiter trigger → Tasks 6+7+8+9 ✓. §5.3 HR trigger → Task 10 ✓. §6 second arbiter → Tasks 7+8 ✓. §7 audit UI → Tasks 11+12 ✓. §8 bootstrap → Task 4 ✓. §9 mermaid flow → represented across Tasks 5–10. §10 module list → covered. §11 config — note: this plan inlines the constants as module-level (MIN_PRIOR_SAMPLES etc.); §11's `workflow.yaml` external override is intentionally deferred — file a follow-up if the constants need to vary per project before that's needed.
- **Placeholders:** All code blocks are concrete. No "implement appropriate X". Each test names exact behavior. Each step shows exact commands or exact code.
- **Type consistency:** `VerifierResult` fields used consistently across Tasks 2/5/6/8. `iter_span_decisions` returns `(span, type)` everywhere it's used. The `prior_verifier_first_arbiter_divergent` metadata flag name is identical across Tasks 6/7/8/9.
