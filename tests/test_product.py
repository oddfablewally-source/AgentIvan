"""
Tests for the example product module.

These double as the documented product-testing pattern: depend on the
ChatProvider protocol and test with a trivial in-memory fake -- no
network, no server, no mocks of internals.
"""

from __future__ import annotations

from typing import Iterator

from src.app.ai.provider import ChatProvider, ProviderMessage
from src.app.data.database import Database
from src.app.data.repositories import ConversationRepository, MessageRepository
from src.app.product.example_service import ExampleProductService


class FakeChatProvider:
    """In-memory ChatProvider for product tests."""

    def __init__(self, reply: str = "AI response text") -> None:
        self.reply = reply
        self.calls: list[tuple[list[ProviderMessage], str | None]] = []

    def chat(
        self,
        messages: list[ProviderMessage],
        system_prompt: str | None = None,
    ) -> str:
        self.calls.append((messages, system_prompt))
        return self.reply

    def chat_stream(
        self,
        messages: list[ProviderMessage],
        system_prompt: str | None = None,
    ) -> Iterator[str]:
        self.calls.append((messages, system_prompt))
        yield self.reply


def _make_service(
    db: Database, reply: str = "AI response text"
) -> tuple[ExampleProductService, FakeChatProvider]:
    conv_repo = ConversationRepository(db)
    msg_repo = MessageRepository(db)
    fake = FakeChatProvider(reply=reply)
    assert isinstance(fake, ChatProvider)
    return ExampleProductService(fake, conv_repo, msg_repo), fake


def test_create_conversation(db: Database) -> None:
    """ExampleProductService should create conversations."""
    service, _ = _make_service(db)
    conv_repo = ConversationRepository(db)

    conv_id = service.create_conversation("My Conversation")

    assert conv_id is not None
    conv = conv_repo.get_by_id(conv_id)
    assert conv.title == "My Conversation"


def test_send_and_save(db: Database) -> None:
    """send_and_save should save both prompt and response."""
    service, fake = _make_service(db)
    msg_repo = MessageRepository(db)

    conv_id = service.create_conversation("Test")
    response = service.send_and_save(conv_id, "Hello AI", system_prompt="Be helpful")

    assert response == "AI response text"

    # Verify both messages were saved
    msgs = msg_repo.list_by_conversation(conv_id)
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].content == "Hello AI"
    assert msgs[1].role == "assistant"
    assert msgs[1].content == "AI response text"

    # Verify the provider was called once, through the protocol, with
    # provider-shaped messages and the system prompt passed through
    assert len(fake.calls) == 1
    sent_messages, sent_prompt = fake.calls[0]
    assert all(isinstance(m, ProviderMessage) for m in sent_messages)
    assert sent_messages[0].content == "Hello AI"
    assert sent_prompt == "Be helpful"


def test_send_and_save_builds_history(db: Database) -> None:
    """send_and_save should send full conversation history to the provider."""
    service, fake = _make_service(db, reply="Response")

    conv_id = service.create_conversation("History Test")

    # First exchange
    service.send_and_save(conv_id, "First question")
    # Second exchange
    service.send_and_save(conv_id, "Second question")

    # The second call should carry the full history: the first exchange's
    # user + assistant messages plus the new user prompt.
    sent_messages, _ = fake.calls[1]
    assert len(sent_messages) == 3
    assert [m.role for m in sent_messages] == ["user", "assistant", "user"]
