"""
Utility functions for the application.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone


def _parse_stored_timestamp(ts: str) -> datetime | None:
    """Parse a stored timestamp, interpreting naive values as UTC.

    The database stores UTC (SQLite datetime('now')). Returns an
    aware datetime, or None if the value is unparseable.
    """
    try:
        # SQLite datetime('now') format: 'YYYY-MM-DD HH:MM:SS' (UTC)
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_timestamp(ts: str) -> str:
    """
    Format a database timestamp string for display.

    Stored timestamps are UTC; they are shown in the user's local
    timezone. Unparseable input is returned unchanged.
    """
    if not ts:
        return ""
    dt = _parse_stored_timestamp(ts)
    if dt is None:
        return ts
    return dt.astimezone().strftime("%b %d, %Y %I:%M %p")


def escape_html(text: str) -> str:
    """Escape HTML special characters for safe display."""
    return html.escape(text, quote=True)


def truncate(text: str, max_length: int = 100) -> str:
    """Truncate text to max_length, adding ellipsis if needed."""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."
