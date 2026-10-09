"""
Tests for the ConfigManager.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.app.core.config import ConfigManager, DEFAULT_CONFIG


def test_config_loads_defaults_when_no_file(temp_config_path: Path) -> None:
    """Config should use defaults when no file exists."""
    config = ConfigManager(config_path=temp_config_path)
    assert config.get("ollama_host") == DEFAULT_CONFIG["ollama_host"]
    assert config.get("ollama_model") == DEFAULT_CONFIG["ollama_model"]
    assert config.get("ollama_timeout") == DEFAULT_CONFIG["ollama_timeout"]


def test_config_set_and_persist(temp_config_path: Path) -> None:
    """Setting a value should persist to disk."""
    config = ConfigManager(config_path=temp_config_path)
    config.set("ollama_host", "http://192.168.1.100:11434")

    # Verify it was saved to disk
    with open(temp_config_path) as f:
        saved = json.load(f)
    assert saved["ollama_host"] == "http://192.168.1.100:11434"


def test_config_loads_saved_values(temp_config_path: Path) -> None:
    """Config should load previously saved values."""
    # Write a config file
    temp_config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(temp_config_path, "w") as f:
        json.dump({
            "ollama_host": "http://remote:11434",
            "ollama_model": "mistral",
        }, f)

    config = ConfigManager(config_path=temp_config_path)
    assert config.get("ollama_host") == "http://remote:11434"
    assert config.get("ollama_model") == "mistral"
    # Defaults should still be present for unset keys
    assert config.get("ollama_timeout") == DEFAULT_CONFIG["ollama_timeout"]


def test_config_update_multiple(temp_config_path: Path) -> None:
    """update() should set multiple values at once."""
    config = ConfigManager(config_path=temp_config_path)
    config.update({
        "ollama_host": "http://newhost:11434",
        "ollama_model": "gpt-oss",
    })
    assert config.get("ollama_host") == "http://newhost:11434"
    assert config.get("ollama_model") == "gpt-oss"


def test_config_get_with_default(temp_config_path: Path) -> None:
    """get() should return the provided default for unknown keys."""
    config = ConfigManager(config_path=temp_config_path)
    assert config.get("nonexistent", "fallback") == "fallback"


def test_config_reset_to_defaults(temp_config_path: Path) -> None:
    """reset_to_defaults() should restore all defaults."""
    config = ConfigManager(config_path=temp_config_path)
    config.set("ollama_host", "http://changed:11434")
    config.reset_to_defaults()
    assert config.get("ollama_host") == DEFAULT_CONFIG["ollama_host"]


def test_config_reset_to_defaults_preserves_env_precedence(
    temp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reset_to_defaults() must not drop env overrides.

    Documented precedence is env > file > defaults; reset rebuilds
    from defaults, so it must re-apply the env layer instead of
    silently reverting to the compiled-in default.
    """
    monkeypatch.setenv("LOCALAI_DESKTOP_OLLAMA_HOST", "http://env:11434")
    config = ConfigManager(config_path=temp_config_path)
    assert config.get("ollama_host") == "http://env:11434"

    config.reset_to_defaults()

    assert config.get("ollama_host") == "http://env:11434"
    assert config.get("ollama_model") == DEFAULT_CONFIG["ollama_model"]


def test_config_handles_corrupt_file(temp_config_path: Path) -> None:
    """Config should fall back to defaults if the file is corrupt."""
    temp_config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(temp_config_path, "w") as f:
        f.write("not valid json {{{")

    config = ConfigManager(config_path=temp_config_path)
    assert config.get("ollama_host") == DEFAULT_CONFIG["ollama_host"]


# Environment-variable overrides. The prefix derives from APP_NAME
# ("LocalAI Desktop" -> "LOCALAI_DESKTOP_").

