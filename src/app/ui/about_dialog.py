"""
About dialog showing application information.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from src.app.core.paths import APP_NAME, APP_VERSION


class AboutDialog(QDialog):
    """Simple about dialog."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About")
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)

        title = QLabel(f"<h2>{APP_NAME}</h2>")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        version = QLabel(f"Version {APP_VERSION}")
        version.setAlignment(Qt.AlignCenter)
        layout.addWidget(version)

        description = QLabel(
            "A local-first AI desktop application boilerplate.\n"
            "Powered by Ollama and PySide6."
        )
        description.setAlignment(Qt.AlignCenter)
        layout.addWidget(description)

        tech = QLabel(
            "<p style='color: gray; font-size: 11px;'>"
            "Python + PySide6 + SQLite + Ollama"
            "</p>"
        )
        tech.setAlignment(Qt.AlignCenter)
        layout.addWidget(tech)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
