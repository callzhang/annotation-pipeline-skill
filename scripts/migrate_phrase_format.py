#!/usr/bin/env python3
"""Migrate stored annotation artifacts to the bare-string phrase format.

V3 originally stored json_structures phrases as {"text", "start", "end"}
objects. The format was simplified to bare verbatim strings (entities use the
same shape) and a pipeline-level verbatim substring check now enforces
correctness. This one-shot migration walks every annotation_result and
arbiter_correction artifact under a project root, strips any leading
<think>...</think> reasoning blocks (a pre-fix legacy concern with minimax /
codex outputs), parses the inner JSON, converts phrase objects to strings, and
writes the cleaned form back.

The script is idempotent — running it a second time leaves clean files alone.

Usage:
    python scripts/migrate_phrase_format.py --root projects/v3_initial_deployment/.annotation-pipeline
    python scripts/migrate_phrase_format.py --root projects/v3_initial_deployment/.annotation-pipeline --dry-run

The script does not touch the SQLite DB itself — feedback / discussion
messages may still reference old offsets in their text but those are
informational, not structural.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def strip_fence(text: str) -> str:
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        return s
    opening = lines[0].strip().lower()
    if opening not in {"```", "```json"}:
        return s
    return "\n".join(lines[1:-1]).strip()


def normalize_phrases(value, stats: dict) -> object:
    """Recursively walk a parsed annotation payload, converting phrase
    objects (dicts with a 'text' field) inside json_structures lists to
    bare strings. Returns the (possibly mutated) value."""
    if isinstance(value, dict):
        # Detect json_structures dict: every entry is a list of {text, ...}
        # objects. We don't strictly check; we just convert any list entries
        # under 'json_structures' that look like {text} dicts.
        js = value.get("json_structures")
        if isinstance(js, dict):
            for k, items in list(js.items()):
                if not isinstance(items, list):
                    continue
                new_items = []
                for item in items:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        new_items.append(item["text"])
                        stats["phrases_migrated"] += 1
                    elif isinstance(item, str):
                        new_items.append(item)
                    else:
                        # Drop malformed entries silently.
                        stats["phrases_dropped"] += 1
                js[k] = new_items
        for v in value.values():
            normalize_phrases(v, stats)
    elif isinstance(value, list):
        for v in value:
            normalize_phrases(v, stats)
    return value


def migrate_file(path: Path, *, dry_run: bool, stats: dict) -> bool:
    """Return True when the file was rewritten."""
    try:
        outer = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        stats["files_unparseable"] += 1
        return False
    if not isinstance(outer, dict):
        return False
    text = outer.get("text")
    if not isinstance(text, str):
        return False
    cleaned = strip_fence(strip_think(text))
    if not cleaned:
        return False
    try:
        inner = json.loads(cleaned)
    except json.JSONDecodeError:
        # Some annotation artifacts are pre-parse failures (e.g. truncated
        # output). Leave them alone.
        stats["files_inner_unparseable"] += 1
        return False
    before = json.dumps(inner, sort_keys=True)
    normalize_phrases(inner, stats)
    after = json.dumps(inner, sort_keys=True)
    if before == after and cleaned == text:
        stats["files_unchanged"] += 1
        return False
    outer["text"] = json.dumps(inner, sort_keys=True)
    stats["files_rewritten"] += 1
    if dry_run:
        return True
    path.write_text(json.dumps(outer, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="project's .annotation-pipeline directory")
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing files")
    args = parser.parse_args()
    artifact_root = args.root / "artifact_payloads"
    if not artifact_root.exists():
        raise SystemExit(f"No artifact_payloads dir under {args.root}")
    stats = {
        "files_seen": 0,
        "files_rewritten": 0,
        "files_unchanged": 0,
        "files_unparseable": 0,
        "files_inner_unparseable": 0,
        "phrases_migrated": 0,
        "phrases_dropped": 0,
    }
    targets = [
        *artifact_root.rglob("*_annotation_result.json"),
        *artifact_root.rglob("*_arbiter_correction.json"),
        *artifact_root.rglob("prelabeled-annotation.json"),
    ]
    for path in targets:
        stats["files_seen"] += 1
        migrate_file(path, dry_run=args.dry_run, stats=stats)
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
