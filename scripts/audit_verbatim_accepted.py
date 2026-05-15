#!/usr/bin/env python3
"""Scan every ACCEPTED task for verbatim violations in its final annotation,
then route violators back to ARBITRATING so the (newly-fixed) arbiter path
can re-judge them. With the verbatim guard now active in
``_apply_arbiter_correction`` (commit TBD), a re-arbitration that still
produces a non-verbatim correction will route to HUMAN_REVIEW rather than
quietly accepting the bad data.

Background: a 5% sample of 1882 accepted tasks showed ~11% verbatim
violations — the arbiter's ``corrected_annotation`` previously bypassed the
verbatim check that the annotator's output goes through. Audit fix sends
those tasks back through the corrected pipeline.

Usage:
    python scripts/audit_verbatim_accepted.py \\
        --project-root projects/v3_initial_deployment
    python scripts/audit_verbatim_accepted.py \\
        --project-root projects/v3_initial_deployment --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from annotation_pipeline_skill.core.states import TaskStatus
from annotation_pipeline_skill.core.transitions import (
    InvalidTransition,
    transition_task,
)
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_wrapper(text: str) -> str:
    text = _THINK_RE.sub("", text).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1]).strip()
    return text


def _load_final_annotation(store: SqliteStore, task_id: str) -> dict | None:
    """Return the parsed final annotation. Prefers a corrected_annotation
    artifact (marked source=arbiter_correction in metadata) over the plain
    latest annotation_result."""
    artifacts = store.list_artifacts(task_id)
    annotation_artifacts = [a for a in artifacts if a.kind == "annotation_result"]
    if not annotation_artifacts:
        return None
    # The arbiter writes a *new* annotation_result (it doesn't differentiate
    # by kind); the most recent one is the final.
    art = annotation_artifacts[-1]
    path = store.root / art.path
    if not path.exists():
        return None
    outer = json.loads(path.read_text(encoding="utf-8"))
    text = outer.get("text", "") if isinstance(outer, dict) else ""
    if not isinstance(text, str):
        return None
    try:
        return json.loads(_strip_wrapper(text))
    except (json.JSONDecodeError, ValueError):
        return None


def _violations_in(annotation: dict, source_rows: list[dict]) -> int:
    """Count entity / json_structures spans in ``annotation`` that aren't a
    verbatim substring of the corresponding input row's text."""
    input_by_idx: dict[int, str] = {}
    for i, r in enumerate(source_rows):
        if isinstance(r, dict):
            idx = r.get("row_index") if isinstance(r.get("row_index"), int) else i
            text = r.get("input")
            if isinstance(text, str):
                input_by_idx[idx] = text
    rows_out = annotation.get("rows") if isinstance(annotation, dict) else None
    if not isinstance(rows_out, list):
        return 0
    violations = 0
    for r in rows_out:
        if not isinstance(r, dict):
            continue
        idx = r.get("row_index") if isinstance(r.get("row_index"), int) else 0
        text = input_by_idx.get(idx)
        if not text:
            continue
        output = r.get("output")
        if not isinstance(output, dict):
            continue
        for typ_dict_key in ("entities", "json_structures"):
            type_dict = output.get(typ_dict_key)
            if not isinstance(type_dict, dict):
                continue
            for items in type_dict.values():
                if not isinstance(items, list):
                    continue
                for span in items:
                    if isinstance(span, str) and span and span not in text:
                        violations += 1
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report violators without transitioning them.")
    args = parser.parse_args()

    store = SqliteStore.open(args.project_root / ".annotation-pipeline")

    accepted = store.list_tasks_by_status({TaskStatus.ACCEPTED})
    print(f"scanning {len(accepted)} accepted tasks...", file=sys.stderr)

    violators: list[tuple[str, int]] = []
    missing_annotation = 0
    scanned = 0
    for task in accepted:
        scanned += 1
        annotation = _load_final_annotation(store, task.task_id)
        if annotation is None:
            missing_annotation += 1
            continue
        source_rows = task.source_ref.get("payload", {}).get("rows", []) \
            if isinstance(task.source_ref, dict) else []
        v = _violations_in(annotation, source_rows)
        if v:
            violators.append((task.task_id, v))
        if scanned % 200 == 0:
            print(f"  {scanned}/{len(accepted)} (violators so far: {len(violators)})",
                  file=sys.stderr)

    print(
        f"\nscanned: {scanned} | violators: {len(violators)} "
        f"| missing_annotation_artifact: {missing_annotation}",
        file=sys.stderr,
    )

    if args.dry_run or not violators:
        print(json.dumps(
            {"violators": [{"task_id": tid, "violations": n} for tid, n in violators]},
            indent=2,
        ))
        return 0

    moved = 0
    skipped = 0
    for task_id, _ in violators:
        task = store.load_task(task_id)
        try:
            event = transition_task(
                task, TaskStatus.ARBITRATING,
                actor="audit-verbatim-scan",
                reason="audit: verbatim violation in final annotation; routing to arbiter for re-judgment",
                stage="audit",
                metadata={"audit": "verbatim_scan"},
            )
        except InvalidTransition as exc:
            print(f"skip {task_id}: {exc}", file=sys.stderr)
            skipped += 1
            continue
        store.save_task(task)
        store.append_event(event)
        moved += 1

    print(json.dumps(
        {"violators_total": len(violators), "moved_to_arbitrating": moved, "skipped": skipped},
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