def test_config_env_override_without_file(
    temp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An env var should override the default when no file exists."""
    monkeypatch.setenv("LOCALAI_DESKTOP_OLLAMA_HOST", "http://env:11434")
    config = ConfigManager(config_path=temp_config_path)
    assert config.get("ollama_host") == "http://env:11434"


def test_config_env_beats_file(
    temp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Precedence is env > file > defaults."""
    temp_config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(temp_config_path, "w") as f:
        json.dump({"ollama_host": "http://file:11434", "ollama_model": "file-model"}, f)

    monkeypatch.setenv("LOCALAI_DESKTOP_OLLAMA_HOST", "http://env:11434")
    config = ConfigManager(config_path=temp_config_path)
    assert config.get("ollama_host") == "http://env:11434"
    # Keys without an env override still come from the file.
    assert config.get("ollama_model") == "file-model"


def test_config_env_type_coercion(
    temp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env strings should be coerced to the default value's type."""
    monkeypatch.setenv("LOCALAI_DESKTOP_OLLAMA_TIMEOUT", "30")
    monkeypatch.setenv("LOCALAI_DESKTOP_SYSTEM_TRAY", "false")
    config = ConfigManager(config_path=temp_config_path)
    assert config.get("ollama_timeout") == 30
    assert isinstance(config.get("ollama_timeout"), int)
    assert config.get("system_tray") is False


def test_config_env_invalid_value_ignored(
    temp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unparseable env value should not crash loading; default wins."""
    monkeypatch.setenv("LOCALAI_DESKTOP_OLLAMA_TIMEOUT", "not-a-number")
    config = ConfigManager(config_path=temp_config_path)
    assert config.get("ollama_timeout") == DEFAULT_CONFIG["ollama_timeout"]


def test_config_env_unknown_keys_ignored(
    temp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env vars outside the DEFAULT_CONFIG whitelist must not leak in."""
    monkeypatch.setenv("LOCALAI_DESKTOP_NO_SUCH_KEY", "x")
    config = ConfigManager(config_path=temp_config_path)
    assert config.get("NO_SUCH_KEY") is None
    assert "NO_SUCH_KEY" not in config.as_dict()


def test_config_set_notifies_plain_listeners(temp_config_path: Path) -> None:
    """set() must notify plain-Python listeners (existing behavior)."""
    config = ConfigManager(config_path=temp_config_path)
    seen: list[tuple[str, object]] = []
    config.add_listener(lambda key, value: seen.append((key, value)))

    config.set("ollama_host", "http://192.168.1.100:11434")

    assert seen == [("ollama_host", "http://192.168.1.100:11434")]


def test_config_update_notifies_plain_listeners(temp_config_path: Path) -> None:
    """update() must notify plain-Python listeners, exactly like set().

    The settings dialog saves via update(), so a listener-only observer
    would otherwise silently miss every settings save.
    """
    config = ConfigManager(config_path=temp_config_path)
    seen: list[tuple[str, object]] = []
    config.add_listener(lambda key, value: seen.append((key, value)))

    config.update({"ollama_host": "http://10.0.0.5:11434", "ollama_timeout": 60})

    assert ("ollama_host", "http://10.0.0.5:11434") in seen
    assert ("ollama_timeout", 60) in seen
    # Unchanged keys must not notify.
    assert len(seen) == 2


def test_config_reset_to_defaults_notifies_plain_listeners(
    temp_config_path: Path,
) -> None:
    """reset_to_defaults() must notify plain-Python listeners too."""
    config = ConfigManager(config_path=temp_config_path)
    config.set("ollama_host", "http://192.168.1.100:11434")
    seen: list[tuple[str, object]] = []
    config.add_listener(lambda key, value: seen.append((key, value)))

    config.reset_to_defaults()

    notified = dict(seen)
    assert notified["ollama_host"] == DEFAULT_CONFIG["ollama_host"]


def test_config_failed_save_preserves_previous_file(
    temp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A save that dies mid-write must not destroy the previous config.

    save() writes to a temp file and atomically replaces the config,
    so a crash or disk error during the write leaves the last good
    file intact instead of truncated.
    """
    config = ConfigManager(config_path=temp_config_path)
    config.set("ollama_host", "http://good:11434")

    def failing_dump(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", failing_dump)
    config.set("ollama_host", "http://bad:11434")  # save() fails, logs, continues

    with open(temp_config_path, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["ollama_host"] == "http://good:11434"
    # No stale temp file left behind either.
    assert not temp_config_path.with_name(temp_config_path.name + ".tmp").exists()


def test_config_save_leaves_no_temp_file(temp_config_path: Path) -> None:
    """Successful saves must not litter temp files next to the config."""
    config = ConfigManager(config_path=temp_config_path)
    config.set("ollama_host", "http://192.168.1.100:11434")

    assert temp_config_path.exists()
    assert not temp_config_path.with_name(temp_config_path.name + ".tmp").exists()


def test_ambient_env_vars_do_not_leak_into_config(
    temp_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config must be unaffected by ambient <PREFIX>_* environment variables.

    Exercises the same stripping the autouse conftest fixture applies
    before every test: a simulated ambient variable must not override
    the file/default value, while unrelated variables are untouched.
    """
    from tests.conftest import _strip_prefixed_env_vars

    monkeypatch.setenv("LOCALAI_DESKTOP_OLLAMA_HOST", "http://ambient:11434")
    monkeypatch.setenv("SOME_UNRELATED_VAR", "keep-me")
    _strip_prefixed_env_vars(monkeypatch)

    config = ConfigManager(config_path=temp_config_path)
    assert config.get("ollama_host") == DEFAULT_CONFIG["ollama_host"]
    assert os.environ.get("SOME_UNRELATED_VAR") == "keep-me"
