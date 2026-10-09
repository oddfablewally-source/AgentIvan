"""
Application stylesheet (dark theme).

Applied globally to the QApplication.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication


DARK_QSS = """
QMainWindow, QDialog {
    background-color: #1e1e2e;
    color: #cdd6f4;
}

QMenuBar {
    background-color: #181825;
    color: #cdd6f4;
    border-bottom: 1px solid #313244;
}

QMenuBar::item:selected {
    background-color: #313244;
}

QMenu {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #313244;
}

QMenu::item:selected {
    background-color: #313244;
}

QToolBar {
    background-color: #181825;
    border-bottom: 1px solid #313244;
    padding: 4px;
}

QStatusBar {
    background-color: #181825;
    color: #a6adc8;
    border-top: 1px solid #313244;
}

QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 13px;
}

QPushButton:hover {
    background-color: #45475a;
}

QPushButton:pressed {
    background-color: #11111b;
}

QPushButton:disabled {
    background-color: #181825;
    color: #585b70;
}

QTextEdit {
    background-color: #11111b;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 8px;
    font-size: 14px;
}

QLineEdit {
    background-color: #11111b;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 6px;
    font-size: 14px;
}

QLineEdit:focus {
    border-color: #89b4fa;
}

QComboBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 4px 8px;
}

QListWidget {
    background-color: #11111b;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 6px;
}

QListWidget::item:selected {
    background-color: #313244;
}

QLabel {
    color: #cdd6f4;
}

QGroupBox {
    color: #a6adc8;
    border: 1px solid #313244;
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 8px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}

QScrollBar:vertical {
    background: #181825;
    width: 10px;
}

QScrollBar::handle:vertical {
    background: #45475a;
    border-radius: 5px;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QCheckBox {
    color: #cdd6f4;
}

QSpinBox {
    background-color: #11111b;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 4px;
    padding: 4px;
}

#chatDisplay {
    background-color: #11111b;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 12px;
    font-size: 15px;
}

#chatInput {
    background-color: #11111b;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 14px;
}

#chatInput:focus {
    border-color: #89b4fa;
}

#sendButton {
    background-color: #89b4fa;
    color: #11111b;
    border: none;
    border-radius: 6px;
    padding: 10px 22px;
    font-size: 14px;
    font-weight: 600;
}

#sendButton:hover {
    background-color: #b4befe;
}

#sendButton:pressed {
    background-color: #74c7ec;
}

#sendButton:disabled {
    background-color: #313244;
    color: #585b70;
}

#cancelButton {
    background-color: #313244;
    color: #f38ba8;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
}

#cancelButton:hover {
    background-color: #45475a;
}

#newConvButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 4px 12px;
    font-size: 12px;
}

#newConvButton:hover {
    background-color: #45475a;
}

#newConvButton:disabled {
    background-color: #181825;
    color: #585b70;
}

#connLabel {
    color: #a6adc8;
    font-size: 12px;
}

#panelHeader {
    color: #a6adc8;
    font-size: 12px;
    font-weight: 600;
}
"""


def apply_dark_style(app: QApplication) -> None:
    """Apply the dark stylesheet to the application."""
    app.setStyleSheet(DARK_QSS)
