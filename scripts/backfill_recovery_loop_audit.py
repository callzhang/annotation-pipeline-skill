"""One-shot backfill: prune spurious recovery-loop audit_events.

Background: a bug in local_scheduler._try_claim_task (fixed in commit
following) bounced live in-flight ANNOTATING tasks back to PENDING on every
claim attempt because the lease guard was missing. Each cycle wrote two
audit_events:

  pending     → annotating  (stage=annotation, "subagent runtime started annotation")
  annotating  → pending     (stage=recovery,  "resume on restart: no annotation artifact yet...")

Neither led to real work — the worker bounced before the LLM call. This
script removes both halves of every paired cycle so the audit log
reflects only events that did something.

Usage:
    python scripts/backfill_recovery_loop_audit.py <project-root>
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


SPURIOUS_RECOVERY_REASON = "resume on restart: no annotation artifact yet"


def prune(project_root: Path) -> dict[str, int]:
    db_path = project_root / ".annotation-pipeline" / "db.sqlite"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Find spurious recovery events.
        recovery_rows = conn.execute(
            """
            SELECT event_id, task_id, seq FROM audit_events
            WHERE stage = 'recovery' AND reason LIKE ? || '%'
            """,
            (SPURIOUS_RECOVERY_REASON,),
        ).fetchall()

        recovery_ids: list[str] = [r["event_id"] for r in recovery_rows]
        # The paired start event has seq = recovery.seq - 1 for the same task.
        paired_ids: list[str] = []
        for r in recovery_rows:
            row = conn.execute(
                """
                SELECT event_id FROM audit_events
                WHERE task_id = ? AND seq = ?
                  AND stage = 'annotation'
                  AND previous_status = 'pending' AND next_status = 'annotating'
                  AND reason = 'subagent runtime started annotation'
                """,
                (r["task_id"], r["seq"] - 1),
            ).fetchone()
            if row is not None:
                paired_ids.append(row["event_id"])

        before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        # Delete in chunks to keep parameter list manageable.
        def delete_in_chunks(ids: list[str]) -> int:
            removed = 0
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                placeholders = ",".join("?" * len(chunk))
                cur = conn.execute(
                    f"DELETE FROM audit_events WHERE event_id IN ({placeholders})",
                    chunk,
                )
                removed += cur.rowcount
            return removed

        deleted_recovery = delete_in_chunks(recovery_ids)
        deleted_paired = delete_in_chunks(paired_ids)
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        return {
            "before": before,
            "after": after,
            "deleted_recovery": deleted_recovery,
            "deleted_paired_starts": deleted_paired,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    project_root = Path(sys.argv[1]).resolve()
    if not (project_root / ".annotation-pipeline" / "db.sqlite").exists():
        print(f"no db.sqlite under {project_root}/.annotation-pipeline")
        sys.exit(1)
    result = prune(project_root)
    import json
    print(json.dumps(result, indent=2))
