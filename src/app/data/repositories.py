"""
Data access objects for conversations and messages.

These are simple repository classes that wrap SQL operations.
No ORM — just plain sqlite3 with parameterized queries.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.app.data.database import Database


@dataclass
class Conversation:
    """A conversation record."""
    id: Optional[int] = None
    title: str = "New Conversation"
    created_at: str = ""
    updated_at: str = ""
    metadata: str = ""


@dataclass
class Message:
    """A message record."""
    id: Optional[int] = None
    conversation_id: int = 0
    role: str = ""  # 'user', 'assistant', 'system'
    content: str = ""
    created_at: str = ""


class ConversationRepository:
    """Data access for conversations table."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, title: str = "New Conversation", metadata: str = "") -> Conversation:
        """Insert a new conversation and return it."""
        conn = self.db.get_connection()
        with conn:
            cursor = conn.execute(
                "INSERT INTO conversations (title, metadata) VALUES (?, ?)",
                (title, metadata),
            )
            conv_id = cursor.lastrowid
        # Fetch the full record
        return self.get_by_id(conv_id)

    def get_by_id(self, conv_id: int) -> Optional[Conversation]:
        """Get a conversation by ID."""
        conn = self.db.get_connection()
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?",
            (conv_id,),
        ).fetchone()
        if row is None:
            return None
        return Conversation(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=row["metadata"],
        )

    def list_all(self) -> list[Conversation]:
        """List all conversations, newest first."""
        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT * FROM conversations ORDER BY datetime(updated_at) DESC, id DESC"
        ).fetchall()
        return [
            Conversation(
                id=r["id"],
                title=r["title"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                metadata=r["metadata"],
            )
            for r in rows
        ]

    def update_title(self, conv_id: int, title: str) -> None:
        """Update a conversation's title."""
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = datetime('now') WHERE id = ?",
                (title, conv_id),
            )

    def update_metadata(self, conv_id: int, metadata: str) -> None:
        """Update a conversation's metadata (JSON string)."""
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                "UPDATE conversations SET metadata = ?, updated_at = datetime('now') WHERE id = ?",
                (metadata, conv_id),
            )

    def touch(self, conv_id: int) -> None:
        """Update the conversation's updated_at timestamp."""
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
                (conv_id,),
            )

    def delete(self, conv_id: int) -> None:
        """Delete a conversation (cascades to messages)."""
        conn = self.db.get_connection()
        with conn:
            conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))

    def count(self) -> int:
        """Return total number of conversations."""
        conn = self.db.get_connection()
        row = conn.execute("SELECT COUNT(*) as cnt FROM conversations").fetchone()
        return row["cnt"]


class MessageRepository:
    """Data access for messages table."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, conversation_id: int, role: str, content: str) -> Message:
        """Insert a message and return it."""
        conn = self.db.get_connection()
        with conn:
            cursor = conn.execute(
                "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
                (conversation_id, role, content),
            )
            msg_id = cursor.lastrowid
            # Touch the conversation's updated_at
            conn.execute(
                "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
                (conversation_id,),
            )
        return self.get_by_id(msg_id)

    def update_content(self, msg_id: int, content: str) -> None:
        """Update a message's content (e.g. with the streamed response)."""
        conn = self.db.get_connection()
        with conn:
            conn.execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                (content, msg_id),
            )

    def get_by_id(self, msg_id: int) -> Optional[Message]:
        """Get a message by ID."""
        conn = self.db.get_connection()
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?",
            (msg_id,),
        ).fetchone()
        if row is None:
            return None
        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
        )

    def list_by_conversation(self, conversation_id: int) -> list[Message]:
        """List all messages in a conversation, oldest first.

        created_at has second precision (datetime('now')), and the UI
        inserts user/assistant rows back-to-back, so ties are the norm,
        not the exception. id is monotonic with insertion order, giving
        a deterministic total order.
        """
        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? "
            "ORDER BY datetime(created_at) ASC, id ASC",
            (conversation_id,),
        ).fetchall()
        return [
            Message(
                id=r["id"],
                conversation_id=r["conversation_id"],
                role=r["role"],
                content=r["content"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def count(self) -> int:
        """Return total number of messages."""
        conn = self.db.get_connection()
        row = conn.execute("SELECT COUNT(*) as cnt FROM messages").fetchone()
        return row["cnt"]
