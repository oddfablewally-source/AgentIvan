"""
Persistent application configuration.

Stores settings in a JSON file in the Windows user data directory.
Provides a simple key-value interface with type-safe getters/setters
and a change notification mechanism for the GUI.

Configuration sources, in precedence order (highest wins):
1. Environment variables: <APP_NAME-derived prefix><KEY>, e.g.
   LOCALAI_DESKTOP_OLLAMA_HOST. Only keys already present in
   DEFAULT_CONFIG can be overridden this way; anything else in the
   environment is ignored. Values are coerced to the type of the
   default (bool/int/float/str); unparseable values are ignored with
   a warning.
2. The JSON config file.
3. DEFAULT_CONFIG.

The prefix derives from APP_NAME in paths.py, so renaming the
application keeps the variable names consistent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from src.app.core.paths import APP_NAME, get_config_path
from src.app.core.logging_setup import get_logger

logger = get_logger("config")

# Default configuration values
DEFAULT_CONFIG: dict[str, Any] = {
    "ollama_host": "http://localhost:11434",
    "ollama_model": "llama3.2",
    "ollama_timeout": 120,  # seconds
    "theme": "dark",
    "window_width": 900,
    "window_height": 600,
    "system_tray": True,
    "system_prompt": "You are a helpful assistant.",
}


def _env_prefix() -> str:
    """Derive the env-var prefix from APP_NAME, e.g. 'LOCALAI_DESKTOP_'."""
    return "".join(c if c.isalnum() else "_" for c in APP_NAME.upper()) + "_"


def _coerce_env_value(raw: str, default: Any) -> Any:
    """Coerce an env-var string to the type of the default value."""
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


def apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """
    Return a copy of `data` with environment-variable overrides applied.

    Only keys already present in DEFAULT_CONFIG are eligible; any other
    <prefix>* variable is ignored. Unparseable values are ignored with
    a warning, leaving the file/default value in place.
    """
    result = dict(data)
    prefix = _env_prefix()
    for key, default in DEFAULT_CONFIG.items():
        name = prefix + key.upper()
        raw = os.environ.get(name)
        if raw is None:
            continue
        try:
            result[key] = _coerce_env_value(raw, default)
            logger.debug("Config override from environment: %s", name)
        except ValueError:
            logger.warning("Ignoring invalid %s=%r", name, raw)
    return result


class ConfigManager(QObject):
    """
    Manages persistent application configuration.

    - Loads from JSON file on startup.
    - Saves to JSON file on change.
    - Emits a Qt signal when configuration changes, so the GUI can react.
    """

    config_changed = Signal(str, object)  # (key, new_value)

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self._path = config_path or get_config_path()
        self._data: dict[str, Any] = {}
        self._listeners: list[Callable[[str, Any], None]] = []
        self.load()

    def load(self) -> None:
        """Load configuration from disk, merging with defaults, then env."""
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # Merge: defaults + saved (saved overrides)
                base = {**DEFAULT_CONFIG, **saved}
                logger.info("Configuration loaded from %s", self._path)
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Failed to load config: %s. Using defaults.", e)
                base = dict(DEFAULT_CONFIG)
        else:
            logger.info("No config file found. Using defaults.")
            base = dict(DEFAULT_CONFIG)
        # Environment overrides win over both file and defaults.
        self._data = apply_env_overrides(base)

    def save(self) -> None:
        """Save current configuration to disk.

        The write is atomic: content goes to a temporary file in the
        same directory first, then replaces the config file. A crash or
        disk error mid-write can therefore never leave a truncated
        config behind — the previous good file stays intact.
        """
        tmp_path = self._path.with_name(self._path.name + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp_path, self._path)
        except OSError as e:
            logger.error("Failed to save config: %s", e)
        else:
            logger.info("Configuration saved to %s", self._path)
        finally:
            # Best effort: never leave a stale temp file behind.
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value and persist."""
        old = self._data.get(key)
        self._data[key] = value
        self.save()
        if old != value:
            self._notify_listeners(key, value)

    def as_dict(self) -> dict[str, Any]:
        """Return a copy of all configuration."""
        return dict(self._data)

    def _notify_listeners(self, key: str, value: Any) -> None:
        """Notify every config observer of a change.

        Single notification path for all mutators: the Qt
        ``config_changed`` signal (GUI observers) and the plain-Python
        listeners registered via ``add_listener`` (headless observers).
        """
        self.config_changed.emit(key, value)
        for listener in self._listeners:
            listener(key, value)
        logger.debug("Config changed: %s = %s", key, value)

    def update(self, values: dict[str, Any]) -> None:
        """Update multiple keys at once, then save once."""
        changed = False
        for key, value in values.items():
            if self._data.get(key) != value:
                self._data[key] = value
                self._notify_listeners(key, value)
                changed = True
        if changed:
            self.save()
            logger.debug("Config updated: %s", list(values.keys()))

    def add_listener(self, callback: Callable[[str, Any], None]) -> None:
        """Register a plain-Python callback for config changes."""
        self._listeners.append(callback)

    def reset_to_defaults(self) -> None:
        """Reset all configuration to defaults.

        Environment overrides still win: the documented precedence
        (env > file > defaults) holds on the reset path too.
        """
        self._data = apply_env_overrides(dict(DEFAULT_CONFIG))
        self.save()
        for key, value in self._data.items():
            self._notify_listeners(key, value)
