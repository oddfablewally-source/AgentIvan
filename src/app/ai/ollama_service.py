"""
Ollama service abstraction.

This module provides a clean interface for communicating with a local Ollama
server. It is the ONLY place that knows about the Ollama REST API. The rest
of the application interacts with this service through its public methods.

Ollama API reference (verified against official docs as of 2025/2026):
- Base URL: http://localhost:11434
- GET  /           -> Health check ("Ollama is running")
- GET  /api/tags   -> List available models
- POST /api/chat   -> Chat completion (supports streaming via NDJSON)

Design goals:
- No external AI framework dependency (uses 'requests' only).
- Configurable host, model, and timeout.
- Connection testing and model availability checking.
- Streaming chat responses for real-time UI updates.
- Clear, typed exceptions for different failure modes.
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass, field
from typing import Generator, Optional
from urllib.parse import urlparse

import requests

from src.app.core.logging_setup import get_logger

logger = get_logger("ollama")


# Hosts that must never be routed through a proxy, per requests'
# no_proxy mechanism. Loopback traffic going through a proxy can only
# break: proxies typically refuse or cannot resolve it.
_LOOPBACK_NO_PROXY = "localhost,127.0.0.1,::1"


def _is_loopback_host(host: str) -> bool:
    """Whether an Ollama host URL points at the local machine."""
    try:
        name = urlparse(host).hostname or ""
    except ValueError:
        return False
    if name.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class OllamaError(Exception):
    """Base exception for all Ollama service errors."""


class OllamaConnectionError(OllamaError):
    """Raised when the Ollama server is unreachable."""


class OllamaModelNotFoundError(OllamaError):
    """Raised when the requested model is not available locally."""


class OllamaTimeoutError(OllamaError):
    """Raised when a request to Ollama times out."""


class OllamaAPIError(OllamaError):
    """Raised when Ollama returns an error response."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ChatMessage:
    """A single chat message."""
    role: str  # 'user', 'assistant', 'system'
    content: str


