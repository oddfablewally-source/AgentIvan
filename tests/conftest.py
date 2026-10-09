"""
Shared pytest fixtures.

Tests use temporary directories for databases and config files to
ensure full isolation. No test should touch real user data.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.app.core.config import ConfigManager, _env_prefix
from src.app.data.database import Database


@pytest.fixture(autouse=True)
def _isolate_app_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite hermetic against ambient environment variables.

    ConfigManager applies <APP_NAME>_*-prefixed env overrides at load, so
    a developer machine or CI exporting such variables would otherwise
    make config tests environment-dependent and spuriously fail. Strip
    them for every test (restored afterwards); tests that need env vars
    set their own via monkeypatch.
    """
    _strip_prefixed_env_vars(monkeypatch)


def _strip_prefixed_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove <APP_NAME>_*-prefixed variables from the environment."""
    prefix = _env_prefix()
    for name in [key for key in os.environ if key.startswith(prefix)]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Return a temporary database path."""
    return tmp_path / "test.db"


@pytest.fixture
def temp_config_path(tmp_path: Path) -> Path:
    """Return a temporary config file path."""
    return tmp_path / "test_config.json"


@pytest.fixture
def db(temp_db_path: Path) -> Database:
    """Create a fresh isolated database for each test."""
    database = Database(db_path=temp_db_path)
    yield database
    database.close_all()


@pytest.fixture
def config(temp_config_path: Path) -> ConfigManager:
    """Create a fresh isolated config manager for each test."""
    return ConfigManager(config_path=temp_config_path)
