"""
Architecture boundary tests.

These pin the layering contracts that keep the boilerplate's seams
honest: UI and product code must go through the repositories and the
ChatProvider protocol, never reaching past them into raw database
connections or concrete AI services. Without these, a future edit
could silently reintroduce a bypass (as happened once with
MainWindow._update_message_content) and the suite would stay green.

Scanned as text because Qt modules cannot run in headless test
environments. Each check is deliberately lexical and precise -- no
keyword heuristics with false positives.

Documented exception: SettingsDialog constructs a throwaway
OllamaService to test *unsaved* dialog values; that is the settings
UI's own job, not a seam bypass, so the concrete-service check
applies to product/ only.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_SOURCES = sorted((REPO_ROOT / "src" / "app" / "ui").rglob("*.py"))
PRODUCT_SOURCES = sorted((REPO_ROOT / "src" / "app" / "product").rglob("*.py"))
ENTRY_POINT = [REPO_ROOT / "main.py"]


def _containing(files: list[Path], needle: str) -> list[str]:
    return [
        str(p.relative_to(REPO_ROOT))
        for p in files
        if needle in p.read_text(encoding="utf-8")
    ]


def test_ui_product_and_entry_never_grab_db_connections() -> None:
    """Data access goes through repositories; nothing outside data/
    may pull a raw connection out of the Database."""
    files = UI_SOURCES + PRODUCT_SOURCES + ENTRY_POINT
    assert _containing(files, "get_connection") == []


def test_ui_and_product_never_touch_sqlite3() -> None:
    """sqlite3 lives in data/ alone (stdlib-stable, intentionally
    unwrapped per convention §7.4); no other layer imports it."""
    files = UI_SOURCES + PRODUCT_SOURCES
    assert _containing(files, "import sqlite3") == []
    assert _containing(files, "from sqlite3") == []


def test_product_never_depends_on_concrete_ollama_service() -> None:
    """Product code depends on the ChatProvider protocol (§7.2), not
    on the concrete OllamaService."""
    assert _containing(PRODUCT_SOURCES, "OllamaService") == []
