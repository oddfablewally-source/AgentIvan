"""
Multi-line chat input box.

A QTextEdit with chat-style keyboard behavior:
- Enter sends the message.
- Shift+Enter inserts a newline.

This is pure presentation: it only emits `send_requested` and never
talks to any backend service.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTextEdit


class ChatInput(QTextEdit):
    """Multi-line prompt input with Enter-to-send semantics."""

    send_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("chatInput")
        self.setPlaceholderText("Message Ivan...  (Enter to send, Shift+Enter for a new line)")
        self.setAcceptRichText(False)
        # Comfortable fixed height: a few lines of text, not a growing box.
        self.setFixedHeight(78)
        self.setTabChangesFocus(True)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Enter sends; Shift+Enter (and everything else) behaves normally."""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
            event.modifiers() & Qt.ShiftModifier
        ):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def take_text(self) -> str:
        """Return the stripped input and clear the box."""
        text = self.toPlainText().strip()
        self.clear()
        return text
