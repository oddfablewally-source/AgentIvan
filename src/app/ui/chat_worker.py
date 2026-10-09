"""
Background worker for AI requests.

Implements a clean QThread-based worker pattern:
- Worker runs on a separate thread, never blocking the GUI.
- Communicates results via Qt signals (thread-safe).
- Supports cooperative cancellation via isInterruptionRequested().
- Emits streaming tokens as they arrive.
- Properly cleans up on completion or cancellation.
- Clean shutdown on application exit.

This worker is designed for chat requests through any ChatProvider.
For other background tasks, create a similar QThread subclass with
appropriate signals.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QThread, Signal

from src.app.ai.ollama_service import OllamaError
from src.app.ai.provider import ChatProvider, ProviderMessage
from src.app.core.logging_setup import get_logger

logger = get_logger("worker")


class ChatWorker(QThread):
    """
    Background worker that sends a chat request to a chat provider and
    streams the response back to the GUI thread.

    Depends on the ChatProvider protocol, not on any concrete service,
    so the same worker serves every current and future provider.

    Signals:
        token_received(str): Emitted for each streaming token chunk.
        finished_success(str): Emitted with the full response when complete.
        failed(str): Emitted with an error message on failure.
    """

    # Signals (emitted on the worker thread, delivered to connected slots
    # on the receiver's thread by Qt's signal/slot mechanism)
    token_received = Signal(str)
    finished_success = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        provider: ChatProvider,
        messages: list[ProviderMessage],
        system_prompt: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._provider = provider
        self._messages = messages
        self._system_prompt = system_prompt
        self._full_response: str = ""

    def run(self) -> None:
        """
        Execute the chat request on the worker thread.

        This method is called by QThread.start(). It must not touch
        any GUI objects directly — only emit signals.
        """
        try:
            logger.info("ChatWorker: starting streaming chat request.")

            self._full_response = ""

            for token in self._provider.chat_stream(
                self._messages,
                system_prompt=self._system_prompt or None,
            ):
                # Check for cancellation
                if self.isInterruptionRequested():
                    logger.info("ChatWorker: cancellation requested. Stopping.")
                    return

                self._full_response += token
                self.token_received.emit(token)

            logger.info("ChatWorker: request completed successfully.")
            self.finished_success.emit(self._full_response)

        except OllamaError as e:
            # The bundled Ollama adapter propagates OllamaError unchanged;
            # other providers' errors take the generic path below.
            logger.error("ChatWorker: provider error: %s", e)
            self.failed.emit(str(e))
        except Exception as e:
            logger.exception("ChatWorker: unexpected error: %s", e)
            self.failed.emit(f"Unexpected error: {e}")

    def get_full_response(self) -> str:
        """Return the accumulated response text (available after completion)."""
        return self._full_response
