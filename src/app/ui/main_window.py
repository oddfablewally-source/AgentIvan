"""
Main application window.

This is the central GUI component. It:
- Provides menu bar and status bar.
- Hosts the chat interface (message history, multi-line input, send/cancel).
- Shows a connection indicator for the Ollama backend.
- Manages background workers for AI requests.
- Supports system tray integration.
- Handles clean shutdown (waiting for workers to finish).

The window receives an AppContext and uses its services. It does NOT
create services directly — that's the AppContext's job.

Presentation-only module: every backend interaction goes through the
existing AppContext seams (conversations, messages, chat_provider,
ollama.test_connection) and the existing ChatWorker. No backend
behavior is changed here.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.app.ai.provider import ProviderMessage
from src.app.core.app_context import AppContext
from src.app.core.logging_setup import get_logger
from src.app.core.paths import APP_NAME
from src.app.data.repositories import Conversation, Message
from src.app.ui.chat_input import ChatInput
from src.app.ui.chat_worker import ChatWorker
from src.app.utils.helpers import escape_html, format_timestamp

logger = get_logger("ui.main_window")

# Role presentation: label shown above each message block.
_ROLE_LABELS = {
    "user": ("You", "#89b4fa"),      # blue
    "assistant": ("Ivan", "#a6e3a1"),  # green
    "system": ("System", "#9399b2"),   # gray
}


class _ConnectionCheckWorker(QThread):
    """Runs OllamaService.test_connection() off the GUI thread."""

    finished_check = Signal(bool, str)

    def __init__(self, service, parent=None) -> None:
        super().__init__(parent)
        self._service = service

    def run(self) -> None:  # noqa: N802 (Qt naming)
        ok, message = self._service.test_connection()
        self.finished_check.emit(ok, message)


class _ConnIndicator(QWidget):
    """Clickable status-bar connection indicator.

    A colored dot plus label (QLabels render rich text; QPushButton does
    not). Clicking re-checks the backend connection.
    """

    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)
        self._dot = QLabel()
        self._text = QLabel()
        self._text.setObjectName("connLabel")
        layout.addWidget(self._dot)
        layout.addWidget(self._text)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Check connection to Ollama")

    def set_state(self, color: str, label: str, detail: str = "") -> None:
        self._dot.setText(f'<span style="color:{color}; font-size:14px;">\u25cf</span>')
        self._text.setText(label)
        self.setToolTip(detail or "Check connection to Ollama")

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.clicked.emit()
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    """
    Main application window with chat interface.
    """

    close_requested = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.ctx = context
        self._worker: Optional[ChatWorker] = None
        self._conn_worker: Optional[_ConnectionCheckWorker] = None
        self._current_conversation: Optional[Conversation] = None
        self._tray_icon: Optional[QSystemTrayIcon] = None
        # Cursor anchored at the end of the chat document; all message
        # rendering (including streamed tokens) goes through it so text
        # always lands inside the current message block.
        self._end_cursor: Optional[QTextCursor] = None
        self._stream_body_started = False

        self._setup_window()
        self._setup_menu_bar()
        self._setup_central_widget()
        self._setup_status_bar()
        self._setup_system_tray()
        self._load_conversations()
        self._new_conversation()
        # Check backend connectivity after the window is on screen so a
        # slow/unreachable Ollama never blocks startup painting.
        QTimer.singleShot(400, self._check_connection)

    # --- Window setup ---

    def _setup_window(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.resize(
            self.ctx.config.get("window_width", 1000),
            self.ctx.config.get("window_height", 660),
        )
        self.setMinimumSize(680, 460)

    def _setup_menu_bar(self) -> None:
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        self._new_action = QAction("&New Conversation", self)
        self._new_action.setShortcut("Ctrl+N")
        self._new_action.triggered.connect(self._new_conversation)
        file_menu.addAction(self._new_action)

        self._delete_action = QAction("&Delete Conversation", self)
        self._delete_action.triggered.connect(self._delete_conversation)
        file_menu.addAction(self._delete_action)

        file_menu.addSeparator()

        self._settings_action = QAction("&Settings...", self)
        self._settings_action.setShortcut("Ctrl+,")
        self._settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(self._settings_action)

        file_menu.addSeparator()

        self._quit_action = QAction("&Quit", self)
        self._quit_action.setShortcut("Ctrl+Q")
        self._quit_action.triggered.connect(self.close)
        file_menu.addAction(self._quit_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        self._about_action = QAction("&About", self)
        self._about_action.triggered.connect(self._open_about)
        help_menu.addAction(self._about_action)

    def _setup_central_widget(self) -> None:
        """Create the main layout: conversation list + chat area."""
        splitter = QSplitter(Qt.Horizontal)

        # --- Left panel: conversation list ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 4, 8)
        left_layout.setSpacing(6)

        left_header = QWidget()
        left_header_layout = QHBoxLayout(left_header)
        left_header_layout.setContentsMargins(0, 0, 0, 0)

        left_label = QLabel("Conversations")
        left_label.setObjectName("panelHeader")
        left_header_layout.addWidget(left_label)
        left_header_layout.addStretch(1)

        self._new_conv_button = QPushButton("New")
        self._new_conv_button.setObjectName("newConvButton")
        self._new_conv_button.setToolTip("Start a new conversation (Ctrl+N)")
        self._new_conv_button.clicked.connect(self._new_conversation)
        left_header_layout.addWidget(self._new_conv_button)

        left_layout.addWidget(left_header)

        self._conversation_list = QListWidget()
        self._conversation_list.itemClicked.connect(self._on_conversation_selected)
        left_layout.addWidget(self._conversation_list)

        splitter.addWidget(left_panel)

        # --- Right panel: chat area ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 8, 8, 8)
        right_layout.setSpacing(8)

        self._chat_display = QTextEdit()
        self._chat_display.setObjectName("chatDisplay")
        self._chat_display.setReadOnly(True)
        self._chat_display.setPlaceholderText("Start a conversation...")
        right_layout.addWidget(self._chat_display, 1)

        # Input row: multi-line input + Send + Cancel
        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)

        self._prompt_input = ChatInput()
        self._prompt_input.send_requested.connect(self._send_prompt)
        input_layout.addWidget(self._prompt_input, 1)

        input_buttons = QVBoxLayout()
        input_buttons.setSpacing(6)

        self._send_button = QPushButton("Send")
        self._send_button.setObjectName("sendButton")
        self._send_button.setToolTip("Send message (Enter)")
        self._send_button.clicked.connect(self._send_prompt)
        input_buttons.addWidget(self._send_button)

        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setObjectName("cancelButton")
        self._cancel_button.setToolTip("Stop generating")
        self._cancel_button.clicked.connect(self._cancel_request)
        self._cancel_button.setVisible(False)
        input_buttons.addWidget(self._cancel_button)

        input_buttons.addStretch(1)
        input_layout.addLayout(input_buttons)

        right_layout.addLayout(input_layout)

        splitter.addWidget(right_panel)
        splitter.setSizes([220, 780])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(splitter)

    def _setup_status_bar(self) -> None:
        self._status_label = QLabel("Ready")
        self.statusBar().addWidget(self._status_label, 1)

        # Connection indicator: clickable, re-checks on click.
        self._conn_indicator = _ConnIndicator()
        self._conn_indicator.clicked.connect(self._check_connection)
        self._set_connection_state("checking", "Checking connection...")
        self.statusBar().addPermanentWidget(self._conn_indicator)

    def _setup_system_tray(self) -> None:
        """Set up system tray icon if enabled in config."""
        if not self.ctx.config.get("system_tray", True):
            return

        # Create a simple icon (a colored square since we don't bundle a real icon)
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.darkBlue)
        icon = QIcon(pixmap)

        self._tray_icon = QSystemTrayIcon(icon, self)
        self._tray_icon.setToolTip(APP_NAME)
        self._tray_icon.activated.connect(self._on_tray_activated)

        self._tray_icon.show()

    def _on_tray_activated(self, reason) -> None:
        """Handle system tray activation."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.raise_()
                self.activateWindow()

    # --- Connection indicator ---

    def _set_connection_state(self, state: str, detail: str = "") -> None:
        """Update the status-bar connection indicator.

        state: "checking" | "ok" | "down".
        """
        colors = {"checking": "#9399b2", "ok": "#a6e3a1", "down": "#f38ba8"}
        labels = {"checking": "Checking...", "ok": "Connected", "down": "Not connected"}
        color = colors[state]
        label = labels[state]
        if state == "ok":
            model = self.ctx.ollama.model
            text = f"Connected \u00b7 {escape_html(model)}"
        else:
            text = label
        self._conn_indicator.set_state(color, text, detail)

    def _check_connection(self) -> None:
        """Test Ollama connectivity on a worker thread; never blocks the UI."""
        if self._conn_worker is not None and self._conn_worker.isRunning():
            return
        self._set_connection_state("checking", "Checking connection...")
        self._conn_worker = _ConnectionCheckWorker(self.ctx.ollama, self)
        self._conn_worker.finished_check.connect(self._on_connection_checked)
        self._conn_worker.finished.connect(self._on_conn_worker_finished)
        self._conn_worker.start()

    def _on_connection_checked(self, ok: bool, message: str) -> None:
        self._set_connection_state("ok" if ok else "down", message)

    def _on_conn_worker_finished(self) -> None:
        if self._conn_worker:
            self._conn_worker.deleteLater()
            self._conn_worker = None

    # --- Conversation management ---

    def _load_conversations(self) -> None:
        """Load all conversations into the list widget."""
        self._conversation_list.clear()
        conversations = self.ctx.conversations.list_all()
        for conv in conversations:
            item = QListWidgetItem(conv.title)
            item.setData(Qt.UserRole, conv.id)
            self._conversation_list.addItem(item)

    def _new_conversation(self) -> None:
        """Create a new conversation and select it."""
        if self._worker is not None and self._worker.isRunning():
            return
        conv = self.ctx.conversations.create(title="New Conversation")
        self._current_conversation = conv
        self._load_conversations()
        self._select_conversation(conv.id)
        self._chat_display.clear()
        self._reset_end_cursor()
        self._prompt_input.setFocus()

    def _delete_conversation(self) -> None:
        """Delete the current conversation."""
        if self._current_conversation is None:
            return
        conv_id = self._current_conversation.id
        self.ctx.conversations.delete(conv_id)
        self._current_conversation = None
        self._chat_display.clear()
        self._reset_end_cursor()
        self._load_conversations()
        self._status_label.setText("Conversation deleted.")

    def _select_conversation(self, conv_id: int) -> None:
        """Select a conversation in the list and load its messages."""
        for i in range(self._conversation_list.count()):
            item = self._conversation_list.item(i)
            if item.data(Qt.UserRole) == conv_id:
                self._conversation_list.setCurrentRow(i)
                break

        conv = self.ctx.conversations.get_by_id(conv_id)
        if conv:
            self._current_conversation = conv
            self._display_messages(conv_id)

    def _on_conversation_selected(self, item: QListWidgetItem) -> None:
        """Handle conversation list item click."""
        if self._worker is not None and self._worker.isRunning():
            # Don't yank the transcript out from under a live response:
            # snap the highlight back to the active conversation.
            if self._current_conversation is not None:
                self._select_conversation(self._current_conversation.id)
            return
        conv_id = item.data(Qt.UserRole)
        self._select_conversation(conv_id)

    # --- Message rendering (presentation only) ---

    def _reset_end_cursor(self) -> None:
        self._end_cursor = QTextCursor(self._chat_display.document())
        self._end_cursor.movePosition(QTextCursor.End)

    def _scroll_to_bottom_if_pinned(self) -> None:
        """Keep the view pinned to the bottom while new content arrives,
        but never yank it away if the user scrolled up to read."""
        bar = self._chat_display.verticalScrollBar()
        if bar.value() >= bar.maximum() - 20:
            bar.setValue(bar.maximum())

    def _start_new_block(self) -> QTextCursor:
        """Return the end cursor positioned at the start of a fresh block.

        insertHtml lands inside the current block, so a new block must be
        opened first -- except on an empty document, where that would add
        a stray leading blank line.
        """
        cursor = self._end_cursor
        if not cursor.document().isEmpty():
            cursor.insertBlock()
        return cursor

    def _body_char_format(self) -> QTextCharFormat:
        """Explicit format for message body text.

        The cursor's format after insertHtml is the trailing header format
        (the small gray timestamp), so the body format must be set
        explicitly rather than inherited.
        """
        fmt = QTextCharFormat()
        fmt.setFont(self._chat_display.font())
        fmt.setForeground(QBrush(QColor("#cdd6f4")))
        return fmt

    def _append_message_block(self, role: str, content: str, timestamp: str = "") -> None:
        """Render one complete message block at the end of the transcript."""
        label, color = _ROLE_LABELS.get(role, (role.capitalize(), "#9399b2"))
        ts = format_timestamp(timestamp) if timestamp else ""
        ts_html = (
            f' <span style="color:#585b70; font-size:11px;">{escape_html(ts)}</span>'
            if ts
            else ""
        )
        cursor = self._start_new_block()
        cursor.insertHtml(
            f'<p style="margin:14px 0 4px 0;">'
            f'<span style="color:{color}; font-weight:600; font-size:14px;">'
            f'{escape_html(label)}</span>{ts_html}</p>'
        )
        cursor.insertBlock()
        cursor.setCharFormat(self._body_char_format())
        cursor.insertText(content)
        self._scroll_to_bottom_if_pinned()

    def _begin_assistant_block(self, timestamp: str = "") -> None:
        """Insert Ivan's header; the body block starts on the first token."""
        label, color = _ROLE_LABELS["assistant"]
        ts = format_timestamp(timestamp) if timestamp else ""
        ts_html = (
            f' <span style="color:#585b70; font-size:11px;">{escape_html(ts)}</span>'
            if ts
            else ""
        )
        cursor = self._start_new_block()
        cursor.insertHtml(
            f'<p style="margin:14px 0 4px 0;">'
            f'<span style="color:{color}; font-weight:600; font-size:14px;">'
            f'{escape_html(label)}</span>{ts_html}</p>'
        )
        self._stream_body_started = False
        self._scroll_to_bottom_if_pinned()

    def _append_stream_token(self, token: str) -> None:
        """Append one streamed token inside Ivan's current body block."""
        cursor = self._end_cursor
        if not self._stream_body_started:
            cursor.insertBlock()
            cursor.setCharFormat(self._body_char_format())
            self._stream_body_started = True
        cursor.insertText(token)
        self._scroll_to_bottom_if_pinned()

    def _append_error_block(self, error: str) -> None:
        """Render a transient, clearly-styled error notice (not persisted)."""
        cursor = self._start_new_block()
        cursor.insertHtml(
            '<p style="margin:6px 0 14px 0; padding:10px 12px; '
            'background-color:#2b1a1e; border:1px solid #f38ba8; border-radius:8px;">'
            '<span style="color:#f38ba8; font-weight:600; font-size:14px;">'
            'Ivan couldn\u2019t respond</span><br>'
            f'<span style="color:#cdd6f4; font-size:13px;">{escape_html(error)}</span><br>'
            '<span style="color:#9399b2; font-size:12px;">'
            'Check that Ollama is running and the model is available, then try again.'
            '</span></p>'
        )
        self._scroll_to_bottom_if_pinned()

    def _display_messages(self, conv_id: int) -> None:
        """Display all messages from a conversation in the chat area."""
        self._chat_display.clear()
        self._reset_end_cursor()
        messages = self.ctx.messages.list_by_conversation(conv_id)
        for msg in messages:
            # Empty assistant messages are failed/streaming placeholders;
            # they carry no content worth rendering.
            if msg.role == "assistant" and not (msg.content or "").strip():
                continue
            self._append_message_block(msg.role, msg.content, msg.created_at)

    # --- Chat / AI request handling ---

    def _send_prompt(self) -> None:
        """Send the current prompt to the backend and stream the response."""
        # Don't allow sending if a worker is already running
        if self._worker is not None and self._worker.isRunning():
            return

        prompt_text = self._prompt_input.take_text()
        if not prompt_text:
            return

        if self._current_conversation is None:
            self._new_conversation()

        conv_id = self._current_conversation.id

        # Save the user message
        user_msg = self.ctx.messages.add(conv_id, "user", prompt_text)
        self._append_message_block(user_msg.role, user_msg.content, user_msg.created_at)

        # Update conversation title if it's the first message
        if self.ctx.messages.count() <= 1 or len(self.ctx.messages.list_by_conversation(conv_id)) <= 1:
            title = prompt_text[:50] + ("..." if len(prompt_text) > 50 else "")
            self.ctx.conversations.update_title(conv_id, title)
            self._current_conversation.title = title
            self._load_conversations()
            self._select_conversation(conv_id)
            # _select_conversation re-rendered the transcript; re-add nothing:
            # the user message is already persisted and now displayed.

        # Prepare chat history
        history = self.ctx.messages.list_by_conversation(conv_id)
        chat_messages = [
            ProviderMessage(
                role=m.role, content=m.content
            )
            for m in history
        ]

        # Add assistant placeholder message (persisted on success)
        assistant_msg = self.ctx.messages.add(conv_id, "assistant", "")
        self._begin_assistant_block(assistant_msg.created_at)

        # Set up the worker (unchanged communication path)
        system_prompt = self.ctx.config.get("system_prompt", "")
        self._worker = ChatWorker(
            self.ctx.chat_provider,
            chat_messages,
            system_prompt=system_prompt,
        )

        # Connect signals
        self._worker.token_received.connect(
            lambda token: self._append_stream_token(token)
        )
        self._worker.finished_success.connect(
            lambda resp: self._on_chat_success(resp, conv_id, assistant_msg.id)
        )
        self._worker.failed.connect(
            lambda err: self._on_chat_failed(err, conv_id, assistant_msg.id)
        )
        self._worker.finished.connect(self._on_worker_finished)

        # Update UI state
        self._set_generating_state(True)
        self._status_label.setText("Ivan is responding...")

        # Start the worker thread
        self._worker.start()

    def _set_generating_state(self, generating: bool) -> None:
        """Toggle input controls between idle and generating states."""
        self._send_button.setEnabled(not generating)
        self._prompt_input.setEnabled(not generating)
        self._cancel_button.setVisible(generating)
        self._new_conv_button.setEnabled(not generating)

    def _on_chat_success(self, response: str, conv_id: int, msg_id: int) -> None:
        """Handle successful chat completion."""
        # Persist the full response, then re-render from the database so
        # the transcript always matches what is stored.
        self._update_message_content(msg_id, response)
        self._status_label.setText("Response complete.")
        self._reload_conversation(conv_id)
        self._prompt_input.setFocus()

    def _on_chat_failed(self, error: str, conv_id: int, msg_id: int) -> None:
        """Handle chat failure with a clean, transient error display."""
        # Keep the failed placeholder out of the persisted transcript.
        self._update_message_content(msg_id, "")
        self._append_error_block(error)
        # Short status text: the full error already lives in the chat block,
        # and a long message would squeeze the connection indicator.
        self._status_label.setText("Error: backend unreachable.")
        self._status_label.setToolTip(error)
        # Re-check the connection indicator so it reflects reality.
        self._check_connection()
        self._prompt_input.setFocus()

    def _on_worker_finished(self) -> None:
        """Clean up after worker thread completes (success or failure)."""
        self._set_generating_state(False)
        # The worker thread object will be cleaned up by Qt's parent mechanism
        if self._worker:
            self._worker.deleteLater()
            self._worker = None

    def _cancel_request(self) -> None:
        """Request cancellation of the current AI request."""
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._status_label.setText("Cancelling...")
            self._cancel_button.setEnabled(False)

    def _update_message_content(self, msg_id: int, content: str) -> None:
        """Update a message's content in the database."""
        self.ctx.messages.update_content(msg_id, content)

    def _reload_conversation(self, conv_id: int) -> None:
        """Reload the conversation display."""
        self._display_messages(conv_id)

    # --- Dialogs ---

    def _open_settings(self) -> None:
        """Open the settings dialog."""
        from src.app.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self.ctx.config, self)
        if dialog.exec():
            # Settings were saved; host/model may have changed.
            self._status_label.setText("Settings updated.")
            self._check_connection()
            # Reload conversation list in case nothing else changed
            self._load_conversations()

    def _open_about(self) -> None:
        """Open the about dialog."""
        from src.app.ui.about_dialog import AboutDialog

        dialog = AboutDialog(self)
        dialog.exec()

    # --- Window close / shutdown ---

    def closeEvent(self, event) -> None:
        """
        Handle window close event.

        Ensures background workers are properly stopped before the
        application exits. This prevents hangs and resource leaks.
        """
        # Save window geometry
        self.ctx.config.set("window_width", self.width())
        self.ctx.config.set("window_height", self.height())

        # Stop any running worker
        if self._worker and self._worker.isRunning():
            logger.info("Waiting for worker thread to finish...")
            self._worker.requestInterruption()
            self._worker.wait(5000)  # Wait up to 5 seconds
            if self._worker.isRunning():
                logger.warning("Worker did not stop within timeout.")
        if self._conn_worker and self._conn_worker.isRunning():
            self._conn_worker.wait(2000)

        # Hide tray icon
        if self._tray_icon:
            self._tray_icon.hide()

        # Shutdown context (closes DB connections)
        self.ctx.shutdown()

        event.accept()