@dataclass
class OllamaModel:
    """Information about an available Ollama model."""
    name: str
    size: int = 0
    modified_at: str = ""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class OllamaService:
    """
    Service abstraction over the Ollama REST API.

    All network I/O happens here. The GUI never touches 'requests' directly.
    """

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "llama3.2",
        timeout: int = 120,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._proxies: dict[str, str] = self._proxy_bypass_for_host(self.host)

    # --- Configuration ---

    @staticmethod
    def _proxy_bypass_for_host(host: str) -> dict[str, str]:
        """Per-request proxy config for a host.

        Proxy environment variables (HTTP_PROXY, HTTPS_PROXY, ALL_PROXY --
        on Windows these can also come from the registry) must never
        hijack traffic to a loopback Ollama server: the proxy would
        typically refuse the connection and the app would misleadingly
        report Ollama as unreachable. Remote hosts keep the default
        behavior so a corporate proxy is still honored where needed.
        """
        if _is_loopback_host(host):
            return {"no_proxy": _LOOPBACK_NO_PROXY}
        return {}

    def set_host(self, host: str) -> None:
        self.host = host.rstrip("/")
        self._proxies = self._proxy_bypass_for_host(self.host)

    def set_model(self, model: str) -> None:
        self.model = model

    def set_timeout(self, timeout: int) -> None:
        self.timeout = timeout

    # --- Connection testing ---

    def test_connection(self) -> tuple[bool, str]:
        """
        Test whether the Ollama server is reachable.

        Returns:
            (success, message) tuple.
        """
        try:
            resp = requests.get(
                f"{self.host}/",
                timeout=min(self.timeout, 10),
                proxies=self._proxies,
            )
            if resp.status_code == 200:
                return True, "Connected to Ollama successfully."
            return False, f"Ollama returned HTTP {resp.status_code}."
        except requests.ConnectionError:
            return False, "Could not connect to Ollama. Is the server running?"
        except requests.Timeout:
            return False, "Connection to Ollama timed out."
        except requests.RequestException as e:
            return False, f"Connection error: {e}"

    # --- Model management ---

    def list_models(self) -> list[OllamaModel]:
        """
        List all locally available models.

        Raises:
            OllamaConnectionError: If the server is unreachable.
            OllamaAPIError: If the server returns an error.
        """
        try:
            resp = requests.get(
                f"{self.host}/api/tags",
                timeout=min(self.timeout, 10),
                proxies=self._proxies,
            )
            if resp.status_code != 200:
                raise OllamaAPIError(
                    f"Failed to list models: HTTP {resp.status_code}"
                )
            data = resp.json()
            models = []
            for m in data.get("models", []):
                models.append(
                    OllamaModel(
                        name=m.get("name", ""),
                        size=m.get("size", 0),
                        modified_at=m.get("modified_at", ""),
                    )
                )
            return models
        except requests.ConnectionError:
            raise OllamaConnectionError(
                "Could not connect to Ollama. Is the server running?"
            )
        except requests.Timeout:
            raise OllamaTimeoutError("Request to list models timed out.")
        except requests.RequestException as e:
            raise OllamaAPIError(f"Failed to list models: {e}")

    def is_model_available(self, model_name: str | None = None) -> bool:
        """
        Check whether a specific model is available locally.

        Args:
            model_name: Model to check. Defaults to the configured model.
        """
        name = model_name or self.model
        try:
            models = self.list_models()
            return any(m.name == name for m in models)
        except OllamaError:
            return False

    # --- Chat ---

    def chat(
        self,
        messages: list[ChatMessage],
        system_prompt: str | None = None,
    ) -> str:
        """
        Send a chat request and return the complete response.

        This is the non-streaming variant. For real-time token updates,
        use chat_stream() instead.

        Args:
            messages: List of chat messages (conversation history).
            system_prompt: Optional system prompt to prepend.

        Returns:
            The assistant's response text.

        Raises:
            OllamaConnectionError: Server unreachable.
            OllamaModelNotFoundError: Model not available.
            OllamaTimeoutError: Request timed out.
            OllamaAPIError: Other API errors.
        """
        payload = self._build_chat_payload(messages, system_prompt, stream=False)

        try:
            resp = requests.post(
                f"{self.host}/api/chat",
                json=payload,
                timeout=self.timeout,
                proxies=self._proxies,
            )
            self._check_response_errors(resp)
            data = resp.json()
            return data.get("message", {}).get("content", "")
        except requests.ConnectionError:
            raise OllamaConnectionError(
                "Could not connect to Ollama. Is the server running?"
            )
        except requests.Timeout:
            raise OllamaTimeoutError("Chat request timed out.")
        except requests.RequestException as e:
            raise OllamaAPIError(f"Chat request failed: {e}")

    def chat_stream(
        self,
        messages: list[ChatMessage],
        system_prompt: str | None = None,
    ) -> Generator[str, None, None]:
        """
        Send a chat request with streaming.

        Yields response tokens as they arrive. The final response is
        the concatenation of all yielded strings.

        Raises:
            OllamaConnectionError: Server unreachable.
            OllamaModelNotFoundError: Model not available.
            OllamaTimeoutError: Request timed out.
            OllamaAPIError: Other API errors.
        """
        payload = self._build_chat_payload(messages, system_prompt, stream=True)

        try:
            with requests.post(
                f"{self.host}/api/chat",
                json=payload,
                timeout=self.timeout,
                stream=True,
                proxies=self._proxies,
            ) as resp:
                self._check_response_errors(resp)

                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Could not parse streaming chunk: %s", line)
                        continue

                    if chunk.get("error"):
                        raise OllamaAPIError(chunk["error"])

                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content

                    if chunk.get("done"):
                        break

        except requests.ConnectionError:
            raise OllamaConnectionError(
                "Could not connect to Ollama. Is the server running?"
            )
        except requests.Timeout:
            raise OllamaTimeoutError("Chat request timed out.")
        except requests.RequestException as e:
            raise OllamaAPIError(f"Chat request failed: {e}")

    # --- Internal helpers ---

    def _build_chat_payload(
        self,
        messages: list[ChatMessage],
        system_prompt: str | None,
        stream: bool,
    ) -> dict:
        """Build the JSON payload for the /api/chat endpoint."""
        msg_list = []

        if system_prompt:
            msg_list.append({"role": "system", "content": system_prompt})

        for m in messages:
            msg_list.append({"role": m.role, "content": m.content})

        return {
            "model": self.model,
            "messages": msg_list,
            "stream": stream,
        }

    def _check_response_errors(self, resp: requests.Response) -> None:
        """
        Check an HTTP response for error conditions.

        Raises appropriate exceptions for model-not-found, connection
        issues, and other API errors.
        """
        if resp.status_code == 200:
            return

        # Try to get error message from response body
        error_msg = ""
        try:
            data = resp.json()
            error_msg = data.get("error", "")
        except (json.JSONDecodeError, ValueError):
            error_msg = resp.text

        # Model not found is typically a 404
        if resp.status_code == 404 or "model not found" in error_msg.lower():
            raise OllamaModelNotFoundError(
                f"Model '{self.model}' not found. "
                f"Run 'ollama pull {self.model}' to download it."
            )

        if resp.status_code == 503:
            raise OllamaConnectionError(
                "Ollama is busy or the model is still loading."
            )

        raise OllamaAPIError(
            f"Ollama API error (HTTP {resp.status_code}): {error_msg}"
        )
