"""
Tests for the OllamaService using mocks/fakes.

These tests do NOT require a running Ollama server. They use
unittest.mock to simulate HTTP responses.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.app.ai.ollama_service import (
    ChatMessage,
    OllamaAPIError,
    OllamaConnectionError,
    OllamaModelNotFoundError,
    OllamaModel,
    OllamaService,
    OllamaTimeoutError,
    _is_loopback_host,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code=200, json_data=None, text="", lines=None):
    """Create a mock requests.Response object."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text

    if lines is not None:
        # For streaming: iter_lines returns bytes
        resp.iter_lines.return_value = [
            json.dumps(line).encode() if isinstance(line, dict) else line.encode()
            for line in lines
        ]
        # Support context manager protocol
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)

    return resp


# ---------------------------------------------------------------------------
# Connection tests
# ---------------------------------------------------------------------------

def test_test_connection_success() -> None:
    """test_connection should return (True, message) when server is up."""
    service = OllamaService(host="http://localhost:11434")
    mock_resp = _make_response(status_code=200, text="Ollama is running")

    with patch("requests.get", return_value=mock_resp):
        success, msg = service.test_connection()

    assert success is True
    assert "Connected" in msg


def test_test_connection_failure() -> None:
    """test_connection should return (False, message) when server is down."""
    service = OllamaService(host="http://localhost:11434")

    with patch("requests.get", side_effect=requests.ConnectionError()):
        success, msg = service.test_connection()

    assert success is False
    assert "Could not connect" in msg


def test_test_connection_timeout() -> None:
    """test_connection should handle timeout."""
    service = OllamaService(host="http://localhost:11434")

    with patch("requests.get", side_effect=requests.Timeout()):
        success, msg = service.test_connection()

    assert success is False
    assert "timed out" in msg.lower()


# ---------------------------------------------------------------------------
# Model listing tests
# ---------------------------------------------------------------------------

def test_list_models_success() -> None:
    """list_models should parse the response correctly."""
    service = OllamaService()
    mock_resp = _make_response(
        status_code=200,
        json_data={
            "models": [
                {"name": "llama3.2", "size": 4000000000, "modified_at": "2024-01-01"},
                {"name": "mistral", "size": 7000000000, "modified_at": "2024-02-01"},
            ]
        },
    )

    with patch("requests.get", return_value=mock_resp):
        models = service.list_models()

    assert len(models) == 2
    assert models[0].name == "llama3.2"
    assert models[0].size == 4000000000
    assert models[1].name == "mistral"


def test_list_models_connection_error() -> None:
    """list_models should raise OllamaConnectionError when server is down."""
    service = OllamaService()

    with patch("requests.get", side_effect=requests.ConnectionError()):
        with pytest.raises(OllamaConnectionError):
            service.list_models()


def test_is_model_available_true() -> None:
    """is_model_available should return True when model exists."""
    service = OllamaService(model="llama3.2")
    mock_resp = _make_response(
        status_code=200,
        json_data={"models": [{"name": "llama3.2"}, {"name": "mistral"}]},
    )

    with patch("requests.get", return_value=mock_resp):
        assert service.is_model_available() is True


def test_is_model_available_false() -> None:
    """is_model_available should return False when model doesn't exist."""
    service = OllamaService(model="nonexistent")
    mock_resp = _make_response(
        status_code=200,
        json_data={"models": [{"name": "llama3.2"}]},
    )

    with patch("requests.get", return_value=mock_resp):
        assert service.is_model_available() is False


def test_is_model_available_connection_error_returns_false() -> None:
    """is_model_available should return False (not raise) on connection error."""
    service = OllamaService()

    with patch("requests.get", side_effect=requests.ConnectionError()):
        assert service.is_model_available() is False


# ---------------------------------------------------------------------------
# Chat tests
# ---------------------------------------------------------------------------

def test_chat_success() -> None:
    """chat should return the response content."""
    service = OllamaService()
    mock_resp = _make_response(
        status_code=200,
        json_data={
            "model": "llama3.2",
            "message": {"role": "assistant", "content": "Hello!"},
            "done": True,
        },
    )

    with patch("requests.post", return_value=mock_resp):
        response = service.chat([ChatMessage(role="user", content="Hi")])

    assert response == "Hello!"


def test_chat_model_not_found() -> None:
    """chat should raise OllamaModelNotFoundError for 404."""
    service = OllamaService(model="nonexistent")
    mock_resp = _make_response(
        status_code=404,
        json_data={"error": "model not found"},
    )

    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(OllamaModelNotFoundError):
            service.chat([ChatMessage(role="user", content="Hi")])


def test_chat_connection_error() -> None:
    """chat should raise OllamaConnectionError when server is down."""
    service = OllamaService()

    with patch("requests.post", side_effect=requests.ConnectionError()):
        with pytest.raises(OllamaConnectionError):
            service.chat([ChatMessage(role="user", content="Hi")])


def test_chat_timeout() -> None:
    """chat should raise OllamaTimeoutError on timeout."""
    service = OllamaService()

    with patch("requests.post", side_effect=requests.Timeout()):
        with pytest.raises(OllamaTimeoutError):
            service.chat([ChatMessage(role="user", content="Hi")])


def test_chat_api_error() -> None:
    """chat should raise OllamaAPIError for other HTTP errors."""
    service = OllamaService()
    mock_resp = _make_response(
        status_code=500,
        json_data={"error": "internal server error"},
    )

    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(OllamaAPIError):
            service.chat([ChatMessage(role="user", content="Hi")])


# ---------------------------------------------------------------------------
# Streaming chat tests
# ---------------------------------------------------------------------------

