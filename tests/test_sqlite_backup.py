from datetime import datetime, timedelta
from pathlib import Path

from annotation_pipeline_skill.store.backup import prune_snapshots, snapshot
from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_snapshot_creates_a_copy_of_db(tmp_path):
    store = SqliteStore.open(tmp_path)
    store.close()
    backups_dir = tmp_path / "backups"

    out = snapshot(tmp_path / "db.sqlite", backups_dir, now=datetime(2026, 5, 10, 12, 0))

    assert out.exists()
    assert out.parent == backups_dir
    assert out.name.startswith("sqlite-2026-05-10-1200")


def test_prune_keeps_hourly_and_daily(tmp_path):
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    base = datetime(2026, 5, 10, 12, 0)
    files = []
    for h in range(48):
        ts = base - timedelta(hours=h)
        f = backups_dir / f"sqlite-{ts:%Y-%m-%d-%H%M}.sqlite"
        f.write_bytes(b"")
        files.append(f)

    prune_snapshots(backups_dir, hourly_keep=24, daily_keep=7, now=base)

    remaining = sorted(p.name for p in backups_dir.iterdir())
    assert len(remaining) <= 24 + 7
    # 24 newest hourly always kept
    for h in range(24):
        ts = base - timedelta(hours=h)
        expected = f"sqlite-{ts:%Y-%m-%d-%H%M}.sqlite"
        assert expected in remaining
