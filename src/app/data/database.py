"""
SQLite database manager with per-thread connections and migrations.

Key design decisions based on best practices:
- WAL mode for concurrent read/write access.
- One connection per thread (Python's sqlite3 default: check_same_thread=True).
- PRAGMA foreign_keys=ON on every connection.
- PRAGMA synchronous=NORMAL (safe with WAL, better performance).
- Simple versioned migration system: each migration is a function that
  applies schema changes. The DB version is tracked in a meta table.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from src.app.core.paths import get_db_path
from src.app.core.logging_setup import get_logger

logger = get_logger("database")

CURRENT_SCHEMA_VERSION = 2


class Database:
    """
    Manages SQLite database connections and schema migrations.

    Each thread gets its own connection via threading.local().
    This is the recommended pattern for SQLite in multi-threaded Python apps.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or get_db_path()
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        # RLock: get_connection() holds it across _create_connection().
        self._lock = threading.RLock()
        # Bumped by close_all() so per-thread caches from before the close
        # are never handed out again (threading.local can't be cleared
        # from another thread).
        self._generation = 0

        # Initialize schema on creation
        self._migrate()

    def _create_connection(self) -> sqlite3.Connection:
        """Create a new SQLite connection configured for this app."""
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=True,  # Enforce per-thread usage
            timeout=30.0,  # Wait up to 30s for locks
        )
        conn.row_factory = sqlite3.Row  # Access columns by name
        # PRAGMAs must be set per-connection
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")

        with self._lock:
            self._connections.append(conn)

        return conn

    def get_connection(self) -> sqlite3.Connection:
        """
        Get the current thread's database connection.

        Always returns a usable connection: one is created for threads
        that don't have one yet, and a fresh one replaces any cached
        connection invalidated by a previous close_all().
        """
        with self._lock:
            conn = getattr(self._local, "conn", None)
            if (
                conn is None
                or getattr(self._local, "generation", -1) != self._generation
            ):
                conn = self._create_connection()
                self._local.conn = conn
                self._local.generation = self._generation
            return conn

    def _migrate(self) -> None:
        """Run database migrations to bring schema up to the current version."""
        # Use a dedicated connection for migration
        conn = self._create_connection()
        try:
            # Ensure meta table exists
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS _meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.commit()

            # Get current version
            row = conn.execute(
                "SELECT value FROM _meta WHERE key = 'schema_version'"
            ).fetchone()
            current = int(row[0]) if row else 0

            if current >= CURRENT_SCHEMA_VERSION:
                logger.info("Database schema is up to date (v%d).", current)
                return

            logger.info(
                "Migrating database from v%d to v%d.",
                current,
                CURRENT_SCHEMA_VERSION,
            )

            # Apply migrations sequentially
            for version in range(current + 1, CURRENT_SCHEMA_VERSION + 1):
                migration = _MIGRATIONS.get(version)
                if migration:
                    migration(conn)
                    conn.execute(
                        "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)",
                        ("schema_version", str(version)),
                    )
                    conn.commit()
                    logger.info("Applied migration v%d.", version)

            logger.info("Database migration complete.")
        finally:
            conn.close()
            with self._lock:
                if conn in self._connections:
                    self._connections.remove(conn)

    def close_all(self) -> None:
        """Close all open database connections.

        The Database remains usable afterwards: the next
        get_connection() on any thread transparently opens a fresh
        connection instead of returning a closed one.
        """
        with self._lock:
            for conn in self._connections:
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()
            self._generation += 1

    def close_thread_connection(self) -> None:
        """Close the current thread's connection."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None
            with self._lock:
                if conn in self._connections:
                    self._connections.remove(conn)


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

def _migration_v1(conn: sqlite3.Connection) -> None:
    """
    Initial schema: conversations and messages tables.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL DEFAULT 'New Conversation',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            metadata   TEXT  -- JSON blob for extensible metadata
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role            TEXT NOT NULL,  -- 'user', 'assistant', 'system'
            content         TEXT NOT NULL,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_conversation "
        "ON messages(conversation_id)"
    )


def _migration_v2(conn: sqlite3.Connection) -> None:
    """
    Add application settings table for storing key-value preferences in DB.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


# Migration registry: version -> function
_MIGRATIONS: dict[int, callable] = {
    1: _migration_v1,
    2: _migration_v2,
}
