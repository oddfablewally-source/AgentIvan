"""
Tests for AppContext: provider-seam wiring and lifecycle.

Verifies that the composition root exposes the ChatProvider seam
(`chat_provider`) wrapping the same OllamaService instance it manages,
and that shutdown() is idempotent and releases the database.

ConfigManager and Database are patched, so these tests touch no real user
data, no GUI, and no Ollama server.
"""

from __future__ import annotations

from unittest.mock import patch

from src.app.ai.ollama_service import OllamaService
from src.app.ai.provider import ChatProvider, OllamaChatProvider
from src.app.core.app_context import AppContext


def _make_context() -> AppContext:
    """Build an AppContext with config and database patched out."""
    with (
        patch("src.app.core.app_context.ConfigManager") as mock_config_cls,
        patch("src.app.core.app_context.Database"),
    ):
        mock_config_cls.return_value.get.side_effect = lambda key, default=None: {
            "ollama_host": "http://localhost:11434",
            "ollama_model": "llama3.2",
            "ollama_timeout": 120,
        }.get(key, default)
        return AppContext()


def test_chat_provider_is_exposed_as_protocol() -> None:
    """Product code can depend on ctx.chat_provider via the protocol."""
    ctx = _make_context()
    assert isinstance(ctx.chat_provider, ChatProvider)


def test_chat_provider_wraps_the_managed_ollama_service() -> None:
    """The seam wraps the very service AppContext manages, so config
    changes applied to ctx.ollama stay effective with no extra rewiring."""
    ctx = _make_context()
    assert isinstance(ctx.chat_provider, OllamaChatProvider)
    assert isinstance(ctx.ollama, OllamaService)
    assert ctx.chat_provider._service is ctx.ollama


def test_shutdown_closes_database() -> None:
    """shutdown() must release the managed database connections."""
    ctx = _make_context()
    ctx.shutdown()
    ctx.database.close_all.assert_called_once_with()


def test_shutdown_is_idempotent() -> None:
    """Overlapping shutdown calls (entry point + GUI close handler)
    must not duplicate cleanup work."""
    ctx = _make_context()
    ctx.shutdown()
    ctx.shutdown()
    ctx.database.close_all.assert_called_once_with()


def test_config_change_propagates_to_managed_service(tmp_path, monkeypatch) -> None:
    """A runtime config change must reach the managed OllamaService.

    The spec promises config changes stay effective through the
    ChatProvider seam automatically; that holds because the seam wraps
    the very service instance this signal handler mutates. This test
    pins the dynamic half of that contract with a real ConfigManager,
    Database, and service (all redirected to tmp paths -- no real user
    data, no network, no display).
    """
    import src.app.core.config as config_mod
    import src.app.data.database as database_mod

    monkeypatch.setattr(
        config_mod, "get_config_path", lambda: tmp_path / "config.json"
    )
    monkeypatch.setattr(
        database_mod, "get_db_path", lambda: tmp_path / "app.db"
    )

    ctx = AppContext()
    try:
        ctx.config.set("ollama_host", "http://ai-box:11434")
        assert ctx.ollama.host == "http://ai-box:11434"

        ctx.config.set("ollama_model", "mymodel")
        assert ctx.ollama.model == "mymodel"

        ctx.config.set("ollama_timeout", 45)
        assert ctx.ollama.timeout == 45
    finally:
        ctx.shutdown()
