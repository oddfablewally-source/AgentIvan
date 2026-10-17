"""
Path resolution for the application.

Handles two scenarios:
1. Development mode - paths are relative to the project directory.
2. PyInstaller frozen mode - paths are relative to the bundled executable.

The key insight: when frozen with PyInstaller, `sys._MEIPASS` points to the
temporary extraction directory for onefile builds, or the `_internal` directory
for onedir builds. We use `sys.executable`'s parent directory for user data
so that databases and logs persist across runs.

Configuration and databases go to the Windows user data directory:
    %LOCALAPPDATA%/<AppName>/

Resources (icons, styles) go to the bundle directory in frozen mode.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "AgentIvan"
APP_VERSION = "1.0.0"


def is_frozen() -> bool:
    """Return True if running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def get_bundle_dir() -> Path:
    """
    Return the directory containing bundled resources.

    In frozen mode, this is the directory of the executable (onedir) or
    the _MEIPASS extraction directory (onefile).

    In development mode, this is the project root (parent of 'src').
    """
    if is_frozen():
        # onefile: _MEIPASS is the temp extraction dir
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        # onedir: executable directory
        return Path(sys.executable).parent
    # Development: project root is two levels up from this file
    # src/app/core/paths.py -> ../../.. = project root
    return Path(__file__).resolve().parent.parent.parent.parent


def get_resource_dir() -> Path:
    """Return the directory containing application resources (icons, etc.)."""
    return get_bundle_dir() / "src" / "app" / "resources"


def get_resource(filename: str) -> Path:
    """Return the full path to a resource file."""
    return get_resource_dir() / filename


def _get_user_data_root() -> Path:
    """
    Return the root user data directory.

    On Windows: %LOCALAPPDATA% when set, otherwise %HOME%/AppData/Local.
    On POSIX: $HOME/.local/share (%LOCALAPPDATA% is ignored).

    HOME is read from the environment rather than Path.home() because
    Path.home() ignores HOME on Windows (it uses USERPROFILE), which
    breaks monkeypatched/redirected HOME values in tests and in the
    hermetic `main.py --self-test`. Path.home() remains only as a
    last-resort fallback when HOME is unset.
    """
    home = Path(os.environ["HOME"]) if "HOME" in os.environ else Path.home()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base)
        # Fallback if LOCALAPPDATA is missing
        return home / "AppData" / "Local"
    # Linux/Mac: LOCALAPPDATA is meaningless here
    return home / ".local" / "share"


def get_user_data_dir() -> Path:
    """Return the application-specific user data directory, creating it if needed."""
    path = _get_user_data_root() / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_db_path() -> Path:
    """Return the path to the SQLite database file."""
    return get_user_data_dir() / "app.db"


def get_config_path() -> Path:
    """Return the path to the JSON configuration file."""
    return get_user_data_dir() / "config.json"


def get_log_dir() -> Path:
    """Return the log directory, creating it if needed."""
    path = get_user_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
