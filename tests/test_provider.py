"""
Tests for the provider boundary (src/app/ai/provider.py).

These tests need no Ollama server, no network, and no GUI. They prove:

1. Product code can depend on the `ChatProvider` protocol and be tested
   with a trivial in-memory fake.
2. `OllamaChatProvider` correctly adapts a service to the protocol
   (message translation + system-prompt passthrough), for both
   `chat()` and `chat_stream()`.
3. The adapter propagates the boilerplate's typed `OllamaError`
   hierarchy unchanged.
4. The adapter validates its constructor input.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from src.app.ai.ollama_service import ChatMessage, OllamaConnectionError
from src.app.ai.provider import ChatProvider, OllamaChatProvider, ProviderMessage


class FakeProvider:
    """In-memory ChatProvider: deterministic, no I/O."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[ProviderMessage], str | None]] = []

    def chat(
        self,
        messages: list[ProviderMessage],
        system_prompt: str | None = None,
    ) -> str:
        self.calls.append((messages, system_prompt))
        return f"fake reply to: {messages[-1].content}"

    def chat_stream(
        self,
        messages: list[ProviderMessage],
        system_prompt: str | None = None,
    ) -> Iterator[str]:
        self.calls.append((messages, system_prompt))
        yield f"fake stream reply to: {messages[-1].content}"


class StubOllamaService:
    """Stand-in for OllamaService: records calls, returns canned text."""

    def __init__(self, reply: str = "stubbed ollama reply") -> None:
        self.reply = reply
        self.calls: list[tuple[list, str | None]] = []

    def chat(self, messages, system_prompt=None) -> str:
        self.calls.append((messages, system_prompt))
        return self.reply

    def chat_stream(self, messages, system_prompt=None) -> Iterator[str]:
        self.calls.append((messages, system_prompt))
        yield "chunk-one "
        yield "chunk-two"


class FailingOllamaService:
    """Stand-in that simulates the server being unreachable."""

    def chat(self, messages, system_prompt=None) -> str:
        raise OllamaConnectionError("Could not connect to Ollama.")

    def chat_stream(self, messages, system_prompt=None) -> Iterator[str]:
        raise OllamaConnectionError("Could not connect to Ollama.")
        yield  # unreachable: makes this a generator, so the error surfaces on iteration


def _exercise(provider: ChatProvider) -> str:
    """Generic consumer: depends only on the protocol, not a concrete class."""
    return provider.chat(
        [ProviderMessage(role="user", content="hello")],
        system_prompt="Be brief.",
    )


def test_fake_satisfies_protocol_and_is_usable_generically() -> None:
    fake = FakeProvider()
    assert isinstance(fake, ChatProvider)

    response = _exercise(fake)

    assert response == "fake reply to: hello"
    sent_messages, sent_prompt = fake.calls[0]
    assert sent_messages[0] == ProviderMessage(role="user", content="hello")
    assert sent_prompt == "Be brief."


def test_adapter_satisfies_protocol() -> None:
    adapter = OllamaChatProvider(StubOllamaService())
    assert isinstance(adapter, ChatProvider)
    assert _exercise(adapter) == "stubbed ollama reply"


def test_adapter_translates_messages_and_passes_system_prompt() -> None:
    stub = StubOllamaService()
    adapter = OllamaChatProvider(stub)

    adapter.chat(
        [
            ProviderMessage(role="user", content="first"),
            ProviderMessage(role="assistant", content="second"),
        ],
        system_prompt="sys",
    )

    sent_messages, sent_prompt = stub.calls[0]
    assert [m.role for m in sent_messages] == ["user", "assistant"]
    assert [m.content for m in sent_messages] == ["first", "second"]
    # Translated into the Ollama service's own message type.
    assert all(isinstance(m, ChatMessage) for m in sent_messages)
    assert sent_prompt == "sys"


def test_adapter_propagates_typed_ollama_errors_unchanged() -> None:
    adapter = OllamaChatProvider(FailingOllamaService())
    with pytest.raises(OllamaConnectionError):
        adapter.chat([ProviderMessage(role="user", content="hi")])


def test_adapter_rejects_bad_constructor_input() -> None:
    with pytest.raises(TypeError):
        OllamaChatProvider(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        OllamaChatProvider(object())  # no chat()/chat_stream() methods


def _stream_exercise(provider: ChatProvider) -> str:
    """Generic streaming consumer: depends only on the protocol."""
    return "".join(
        provider.chat_stream(
            [ProviderMessage(role="user", content="hello")],
            system_prompt="Be brief.",
        )
    )


def test_streaming_fake_satisfies_protocol() -> None:
    fake = FakeProvider()
    assert isinstance(fake, ChatProvider)
    assert _stream_exercise(fake) == "fake stream reply to: hello"


def test_adapter_streams_with_translation() -> None:
    stub = StubOllamaService()
    adapter = OllamaChatProvider(stub)
    assert isinstance(adapter, ChatProvider)

    chunks = list(
        adapter.chat_stream(
            [ProviderMessage(role="user", content="hi")],
            system_prompt="sys",
        )
    )
    assert chunks == ["chunk-one ", "chunk-two"]

    sent_messages, sent_prompt = stub.calls[0]
    assert [m.content for m in sent_messages] == ["hi"]
    assert all(isinstance(m, ChatMessage) for m in sent_messages)
    assert sent_prompt == "sys"


def test_adapter_stream_propagates_typed_ollama_errors() -> None:
    adapter = OllamaChatProvider(FailingOllamaService())
    with pytest.raises(OllamaConnectionError):
        list(adapter.chat_stream([ProviderMessage(role="user", content="hi")]))
