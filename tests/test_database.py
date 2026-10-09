"""
Tests for the SQLite database and migration system.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from src.app.data.database import Database, CURRENT_SCHEMA_VERSION


def test_database_creates_tables(db: Database) -> None:
    """Database should create conversations and messages tables on init."""
    conn = db.get_connection()

    # Check conversations table exists
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = [t["name"] for t in tables]

    assert "conversations" in table_names
    assert "messages" in table_names
    assert "app_settings" in table_names
    assert "_meta" in table_names


def test_database_migration_version(db: Database) -> None:
    """Database should be at the current schema version after init."""
    conn = db.get_connection()
    row = conn.execute(
        "SELECT value FROM _meta WHERE key = 'schema_version'"
    ).fetchone()
    assert row is not None
    assert int(row["value"]) == CURRENT_SCHEMA_VERSION


def test_database_wal_mode(db: Database) -> None:
    """Database should be in WAL journal mode."""
    conn = db.get_connection()
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"


def test_database_foreign_keys_enabled(db: Database) -> None:
    """Foreign keys should be enabled."""
    conn = db.get_connection()
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


def test_database_idempotent_migration(db: Database) -> None:
    """Creating a Database on an existing DB should not re-apply migrations."""
    # Close the initial connection
    db.close_all()

    # Re-open
    db2 = Database(db_path=db.db_path)
    conn = db2.get_connection()
    row = conn.execute(
        "SELECT value FROM _meta WHERE key = 'schema_version'"
    ).fetchone()
    assert int(row["value"]) == CURRENT_SCHEMA_VERSION
    db2.close_all()


def test_database_per_thread_connections(db: Database) -> None:
    """Each thread should get its own connection."""
    import threading

    results = {}
    errors = []

    def worker():
        try:
            conn = db.get_connection()
            conn.execute("SELECT 1")
            results[threading.current_thread().name] = "ok"
            # Close this thread's connection before exiting
            db.close_thread_connection()
        except Exception as e:
            errors.append(str(e))

    threads = [
        threading.Thread(target=worker, name=f"t{i}")
        for i in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Thread errors: {errors}"
    assert len(results) == 3


def test_database_corrupt_file_raises_on_init(tmp_path: Path) -> None:
    """A corrupt database file must fail fast at construction with
    sqlite3.DatabaseError -- not hang, and not limp along half-open.

    This documents the data layer's error boundary: construction either
    yields a migrated, usable database or raises. Callers (e.g. the
    composition root) handle this failure at startup.
    """
    bad_path = tmp_path / "corrupt.db"
    bad_path.write_bytes(b"this is not a database file" * 64)

    with pytest.raises(sqlite3.DatabaseError):
        Database(db_path=bad_path)


def test_database_close_all_is_idempotent(db: Database) -> None:
    """Closing twice must be safe -- AppContext.shutdown() depends on it."""
    db.close_all()
    db.close_all()


def test_get_connection_usable_after_close_all(temp_db_path: Path) -> None:
    """close_all() must not poison the Database: the next get_connection()
    returns a fresh, usable connection instead of the closed one."""
    db = Database(db_path=temp_db_path)
    first = db.get_connection()
    assert first.execute("SELECT 1").fetchone() is not None

    db.close_all()

    second = db.get_connection()
    assert second is not first
    assert second.execute("SELECT 1").fetchone() is not None
    db.close_all()


def test_close_all_invalidates_other_threads_cached_connections(
    temp_db_path: Path,
) -> None:
    """A thread that cached its connection before close_all() must get a
    fresh one afterwards -- threading.local() can't be cleared from the
    closing thread, so the generation must invalidate the cache."""
    db = Database(db_path=temp_db_path)
    cached = threading.Event()
    closed = threading.Event()
    done = threading.Event()
    outcome: dict[str, object] = {}

    def worker() -> None:
        first = db.get_connection()
        assert first.execute("SELECT 1").fetchone() is not None
        cached.set()
        assert closed.wait(timeout=10)
        try:
            second = db.get_connection()
            second.execute("SELECT 1").fetchone()
            outcome["fresh"] = second is not first
        except Exception as exc:  # noqa: BLE001 - recorded for the assert
            outcome["error"] = repr(exc)
        finally:
            done.set()

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert cached.wait(timeout=10)
        db.close_all()
        closed.set()
        assert done.wait(timeout=10)
        assert outcome.get("fresh") is True, outcome.get("error")
    finally:
        thread.join(timeout=10)
        db.close_all()


def test_database_upgrades_from_v1_preserving_data(temp_db_path: Path) -> None:
    """Opening an old-version database must run the sequential upgrade
    path, apply every intermediate migration, and preserve user data.

    Fresh-init tests never execute the migration loop, so a broken
    _migration_v2 would previously ship with a green suite and only
    fail on real user upgrades.
    """
    from src.app.data.database import _migration_v1

    # Build a genuine v1 database: the v1 schema plus _meta pinned at
    # version 1, exactly as a database created when v1 was current.
    raw = sqlite3.connect(str(temp_db_path))
    try:
        _migration_v1(raw)
        raw.execute(
            "CREATE TABLE IF NOT EXISTS _meta ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        raw.execute(
            "INSERT INTO _meta (key, value) VALUES ('schema_version', '1')"
        )
        raw.execute("INSERT INTO conversations (title) VALUES ('Old chat')")
        conv_id = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
        raw.execute(
            "INSERT INTO messages (conversation_id, role, content)"
            " VALUES (?, 'user', 'hello from v1')",
            (conv_id,),
        )
        raw.commit()
    finally:
        raw.close()

    # Opening it must trigger the upgrade path (not a fresh init).
    db = Database(db_path=temp_db_path)
    try:
        conn = db.get_connection()
        version = conn.execute(
            "SELECT value FROM _meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        assert int(version) == CURRENT_SCHEMA_VERSION

        # The v2 schema change was applied.
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "app_settings" in tables

        # Pre-existing user data survived the upgrade.
        row = conn.execute(
            "SELECT title FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()
        assert row[0] == "Old chat"
        msg = conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ?",
            (conv_id,),
        ).fetchone()
        assert msg[0] == "hello from v1"
    finally:
        db.close_all()
