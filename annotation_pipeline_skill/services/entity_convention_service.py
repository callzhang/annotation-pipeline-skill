"""Per-project entity convention store.

Accumulates "lesson learned" entity-type decisions from QC consensus,
arbiter rulings, HR feedback, and operator declarations. Each subsequent
task gets the matching conventions injected into its annotator/QC/arbiter
prompts so ambiguous spans (Gmail = project, Apple = organization, etc.)
get consistent classification.

Case-insensitive matching, original-case storage. Conflicting decisions
on the same span mark the convention 'disputed' — the runtime no longer
applies it and the operator can settle it manually.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from annotation_pipeline_skill.store.sqlite_store import SqliteStore


@dataclass(frozen=True)
class EntityConvention:
    convention_id: str
    project_id: str
    span_lower: str
    span_original: str
    entity_type: str | None
    status: str   # 'active' or 'disputed'
    evidence_count: int
    proposals: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    created_by: str
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "convention_id": self.convention_id,
            "project_id": self.project_id,
            "span": self.span_original,
            "entity_type": self.entity_type,
            "status": self.status,
            "evidence_count": self.evidence_count,
            "proposals": self.proposals,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "created_by": self.created_by,
            "notes": self.notes,
        }


class EntityConventionService:
    def __init__(self, store: SqliteStore):
        self.store = store

    def record_decision(
        self,
        *,
        project_id: str,
        span: str,
        entity_type: str,
        source: str,
        task_id: str | None = None,
        notes: str | None = None,
    ) -> EntityConvention:
        """Upsert a convention. Rules:
        - first time → insert as 'active'
        - same type re-affirmed → bump evidence_count, append proposal
        - different type → mark 'disputed', append proposal
        - already 'disputed' → just append proposal (do not silently re-activate)
        """
        if not span or not entity_type:
            raise ValueError("span and entity_type are required")
        span_lower = span.strip().lower()
        now = datetime.now(timezone.utc)
        proposal = {
            "type": entity_type,
            "source": source,
            "task_id": task_id,
            "notes": notes,
            "at": now.isoformat(),
        }
        conn = self.store._conn
        row = conn.execute(
            "SELECT * FROM entity_conventions WHERE project_id=? AND span_lower=?",
            (project_id, span_lower),
        ).fetchone()
        if row is None:
            conv_id = f"conv-{uuid4().hex[:16]}"
            conn.execute(
                """
                INSERT INTO entity_conventions
                (convention_id, project_id, span_lower, span_original, entity_type,
                 status, evidence_count, proposals_json, created_at, updated_at,
                 created_by, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conv_id, project_id, span_lower, span.strip(), entity_type,
                    "active", 1, json.dumps([proposal]),
                    now.isoformat(), now.isoformat(), source, notes,
                ),
            )
            return self._load_row(conn.execute(
                "SELECT * FROM entity_conventions WHERE convention_id=?", (conv_id,)
            ).fetchone())

        proposals = json.loads(row["proposals_json"] or "[]")
        proposals.append(proposal)
        new_status = row["status"]
        new_type = row["entity_type"]
        new_count = row["evidence_count"]
        if row["status"] == "disputed":
            # Stay disputed — don't reactivate on new contributions; operator
            # needs to clear status explicitly.
            pass
        elif row["entity_type"] == entity_type:
            new_count = row["evidence_count"] + 1
        else:
            # New type disagrees with existing active convention → dispute.
            new_status = "disputed"
            new_type = None
        conn.execute(
            """
            UPDATE entity_conventions
            SET entity_type=?, status=?, evidence_count=?, proposals_json=?, updated_at=?
            WHERE convention_id=?
            """,
            (new_type, new_status, new_count, json.dumps(proposals),
             now.isoformat(), row["convention_id"]),
        )
        return self._load_row(conn.execute(
            "SELECT * FROM entity_conventions WHERE convention_id=?",
            (row["convention_id"],),
        ).fetchone())

    def clear_dispute(
        self,
        *,
        convention_id: str,
        resolved_type: str,
        actor: str,
        notes: str | None = None,
    ) -> EntityConvention:
        """Operator resolves a disputed convention by picking a winning type."""
        now = datetime.now(timezone.utc)
        conn = self.store._conn
        row = conn.execute(
            "SELECT * FROM entity_conventions WHERE convention_id=?",
            (convention_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"convention {convention_id} not found")
        proposals = json.loads(row["proposals_json"] or "[]")
        proposals.append({
            "type": resolved_type,
            "source": f"dispute_resolved_by:{actor}",
            "notes": notes,
            "at": now.isoformat(),
        })
        conn.execute(
            """
            UPDATE entity_conventions
            SET entity_type=?, status='active', proposals_json=?, updated_at=?
            WHERE convention_id=?
            """,
            (resolved_type, json.dumps(proposals), now.isoformat(), convention_id),
        )
        return self._load_row(conn.execute(
            "SELECT * FROM entity_conventions WHERE convention_id=?",
            (convention_id,),
        ).fetchone())

    def list_for_project(
        self, project_id: str, *, include_disputed: bool = True
    ) -> list[EntityConvention]:
        conn = self.store._conn
        q = "SELECT * FROM entity_conventions WHERE project_id=?"
        params: tuple[Any, ...] = (project_id,)
        if not include_disputed:
            q += " AND status='active'"
        q += " ORDER BY evidence_count DESC, updated_at DESC"
        rows = conn.execute(q, params).fetchall()
        return [self._load_row(r) for r in rows]

    def find_matches_in_text(
        self, project_id: str, text: str
    ) -> list[EntityConvention]:
        """Return active conventions whose span (case-insensitive) appears
        as a substring of ``text``. Disputed conventions are not returned —
        the runtime should not inject contradictory guidance.
        """
        if not text:
            return []
        text_lower = text.lower()
        out: list[EntityConvention] = []
        for conv in self.list_for_project(project_id, include_disputed=False):
            if conv.span_lower and conv.span_lower in text_lower:
                out.append(conv)
        return out

    def _load_row(self, row: sqlite3.Row) -> EntityConvention:
        return EntityConvention(
            convention_id=row["convention_id"],
            project_id=row["project_id"],
            span_lower=row["span_lower"],
            span_original=row["span_original"],
            entity_type=row["entity_type"],
            status=row["status"],
            evidence_count=row["evidence_count"],
            proposals=json.loads(row["proposals_json"] or "[]"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            created_by=row["created_by"],
            notes=row["notes"],
        )


def extract_entity_type_decisions(
    prior_annotation: Any,
    new_annotation: Any,
) -> list[tuple[str, str]]:
    """Walk both annotations and return (span, new_type) for every entity
    whose type differs between prior and new. Used to auto-collect
    conventions when HR submits a correction or arbiter applies a fix.

    Returns spans where:
      - new annotation has the span under type X
      - prior annotation either didn't have the span, or had it under type Y != X
    Json_structures collisions are NOT considered (phrases play multiple
    legitimate roles; type "fixes" there usually aren't meaningful).
    """
    def _index_entities(annotation: Any) -> dict[tuple[int, str], str]:
        # (row_index, span_lower) -> type
        index: dict[tuple[int, str], str] = {}
        if not isinstance(annotation, dict):
            return index
        rows = annotation.get("rows")
        if not isinstance(rows, list):
            return index
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_idx = row.get("row_index") if isinstance(row.get("row_index"), int) else 0
            output = row.get("output")
            if not isinstance(output, dict):
                continue
            entities = output.get("entities")
            if not isinstance(entities, dict):
                continue
            for typ, items in entities.items():
                if not isinstance(items, list):
                    continue
                for s in items:
                    if isinstance(s, str) and s.strip():
                        # First-seen wins per row+span (consistent with within-row dedupe)
                        index.setdefault((row_idx, s.strip().lower()), typ)
        return index

    prior_index = _index_entities(prior_annotation)
    new_index = _index_entities(new_annotation)
    decisions: list[tuple[str, str]] = []
    seen_spans: set[str] = set()
    # Walk new — for any (span, type) that wasn't in prior, or had a different
    # type in prior, record one decision (use the original case from the new
    # annotation by looking it up again).
    if isinstance(new_annotation, dict):
        rows = new_annotation.get("rows", [])
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            row_idx = row.get("row_index") if isinstance(row.get("row_index"), int) else 0
            entities = row.get("output", {}).get("entities") if isinstance(row.get("output"), dict) else None
            if not isinstance(entities, dict):
                continue
            for typ, items in entities.items():
                if not isinstance(items, list):
                    continue
                for s in items:
                    if not isinstance(s, str) or not s.strip():
                        continue
                    key = (row_idx, s.strip().lower())
                    prior_type = prior_index.get(key)
                    if prior_type == typ:
                        continue
                    span_key = s.strip().lower()
                    if span_key in seen_spans:
                        continue
                    seen_spans.add(span_key)
                    decisions.append((s.strip(), typ))
    return decisions
