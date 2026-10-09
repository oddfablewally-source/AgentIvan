"""
Tests for the ChatWorker background pattern.

The worker is exercised headlessly: a QCoreApplication (no display
libraries needed) is enough to construct QThreads, and run() is invoked
directly so the streaming, error, and cancellation paths execute
synchronously on the test thread, with signals delivered via direct
connections. A fake ChatProvider (not an Ollama service) proves the
worker depends only on the protocol.
"""

from __future__ import annotations

from typing import Iterator, Optional

import pytest
from PySide6.QtCore import QCoreApplication

from src.app.ai.ollama_service import OllamaError
from src.app.ai.provider import ChatProvider, ProviderMessage
from src.app.ui.chat_worker import ChatWorker


@pytest.fixture(scope="module")
def qapp():
    """A QCoreApplication is enough for QThread tests; no display needed."""
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


class FakeProvider:
    """In-memory ChatProvider; records what the worker sent it."""

    def __init__(
        self,
        chunks: tuple[str, ...] = (),
        error: Optional[Exception] = None,
    ) -> None:
        self._chunks = list(chunks)
        self._error = error
        self.seen_messages: Optional[list[ProviderMessage]] = None
        self.seen_prompt: Optional[str] = None

    def chat(
        self,
        messages: list[ProviderMessage],
        system_prompt: Optional[str] = None,
    ) -> str:
        raise AssertionError("these tests exercise chat_stream")

    def chat_stream(
        self,
        messages: list[ProviderMessage],
        system_prompt: Optional[str] = None,
    ) -> Iterator[str]:
        self.seen_messages = messages
        self.seen_prompt = system_prompt
        if self._error is not None:
            raise self._error
        yield from self._chunks


def _run_worker(
    qapp, provider: FakeProvider, system_prompt: str = "Be helpful"
) -> tuple[ChatWorker, dict[str, list[str]]]:
    assert isinstance(provider, ChatProvider)
    worker = ChatWorker(
        provider,
        [ProviderMessage(role="user", content="Hello")],
        system_prompt=system_prompt,
    )
    captured: dict[str, list[str]] = {
        "tokens": [],
        "success": [],
        "failed": [],
    }
    worker.token_received.connect(captured["tokens"].append)
    worker.finished_success.connect(captured["success"].append)
    worker.failed.connect(captured["failed"].append)
    worker.run()
    return worker, captured


def test_streams_tokens_and_emits_full_response(qapp) -> None:
    """Tokens stream through; finished_success carries the full text."""
    provider = FakeProvider(chunks=("Hel", "lo", "!"))
    worker, captured = _run_worker(qapp, provider)

    assert captured["tokens"] == ["Hel", "lo", "!"]
    assert captured["success"] == ["Hello!"]
    assert captured["failed"] == []
    assert worker.get_full_response() == "Hello!"

    # The worker passed provider-shaped messages and the system prompt
    # through to the protocol, untouched.
    assert provider.seen_messages == [ProviderMessage(role="user", content="Hello")]
    assert provider.seen_prompt == "Be helpful"


def test_provider_error_emits_failed(qapp) -> None:
    """A typed provider error surfaces via failed() with its message."""
    provider = FakeProvider(error=OllamaError("connection refused"))
    _, captured = _run_worker(qapp, provider)

    assert captured["success"] == []
    assert captured["failed"] == ["connection refused"]


def test_unexpected_error_gets_generic_prefix(qapp) -> None:
    """Non-provider errors are reported, never raised out of run()."""
    provider = FakeProvider(error=RuntimeError("boom"))
    _, captured = _run_worker(qapp, provider)

    assert captured["success"] == []
    assert len(captured["failed"]) == 1
    assert captured["failed"][0].startswith("Unexpected error:")


def test_cancellation_stops_before_completion(qapp) -> None:
    """An interruption request ends the run with no success signal.

    Deterministic: the provider blocks after its first chunk, so the
    test can request interruption mid-stream and then release it.
    """
    import threading
    import time

    release = threading.Event()
    first_sent = threading.Event()

    class BlockingProvider:
        def chat(self, messages, system_prompt=None):
            raise AssertionError("streaming only")

        def chat_stream(self, messages, system_prompt=None):
            yield "first"
            first_sent.set()
            assert release.wait(10), "test did not release the provider"
            yield "second"

    assert isinstance(BlockingProvider(), ChatProvider)
    worker = ChatWorker(BlockingProvider(), [ProviderMessage(role="user", content="Hi")])
    captured: dict[str, list[str]] = {"tokens": [], "success": [], "failed": []}
    worker.token_received.connect(captured["tokens"].append)
    worker.finished_success.connect(captured["success"].append)
    worker.failed.connect(captured["failed"].append)

    worker.start()
    assert first_sent.wait(5), "worker never produced its first token"
    # Let the queued token signal reach this thread.
    deadline = time.time() + 5
    while not captured["tokens"] and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    worker.requestInterruption()
    release.set()
    assert worker.wait(5000), "worker did not stop after interruption"
    qapp.processEvents()

    assert captured["tokens"] == ["first"]
    assert captured["success"] == []
    assert captured["failed"] == []
