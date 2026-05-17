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
