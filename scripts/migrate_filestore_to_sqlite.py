"""One-shot FileStore -> SqliteStore migration.

Usage:
    python scripts/migrate_filestore_to_sqlite.py --src <old-root> --dst <new-root>

Idempotency: refuses to run if target already has tasks unless --force is given.

Note: --force only bypasses the emptiness check. It does NOT wipe the target.
Re-running against a target that already contains audit data for the same task
IDs will fail with IntegrityError on append tables (audit_events, attempts,
feedback_records, feedback_discussions, artifact_refs). For a clean re-run,
delete the target directory and re-invoke.
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

    src_resolved = src.resolve()
    dst_resolved = dst.resolve()
    if (
        src_resolved == dst_resolved
        or src_resolved in dst_resolved.parents
        or dst_resolved in src_resolved.parents
    ):
        raise RuntimeError(
            f"src ({src_resolved}) and dst ({dst_resolved}) must be disjoint "
            f"directories; one cannot be inside the other"
        )

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
                        help="bypass the non-empty-target check (does NOT wipe target; "
                             "append tables will fail loudly on duplicate keys)")
    args = parser.parse_args(argv)

    report = migrate(args.src, args.dst,
                     archive_genesis=not args.no_archive, force=args.force)
    for k, v in report.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    sys.exit(main())
