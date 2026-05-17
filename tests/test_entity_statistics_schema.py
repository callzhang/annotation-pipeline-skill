from annotation_pipeline_skill.store.sqlite_store import SqliteStore


def test_entity_statistics_table_exists(tmp_path):
    store = SqliteStore.open(tmp_path)
    cols = [
        r["name"]
        for r in store._conn.execute("PRAGMA table_info(entity_statistics)").fetchall()
    ]
    assert cols == ["project_id", "span_lower", "entity_type", "count", "updated_at"]


def test_entity_statistics_primary_key(tmp_path):
    store = SqliteStore.open(tmp_path)
    now = "2026-05-17T00:00:00+00:00"
    store._conn.execute(
        "INSERT INTO entity_statistics (project_id, span_lower, entity_type, count, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("p", "apple", "organization", 1, now),
    )
    # Same (project_id, span_lower, entity_type) → conflict
    import sqlite3
    try:
        store._conn.execute(
            "INSERT INTO entity_statistics (project_id, span_lower, entity_type, count, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("p", "apple", "organization", 5, now),
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
