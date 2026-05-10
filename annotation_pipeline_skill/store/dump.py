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
