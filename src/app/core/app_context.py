"""
Application context: the central composition root.

This object is created once at startup and passed to the main window.
It holds references to all shared services so they can be injected
where needed. This keeps the GUI decoupled from service construction.
"""

from __future__ import annotations

from src.app.core.config import ConfigManager
from src.app.core.logging_setup import get_logger
from src.app.data.database import Database
from src.app.data.repositories import ConversationRepository, MessageRepository
from src.app.ai.ollama_service import OllamaService
from src.app.ai.provider import ChatProvider, OllamaChatProvider

logger = get_logger("context")


class AppContext:
    """
    Composition root for the application.

    Owns and manages the lifecycle of:
    - ConfigManager (persistent settings)
    - Database (SQLite connection manager)
    - Repositories (conversation/message data access)
    - OllamaService (AI backend abstraction)
    - chat_provider (ChatProvider seam wrapping the OllamaService;
      product code should depend on this, not on the concrete service)

    The GUI receives this object and uses it to access services.

    Lifecycle contract: the entry point (`main.py`) guarantees
    `shutdown()` runs when the event loop exits (try/finally), and GUI
    close handlers may also call it after their own teardown.
    `shutdown()` is idempotent, so overlapping calls are safe.
    """

    def __init__(self) -> None:
        logger.info("Initializing application context...")

        # --- Configuration ---
        self.config = ConfigManager()

        # --- Database ---
        self.database = Database()

        # --- Repositories ---
        self.conversations = ConversationRepository(self.database)
        self.messages = MessageRepository(self.database)

        # --- AI Service ---
        self.ollama = OllamaService(
            host=self.config.get("ollama_host"),
            model=self.config.get("ollama_model"),
            timeout=self.config.get("ollama_timeout"),
        )

        # --- Provider seam ---
        # Product code depends on this protocol-typed attribute, not on
        # self.ollama directly. The adapter wraps the same OllamaService
        # instance, so config changes applied to self.ollama in
        # _on_config_changed remain effective through the seam with no
        # extra rewiring.
        self.chat_provider: ChatProvider = OllamaChatProvider(self.ollama)

        # Wire config changes to Ollama service
        self.config.config_changed.connect(self._on_config_changed)

        self._shutdown = False

        logger.info("Application context initialized.")

    def _on_config_changed(self, key: str, value: object) -> None:
        """React to configuration changes."""
        if key == "ollama_host":
            self.ollama.set_host(str(value))
        elif key == "ollama_model":
            self.ollama.set_model(str(value))
        elif key == "ollama_timeout":
            self.ollama.set_timeout(int(value))

    def shutdown(self) -> None:
        """
        Clean up resources on application exit.

        Idempotent: safe to call from both the entry point's finally
        block and GUI close handlers; only the first call does work.
        """
        if self._shutdown:
            logger.debug("Application context already shut down; skipping.")
            return
        self._shutdown = True
        logger.info("Shutting down application context...")
        self.database.close_all()
        logger.info("Application context shut down.")