def test_chat_stream_success() -> None:
    """chat_stream should yield tokens from the streaming response."""
    service = OllamaService()
    mock_resp = _make_response(
        status_code=200,
        lines=[
            {"model": "llama3.2", "message": {"role": "assistant", "content": "Hello"}, "done": False},
            {"model": "llama3.2", "message": {"role": "assistant", "content": " world"}, "done": False},
            {"model": "llama3.2", "message": {"role": "assistant", "content": "!"}, "done": True},
        ],
    )

    with patch("requests.post", return_value=mock_resp):
        tokens = list(service.chat_stream([ChatMessage(role="user", content="Hi")]))

    assert tokens == ["Hello", " world", "!"]


def test_chat_stream_connection_error() -> None:
    """chat_stream should raise OllamaConnectionError on connection failure."""
    service = OllamaService()

    with patch("requests.post", side_effect=requests.ConnectionError()):
        with pytest.raises(OllamaConnectionError):
            list(service.chat_stream([ChatMessage(role="user", content="Hi")]))


def test_chat_stream_handles_error_chunk() -> None:
    """chat_stream should raise OllamaAPIError if a chunk contains an error."""
    service = OllamaService()
    mock_resp = _make_response(
        status_code=200,
        lines=[
            {"error": "something went wrong"},
        ],
    )

    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(OllamaAPIError):
            list(service.chat_stream([ChatMessage(role="user", content="Hi")]))


# ---------------------------------------------------------------------------
# Configuration tests
# ---------------------------------------------------------------------------

def test_set_host() -> None:
    """set_host should update the host."""
    service = OllamaService(host="http://old:11434")
    service.set_host("http://new:11434/")
    assert service.host == "http://new:11434"


def test_set_model() -> None:
    """set_model should update the model."""
    service = OllamaService(model="old")
    service.set_model("new")
    assert service.model == "new"


def test_set_timeout() -> None:
    """set_timeout should update the timeout."""
    service = OllamaService(timeout=30)
    service.set_timeout(60)
    assert service.timeout == 60


def test_chat_payload_includes_system_prompt() -> None:
    """chat payload should include system prompt when provided."""
    service = OllamaService()
    payload = service._build_chat_payload(
        [ChatMessage(role="user", content="Hi")],
        system_prompt="Be helpful",
        stream=False,
    )

    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][0]["content"] == "Be helpful"
    assert payload["messages"][1]["role"] == "user"
    assert payload["stream"] is False


# ---------------------------------------------------------------------------
# Proxy bypass for loopback hosts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("http://localhost:11434", True),
        ("http://LOCALHOST:11434", True),
        ("http://127.0.0.1:11434", True),
        ("http://[::1]:11434", True),
        ("http://192.168.1.10:11434", False),
        ("http://10.0.0.5:11434", False),
        ("https://ollama.example.com", False),
        ("not a url", False),
        ("", False),
    ],
)
def test_is_loopback_host(host: str, expected: bool) -> None:
    assert _is_loopback_host(host) is expected


def test_loopback_host_gets_proxy_bypass() -> None:
    service = OllamaService(host="http://localhost:11434")
    assert service._proxies == {"no_proxy": "localhost,127.0.0.1,::1"}


def test_remote_host_keeps_default_proxy_behavior() -> None:
    """A remote Ollama host must still honor proxy env vars (e.g. a
    corporate egress proxy), so no per-request override is applied."""
    service = OllamaService(host="http://192.168.1.10:11434")
    assert service._proxies == {}


def test_set_host_recomputes_proxy_bypass() -> None:
    service = OllamaService(host="http://localhost:11434")
    assert service._proxies != {}
    service.set_host("http://192.168.1.10:11434")
    assert service._proxies == {}
    service.set_host("http://127.0.0.1:11434/")
    assert service._proxies == {"no_proxy": "localhost,127.0.0.1,::1"}


class _StubOllamaHandler(BaseHTTPRequestHandler):
    """Minimal stand-in for an Ollama server root endpoint."""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        body = b"Ollama is running"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def test_loopback_ignores_proxy_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proxy env vars must not hijack traffic to a loopback Ollama host.

    Reproduces a real corporate-machine failure: HTTP(S)_PROXY set with
    no NO_PROXY entry covering localhost. Without the bypass, requests
    routes the local connection through the (dead) proxy and
    test_connection() misleadingly reports Ollama as unreachable.
    """
    server = HTTPServer(("127.0.0.1", 0), _StubOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for name in (
            "HTTP_PROXY",
            "http_proxy",
            "HTTPS_PROXY",
            "https_proxy",
            "ALL_PROXY",
            "all_proxy",
        ):
            monkeypatch.setenv(name, "http://127.0.0.1:9/")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        service = OllamaService(host=f"http://127.0.0.1:{server.server_port}")
        ok, _ = service.test_connection()
        assert ok is True
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


def test_proxy_env_breaks_loopback_without_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control case: prove the test above actually exercises the proxy
    path -- with the bypass disabled, the dead proxy must break the
    connection."""
    server = HTTPServer(("127.0.0.1", 0), _StubOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for name in (
            "HTTP_PROXY",
            "http_proxy",
            "HTTPS_PROXY",
            "https_proxy",
            "ALL_PROXY",
            "all_proxy",
        ):
            monkeypatch.setenv(name, "http://127.0.0.1:9/")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        service = OllamaService(host=f"http://127.0.0.1:{server.server_port}")
        service._proxies = {}  # simulate the old behavior
        ok, _ = service.test_connection()
        assert ok is False
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
