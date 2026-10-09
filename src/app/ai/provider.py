"""
Model/provider boundary.

This module defines the abstraction that product code should depend on
instead of any concrete model service:

- `ProviderMessage`: the single message shape crossing the boundary.
- `ChatProvider`: a `runtime_checkable` Protocol. Any object with matching
  `chat()` and `chat_stream()` methods satisfies it, which makes product
  code testable with trivial in-memory fakes (no network, no server, no
  mocks of internals).
- `OllamaChatProvider`: the bundled adapter that wraps this package's
  existing `OllamaService` to satisfy the protocol. `OllamaService` itself
  is unchanged; this class is the only place that knows about its types.

Error contract: the adapter propagates the existing `OllamaError`
hierarchy unchanged. No new exception types are introduced here; the
boilerplate's typed errors remain the single error vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol, runtime_checkable

from src.app.ai.ollama_service import ChatMessage as _OllamaChatMessage


@dataclass(frozen=True)
class ProviderMessage:
    """A single message crossing the provider boundary."""

    role: str  # "user" | "assistant" | "system"
    content: str


@runtime_checkable
class ChatProvider(Protocol):
    """
    Minimal contract any chat model provider must satisfy.

    Product code should depend on this protocol, not on a concrete
    service class, so providers can be swapped and faked in tests.
    """

    def chat(
        self,
        messages: list[ProviderMessage],
        system_prompt: str | None = None,
    ) -> str:
        """
        Return the provider's complete response text.

        Implementations raise their own documented errors; the bundled
        `OllamaChatProvider` raises the `OllamaError` hierarchy.
        """
        ...

    def chat_stream(
        self,
        messages: list[ProviderMessage],
        system_prompt: str | None = None,
    ) -> Iterator[str]:
        """
        Yield response chunks as they arrive.

        The concatenated chunks form the complete response. Same error
        contract as `chat()`; errors may surface during iteration rather
        than at call time.
        """
        ...


class OllamaChatProvider:
    """
    Adapter from `OllamaService` to the `ChatProvider` protocol.

    Wraps an existing `OllamaService` instance without modifying it.
    Type translation (`ProviderMessage` <-> Ollama `ChatMessage`) and
    Ollama-specific knowledge live only here.
    """

    def __init__(self, ollama_service: object) -> None:
        if ollama_service is None:
            raise TypeError("OllamaChatProvider requires an OllamaService instance.")
        if not hasattr(ollama_service, "chat") or not hasattr(
            ollama_service, "chat_stream"
        ):
            raise TypeError(
                "OllamaChatProvider requires an object with chat() and "
                f"chat_stream() methods; got {type(ollama_service).__name__}."
            )
        self._service = ollama_service

    def chat(
        self,
        messages: list[ProviderMessage],
        system_prompt: str | None = None,
    ) -> str:
        ollama_messages = [
            _OllamaChatMessage(role=m.role, content=m.content) for m in messages
        ]
        # OllamaService.chat already raises the typed OllamaError hierarchy;
        # it propagates unchanged as this adapter's error contract.
        return self._service.chat(ollama_messages, system_prompt=system_prompt)

    def chat_stream(
        self,
        messages: list[ProviderMessage],
        system_prompt: str | None = None,
    ) -> Iterator[str]:
        ollama_messages = [
            _OllamaChatMessage(role=m.role, content=m.content) for m in messages
        ]
        # OllamaService.chat_stream is a generator: errors surface during
        # iteration, still as the typed OllamaError hierarchy.
        return self._service.chat_stream(ollama_messages, system_prompt=system_prompt)
