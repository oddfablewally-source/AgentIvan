"""
Tests for application identity centralization.

The product rename flow is: update APP_NAME / APP_VERSION in
src/app/core/paths.py. Every user-visible surface must derive from
those constants -- a hardcoded copy means a renamed app ships
half-branded. These tests pin that invariant without needing a
display (Qt modules are scanned as text, since no Qt platform
libraries are available in headless environments).
"""

from __future__ import annotations

from pathlib import Path

from src.app.core import paths

REPO_ROOT = Path(__file__).resolve().parent.parent
PATHS_PY = REPO_ROOT / "src" / "app" / "core" / "paths.py"
APP_SOURCES = [
    p
    for p in [REPO_ROOT / "main.py", *sorted((REPO_ROOT / "src" / "app").rglob("*.py"))]
    # paths.py is the single source of truth and defines the literals;
    # tests/ never appears here, but the guard documents the intent.
    if p != PATHS_PY and "tests" not in p.parts
]


def test_app_identity_constants_sane() -> None:
    """APP_NAME / APP_VERSION are the single source of identity."""
    assert isinstance(paths.APP_NAME, str) and paths.APP_NAME.strip()
    assert isinstance(paths.APP_VERSION, str) and paths.APP_VERSION.strip()


def _files_containing(literal: str) -> list[str]:
    return [
        str(p.relative_to(REPO_ROOT))
        for p in APP_SOURCES
        if literal in p.read_text(encoding="utf-8")
    ]


def test_no_hardcoded_app_name() -> None:
    """The display name must come from APP_NAME, not a copied literal."""
    assert _files_containing('"LocalAI Desktop"') == []
    assert _files_containing("'LocalAI Desktop'") == []


def test_no_hardcoded_app_version() -> None:
    """The version must come from APP_VERSION, not a copied literal."""
    assert _files_containing('"1.0.0"') == []
    assert _files_containing("'1.0.0'") == []
