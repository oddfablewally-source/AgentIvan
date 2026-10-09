"""
Tests for utility functions.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.app.utils.helpers import escape_html, format_timestamp, truncate


def test_format_timestamp_valid() -> None:
    """Should format a SQLite timestamp correctly."""
    result = format_timestamp("2024-01-15 14:30:00")
    assert "Jan" in result
    assert "2024" in result


def test_format_timestamp_empty() -> None:
    """Should return empty string for empty input."""
    assert format_timestamp("") == ""


def test_format_timestamp_invalid() -> None:
    """Should return the original string for unparseable input."""
    assert format_timestamp("not a date") == "not a date"


def test_escape_html() -> None:
    """Should escape HTML special characters."""
    assert escape_html("<script>alert('xss')</script>") == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"


def test_truncate_short() -> None:
    """Should return text unchanged if under max length."""
    assert truncate("short text", 100) == "short text"


def test_truncate_long() -> None:
    """Should truncate and add ellipsis."""
    long_text = "a" * 150
    result = truncate(long_text, 50)
    assert len(result) == 50
    assert result.endswith("...")


def test_format_timestamp_converts_utc_to_local() -> None:
    """Stored timestamps are UTC; display must be local time.

    SQLite's datetime('now') is UTC. Formatting it with no conversion
    silently shows every message timestamp shifted by the UTC offset.
    """
    utc = datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc)
    expected = utc.astimezone().strftime("%b %d, %Y %I:%M %p")
    assert format_timestamp("2024-01-15 14:30:00") == expected


def test_format_timestamp_iso_offset_converted_to_local() -> None:
    """ISO timestamps with an explicit offset convert via UTC to local."""
    # 14:30 at +02:00 is 12:30 UTC.
    utc = datetime(2024, 6, 1, 12, 30, tzinfo=timezone.utc)
    expected = utc.astimezone().strftime("%b %d, %Y %I:%M %p")
    assert format_timestamp("2024-06-01T14:30:00+02:00") == expected


def test_format_timestamp_naive_iso_assumed_utc() -> None:
    """Naive ISO timestamps are stored UTC, so display converts them."""
    utc = datetime(2024, 6, 1, 12, 30, tzinfo=timezone.utc)
    expected = utc.astimezone().strftime("%b %d, %Y %I:%M %p")
    assert format_timestamp("2024-06-01T12:30:00") == expected
