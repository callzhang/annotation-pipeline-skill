"""Audit exported training data for quality issues.

Three categories surfaced:
  1. duplicate-same-type: same span listed twice under the same entity /
     json_structures type. Always a data-cleanup miss; safe to dedupe.
  2. non-verbatim: span string isn't a substring of the row's input.text.
     The runtime verbatim guard should catch these at acceptance time, but
     legacy artifacts (prelabel imports, pre-guard accepts) can carry them
     into the export. Needs human review.
  3. cross-type-collision: same entity span appears under two different
     entity types in one row (e.g., "Google" tagged both organization AND
     technology). Schema permits but the model likely confused itself.
     Needs human review.

Usage:
  python scripts/audit_export_quality.py <export_dir>
      report counts + per-task issue list

  python scripts/audit_export_quality.py <export_dir> --fix-duplicates
      additionally rewrite the export jsonl in place, deduping
      same-type duplicates. Other issue categories are reported but
      not modified.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _scan_row(
    row: dict,
    input_text: str,
    task_id: str,
    issues: dict[str, list],
) -> tuple[int, dict]:
    """Return (number_of_duplicates_removed, cleaned_row)."""
    cleaned_row = json.loads(json.dumps(row))  # deep copy
    output = cleaned_row.get("output", {})
    if not isinstance(output, dict):
        return 0, cleaned_row
    duplicates_removed = 0
    row_idx = cleaned_row.get("row_index")
    for field_key in ("entities", "json_structures"):
        field = output.get(field_key, {})
        if not isinstance(field, dict):
            continue
        # cross-type tracking only for entities
        seen_in_type: dict[str, str] = {}
        for typ, items in list(field.items()):
            if not isinstance(items, list):
                continue
            seen: set[str] = set()
            deduped: list[Any] = []
            for s in items:
                if not isinstance(s, str):
                    deduped.append(s)
                    continue
                if s in seen:
                    duplicates_removed += 1
                    issues["dup"].append({
                        "task_id": task_id, "row_index": row_idx,
                        "field": f"{field_key}.{typ}", "span": s,
                    })
                    continue
                seen.add(s)
                deduped.append(s)
                if input_text and s and s not in input_text:
                    issues["non_verbatim"].append({
                        "task_id": task_id, "row_index": row_idx,
                        "field": f"{field_key}.{typ}", "span": s,
                    })
                if field_key == "entities":
                    if s in seen_in_type and seen_in_type[s] != typ:
                        issues["cross_type"].append({
                            "task_id": task_id, "row_index": row_idx,
                            "span": s, "types": [seen_in_type[s], typ],
                        })
                    else:
                        seen_in_type[s] = typ
            field[typ] = deduped
    return duplicates_removed, cleaned_row


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_dir", type=Path, help="Path to export-* directory containing training_data.jsonl")
    parser.add_argument("--fix-duplicates", action="store_true", help="Rewrite jsonl in place with duplicates removed")
    parser.add_argument("--out", type=Path, default=None, help="Write detailed issue list to this JSON file (default: stdout summary only)")
    args = parser.parse_args(argv)

    jsonl_path = args.export_dir / "training_data.jsonl"
    if not jsonl_path.exists():
        print(f"error: {jsonl_path} not found", file=sys.stderr)
        return 1

    issues: dict[str, list] = {"dup": [], "non_verbatim": [], "cross_type": []}
    total_tasks = 0
    total_rows = 0
    total_spans = 0
    total_dup_removed = 0
    rewritten_lines: list[str] = []

    with jsonl_path.open() as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            rec = json.loads(line)
            total_tasks += 1
            ann_str = rec.get("annotation")
            if not ann_str:
                rewritten_lines.append(line)
                continue
            try:
                ann = json.loads(ann_str) if isinstance(ann_str, str) else ann_str
            except json.JSONDecodeError:
                rewritten_lines.append(line)
                continue
            src = rec.get("source_ref", {}).get("payload", {})
            src_rows = {r["row_index"]: r.get("input", "") for r in src.get("rows", []) if isinstance(r, dict) and "row_index" in r}

            new_rows = []
            for row in ann.get("rows", []):
                total_rows += 1
                if isinstance(row, dict):
                    input_text = src_rows.get(row.get("row_index"), "")
                    removed, cleaned_row = _scan_row(row, input_text, rec["task_id"], issues)
                    total_dup_removed += removed
                    new_rows.append(cleaned_row)
                    # count spans
                    for fk in ("entities", "json_structures"):
                        f2 = cleaned_row.get("output", {}).get(fk, {})
                        if isinstance(f2, dict):
                            for v in f2.values():
                                if isinstance(v, list):
                                    total_spans += sum(1 for x in v if isinstance(x, str))
                else:
                    new_rows.append(row)
            ann["rows"] = new_rows
            rec["annotation"] = json.dumps(ann, ensure_ascii=False)
            rewritten_lines.append(json.dumps(rec, ensure_ascii=False))

    affected_tasks = (
        {i["task_id"] for i in issues["dup"]}
        | {i["task_id"] for i in issues["non_verbatim"]}
        | {i["task_id"] for i in issues["cross_type"]}
    )
    print(json.dumps({
        "export_dir": str(args.export_dir),
        "tasks_scanned": total_tasks,
        "rows_scanned": total_rows,
        "spans_scanned": total_spans,
        "duplicates_same_type": len(issues["dup"]),
        "non_verbatim": len(issues["non_verbatim"]),
        "cross_type_collisions": len(issues["cross_type"]),
        "affected_tasks": len(affected_tasks),
        "non_verbatim_top_categories": Counter(i["field"] for i in issues["non_verbatim"]).most_common(10),
        "cross_type_top_pairs": Counter(tuple(sorted(i["types"])) for i in issues["cross_type"]).most_common(10),
    }, indent=2, ensure_ascii=False))

    if args.out:
        args.out.write_text(json.dumps(issues, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote detailed issue list to {args.out}", file=sys.stderr)

    if args.fix_duplicates and total_dup_removed > 0:
        jsonl_path.write_text("\n".join(rewritten_lines) + "\n", encoding="utf-8")
        print(f"\nrewrote {jsonl_path} with {total_dup_removed} duplicate span(s) removed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
