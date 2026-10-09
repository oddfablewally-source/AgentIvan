"""
Example product module.

This demonstrates how to use the boilerplate's infrastructure to build
product-specific functionality. It shows the pattern for creating a
service that uses a chat provider and the database.

Key pattern: depend on the ChatProvider protocol (obtained from the
composition root as ctx.chat_provider), never on a concrete service.
This keeps product code swappable and testable with trivial fakes.

When you build your own product, replace this with your actual logic.
"""

from __future__ import annotations

from src.app.ai.provider import ChatProvider, ProviderMessage
from src.app.core.logging_setup import get_logger
from src.app.data.repositories import ConversationRepository, MessageRepository

logger = get_logger("product.example")


class ExampleProductService:
    """
    Example product-specific service.

    This is a thin orchestration layer that demonstrates:
    - Depending on the ChatProvider protocol instead of a concrete
      AI service.
    - Persisting conversations and messages to SQLite.
    - Proper error handling and logging.

    Replace this with your own product logic.
    """

    def __init__(
        self,
        chat_provider: ChatProvider,
        conversations: ConversationRepository,
        messages: MessageRepository,
    ) -> None:
        self.chat_provider = chat_provider
        self.conversations = conversations
        self.messages = messages

    def create_conversation(self, title: str = "New Conversation") -> int:
        """Create a new conversation and return its ID."""
        conv = self.conversations.create(title=title)
        logger.info("Created conversation %d: %s", conv.id, conv.title)
        return conv.id

    def send_and_save(
        self,
        conversation_id: int,
        prompt: str,
        system_prompt: str = "",
    ) -> str:
        """
        Send a prompt through the chat provider, save both the prompt and
        response to the database, and return the response text.

        This is a synchronous method (no streaming). For streaming,
        use the ChatWorker in the UI layer.
        """
        # Save user message
        self.messages.add(conversation_id, "user", prompt)

        # Build chat history in the provider's message shape
        history = self.messages.list_by_conversation(conversation_id)
        provider_messages = [
            ProviderMessage(role=m.role, content=m.content) for m in history
        ]

        # Get response through the provider seam
        response = self.chat_provider.chat(
            provider_messages, system_prompt=system_prompt or None
        )

        # Save assistant response
        self.messages.add(conversation_id, "assistant", response)

        logger.info(
            "Conversation %d: prompt saved, response (%d chars) saved.",
            conversation_id,
            len(response),
        )

        return response
