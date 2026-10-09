"""
Tests for path resolution (paths.py).

Covers the highest-risk seam in a PyInstaller app: dev vs frozen path
resolution and Windows user-data directory selection. Platform and
interpreter attributes are monkeypatched; LOCALAPPDATA/HOME are
redirected to tmp_path, so no test touches real user data.

Why this matters: path bugs are the classic "works in dev, breaks in the
packaged app" failure, and this module previously had zero coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.app.core.paths import (
    APP_NAME,
    _get_user_data_root,
    get_bundle_dir,
    get_config_path,
    get_db_path,
    get_log_dir,
    get_resource,
    get_resource_dir,
    get_user_data_dir,
    is_frozen,
)


def test_is_frozen_false_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert is_frozen() is False


def test_is_frozen_true_when_attribute_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert is_frozen() is True


def test_bundle_dir_dev_mode_is_project_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In development, the bundle dir is the project root containing src/."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    bundle = get_bundle_dir()
    assert (bundle / "src" / "app" / "core" / "paths.py").is_file()


def test_bundle_dir_frozen_onefile_uses_meipass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PyInstaller onefile extracts to sys._MEIPASS; resources live there."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert get_bundle_dir() == tmp_path


def test_bundle_dir_frozen_onedir_uses_executable_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PyInstaller onedir: resources live next to the executable."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"))
    assert get_bundle_dir() == tmp_path


def test_user_data_root_windows_with_localappdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert _get_user_data_root() == tmp_path


def test_user_data_root_windows_fallback_without_localappdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _get_user_data_root() == tmp_path / "AppData" / "Local"


def test_user_data_root_posix_ignores_localappdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\x\\AppData\\Local")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _get_user_data_root() == tmp_path / ".local" / "share"


def test_user_data_dir_creates_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = get_user_data_dir()
    assert data_dir == tmp_path / APP_NAME
    assert data_dir.is_dir()


def test_db_config_and_log_paths_live_under_user_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert get_db_path() == tmp_path / APP_NAME / "app.db"
    assert get_config_path() == tmp_path / APP_NAME / "config.json"
    log_dir = get_log_dir()
    assert log_dir == tmp_path / APP_NAME / "logs"
    assert log_dir.is_dir()


def test_get_resource_joins_resource_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert get_resource("icon.png") == get_resource_dir() / "icon.png"
    assert get_resource_dir().name == "resources"
