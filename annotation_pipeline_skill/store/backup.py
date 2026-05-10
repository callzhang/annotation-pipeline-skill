from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def snapshot(db_path: Path, backups_dir: Path, *, now: datetime | None = None) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d-%H%M")
    out = backups_dir / f"sqlite-{timestamp}.sqlite"
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(out)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return out


def prune_snapshots(
    backups_dir: Path,
    *,
    hourly_keep: int,
    daily_keep: int,
    now: datetime | None = None,
) -> list[Path]:
    """Keep the newest `hourly_keep` snapshots, plus one snapshot per day for `daily_keep` days. Delete the rest."""
    now = now or datetime.now(timezone.utc)
    files = sorted(backups_dir.glob("sqlite-*.sqlite"))
    if not files:
        return []

    parsed = []
    for f in files:
        # name format: sqlite-YYYY-MM-DD-HHMM.sqlite
        stem = f.stem.removeprefix("sqlite-")
        try:
            ts = datetime.strptime(stem, "%Y-%m-%d-%H%M")
        except ValueError:
            continue
        parsed.append((ts, f))
    parsed.sort(key=lambda x: x[0], reverse=True)

    keep = set()
    # newest N hourly
    for ts, f in parsed[:hourly_keep]:
        keep.add(f)
    # one per day for last `daily_keep` days
    seen_days = set()
    for ts, f in parsed:
        day = ts.date()
        if day not in seen_days and (now.date() - day).days < daily_keep:
            seen_days.add(day)
            keep.add(f)

    deleted = []
    for ts, f in parsed:
        if f not in keep:
            f.unlink()
            deleted.append(f)
    return deleted
