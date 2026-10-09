"""AI service layer: Ollama integration abstraction."""

from src.app.ai.provider import ChatProvider, OllamaChatProvider, ProviderMessage

__all__ = [
    "ChatProvider",
    "OllamaChatProvider",
    "ProviderMessage",
]
