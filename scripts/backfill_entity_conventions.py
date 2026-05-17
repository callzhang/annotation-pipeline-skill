"""Backfill entity_conventions from historical ACCEPTED tasks.

Source of truth per task (in order):
  1. human_review_answer artifact (operator authority)
  2. latest annotation_result artifact (post-arbiter or post-QC consensus)

We diff this against the prelabel annotation_result (v2 baseline). For
each entity span whose type changed between prelabel and final, record
a convention via the same service the runtime uses — so conflicts
across tasks auto-resolve to status='disputed' the same way new
decisions do.

Usage:
  python scripts/backfill_entity_conventions.py <project_root>
      walk all accepted tasks, record conventions, print summary
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.runtime.subagent_cycle import _parse_llm_json
from annotation_pipeline_skill.services.entity_convention_service import (
    EntityConventionService,
    extract_entity_type_decisions,
)
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def _load_artifact_payload(store: SqliteStore, artifact) -> Any:
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
        return ans if isinstance(ans, (dict, list)) else None
    text = outer.get("text")
    if not isinstance(text, str):
        return None
    try:
        return _parse_llm_json(_strip_think(text))
    except (json.JSONDecodeError, ValueError):
        return None


def _pick_final_artifact(store: SqliteStore, task_id: str):
    arts = store.list_artifacts(task_id)
    hr = [a for a in arts if a.kind == "human_review_answer"]
    if hr:
        return hr[-1]
    ann = [a for a in arts if a.kind == "annotation_result"]
    return ann[-1] if ann else None


def _pick_prelabel_artifact(store: SqliteStore, task_id: str):
    for a in store.list_artifacts(task_id):
        if a.kind == "annotation_result" and a.metadata.get("provider") == "prelabel":
            return a
    return None


def _arbiter_touched_acceptance(store: SqliteStore, task_id: str) -> bool:
    """True if the task's history shows an arbiter ruling (corrected_annotation
    OR 'annotator-wins') was part of the accepted path. Used to exclude
    arbiter-influenced decisions from the convention dictionary per policy
    (only annotator+QC consensus or operator/HR decisions are eligible).
    """
    for ev in store.list_events(task_id):
        if ev.next_status != TaskStatus.ACCEPTED:
            continue
        reason = (ev.reason or "").lower()
        if "arbiter" in reason:
            return True
    # Also check for arbiter_correction artifacts (a fix that may have been
    # applied without an explicit "arbiter" in the reason).
    for art in store.list_artifacts(task_id):
        if art.kind == "annotation_result" and "arbiter_correction" in art.path:
            return True
        if art.kind == "annotation_result" and art.metadata.get("source") == "arbiter_correction":
            return True
    return False


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", type=Path, help="Project root containing .annotation-pipeline/")
    args = parser.parse_args(argv)

    store = SqliteStore.open(args.project_root / ".annotation-pipeline")
    svc = EntityConventionService(store)

    tasks = list(store.list_tasks_by_status({TaskStatus.ACCEPTED}))
    print(f"scanning {len(tasks)} ACCEPTED tasks...", file=sys.stderr)

    decisions_per_project: Counter = Counter()
    skipped_no_prelabel = 0
    skipped_no_final = 0
    skipped_parse_fail = 0
    skipped_arbiter_touched = 0
    recorded = 0
    errors: list[tuple[str, str]] = []

    for task in tasks:
        # Per policy: only annotator+QC consensus or HR-authored decisions
        # are eligible. Tasks whose path through the pipeline touched the
        # arbiter (annotator-wins or corrected_annotation) are excluded —
        # arbiter is another LLM, not human-level authority.
        if _arbiter_touched_acceptance(store, task.task_id):
            skipped_arbiter_touched += 1
            continue
        final_art = _pick_final_artifact(store, task.task_id)
        if final_art is None:
            skipped_no_final += 1
            continue
        prelabel_art = _pick_prelabel_artifact(store, task.task_id)
        # Final annotation
        final_ann = _load_artifact_payload(store, final_art)
        if final_ann is None:
            skipped_parse_fail += 1
            continue
        prelabel_ann = _load_artifact_payload(store, prelabel_art) if prelabel_art else None
        decisions = extract_entity_type_decisions(prelabel_ann or {}, final_ann)
        if not decisions:
            continue
        # Source label reflects the path: human_review_answer → hr_correction,
        # else qc_consensus (we excluded arbiter-touched tasks above).
        source_label = (
            "hr_correction" if final_art.kind == "human_review_answer" else "qc_consensus"
        )
        for span, entity_type in decisions:
            try:
                svc.record_decision(
                    project_id=task.pipeline_id,
                    span=span,
                    entity_type=entity_type,
                    source=source_label,
                    task_id=task.task_id,
                )
                recorded += 1
                decisions_per_project[task.pipeline_id] += 1
            except Exception as exc:  # noqa: BLE001
                errors.append((task.task_id, str(exc)))

    # Summary
    by_project: dict[str, dict[str, int]] = {}
    for pid in decisions_per_project:
        convs = svc.list_for_project(pid)
        by_project[pid] = {
            "total": len(convs),
            "active": sum(1 for c in convs if c.status == "active"),
            "disputed": sum(1 for c in convs if c.status == "disputed"),
        }
    print(json.dumps({
        "tasks_scanned": len(tasks),
        "decisions_recorded": recorded,
        "skipped_no_final_artifact": skipped_no_final,
        "skipped_parse_failures": skipped_parse_fail,
        "skipped_arbiter_touched": skipped_arbiter_touched,
        "errors": len(errors),
        "by_project": by_project,
    }, indent=2))

    if errors:
        print("\nfirst 5 errors:", file=sys.stderr)
        for tid, e in errors[:5]:
            print(f"  {tid}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
