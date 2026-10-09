"""
Tests for conversation and message repositories.
"""

from __future__ import annotations

from src.app.data.database import Database
from src.app.data.repositories import ConversationRepository, MessageRepository


def test_create_conversation(db: Database) -> None:
    """Creating a conversation should return it with an ID."""
    repo = ConversationRepository(db)
    conv = repo.create(title="Test Conversation")

    assert conv.id is not None
    assert conv.title == "Test Conversation"
    assert conv.created_at != ""


def test_get_conversation_by_id(db: Database) -> None:
    """Should retrieve a conversation by ID."""
    repo = ConversationRepository(db)
    created = repo.create(title="Find Me")
    found = repo.get_by_id(created.id)

    assert found is not None
    assert found.title == "Find Me"


def test_list_conversations(db: Database) -> None:
    """Should list all conversations, newest first."""
    repo = ConversationRepository(db)
    repo.create(title="First")
    repo.create(title="Second")
    repo.create(title="Third")

    convs = repo.list_all()
    assert len(convs) == 3
    # Newest first (Third was created last)
    assert convs[0].title == "Third"


def test_update_conversation_title(db: Database) -> None:
    """Should update a conversation's title."""
    repo = ConversationRepository(db)
    conv = repo.create(title="Old Title")
    repo.update_title(conv.id, "New Title")

    updated = repo.get_by_id(conv.id)
    assert updated.title == "New Title"


def test_delete_conversation_cascades(db: Database) -> None:
    """Deleting a conversation should cascade-delete its messages."""
    conv_repo = ConversationRepository(db)
    msg_repo = MessageRepository(db)

    conv = conv_repo.create(title="To Delete")
    msg_repo.add(conv.id, "user", "Hello")
    msg_repo.add(conv.id, "assistant", "Hi there")

    assert msg_repo.count() == 2

    conv_repo.delete(conv.id)

    assert conv_repo.count() == 0
    # Messages should be cascade-deleted
    msgs = msg_repo.list_by_conversation(conv.id)
    assert len(msgs) == 0


def test_add_message(db: Database) -> None:
    """Adding a message should return it with an ID."""
    conv_repo = ConversationRepository(db)
    msg_repo = MessageRepository(db)

    conv = conv_repo.create(title="Msg Test")
    msg = msg_repo.add(conv.id, "user", "Test prompt")

    assert msg.id is not None
    assert msg.conversation_id == conv.id
    assert msg.role == "user"
    assert msg.content == "Test prompt"
    assert msg.created_at != ""


def test_list_messages_by_conversation(db: Database) -> None:
    """Should list messages in chronological order."""
    conv_repo = ConversationRepository(db)
    msg_repo = MessageRepository(db)

    conv = conv_repo.create(title="History Test")
    msg_repo.add(conv.id, "user", "First")
    msg_repo.add(conv.id, "assistant", "Second")
    msg_repo.add(conv.id, "user", "Third")

    msgs = msg_repo.list_by_conversation(conv.id)
    assert len(msgs) == 3
    assert msgs[0].content == "First"
    assert msgs[1].content == "Second"
    assert msgs[2].content == "Third"


def test_message_count(db: Database) -> None:
    """Should return total message count."""
    conv_repo = ConversationRepository(db)
    msg_repo = MessageRepository(db)

    conv = conv_repo.create(title="Count Test")
    msg_repo.add(conv.id, "user", "A")
    msg_repo.add(conv.id, "assistant", "B")

    assert msg_repo.count() == 2


def test_conversation_count(db: Database) -> None:
    """Should return total conversation count."""
    conv_repo = ConversationRepository(db)
    conv_repo.create(title="1")
    conv_repo.create(title="2")

    assert conv_repo.count() == 2


def test_get_nonexistent_conversation(db: Database) -> None:
    """Getting a non-existent conversation should return None."""
    conv_repo = ConversationRepository(db)
    result = conv_repo.get_by_id(99999)
    assert result is None


def test_update_message_content(db: Database) -> None:
    """update_content should persist new content, leaving other fields alone."""
    conv_repo = ConversationRepository(db)
    msg_repo = MessageRepository(db)

    conv = conv_repo.create(title="Update Test")
    msg = msg_repo.add(conv.id, "assistant", "")

    msg_repo.update_content(msg.id, "streamed response")

    updated = msg_repo.get_by_id(msg.id)
    assert updated is not None
    assert updated.content == "streamed response"
    assert updated.role == "assistant"
    assert updated.conversation_id == conv.id


def test_list_messages_orders_tied_timestamps_by_id(db: Database) -> None:
    """Messages sharing one created_at second must still list oldest-first.

    created_at has second precision (datetime('now')), and the UI inserts
    the user row and the assistant placeholder back-to-back, so ties happen
    on every exchange. id is monotonic with insertion, giving a
    deterministic total order regardless of sorter tie behavior.
    """
    convs = ConversationRepository(db)
    msgs = MessageRepository(db)
    conv = convs.create(title="Tie")

    # Force identical timestamps directly: the worst-case tie.
    conn = db.get_connection()
    with conn:
        for i in range(5):
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at)"
                " VALUES (?, 'user', ?, '2026-01-01 12:00:00')",
                (conv.id, f"msg-{i}"),
            )

    listed = msgs.list_by_conversation(conv.id)
    assert [m.content for m in listed] == [f"msg-{i}" for i in range(5)]


def test_rapid_inserts_list_in_insertion_order(db: Database) -> None:
    """Back-to-back inserts (the real UI pattern) list oldest-first."""
    convs = ConversationRepository(db)
    msgs = MessageRepository(db)
    conv = convs.create(title="Rapid")

    msgs.add(conv.id, "user", "hello")
    msgs.add(conv.id, "assistant", "")

    listed = msgs.list_by_conversation(conv.id)
    assert [m.role for m in listed] == ["user", "assistant"]
