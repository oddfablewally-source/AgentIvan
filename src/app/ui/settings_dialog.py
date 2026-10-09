"""
Settings dialog for configuring Ollama connection and app preferences.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.app.ai.ollama_service import OllamaService
from src.app.core.app_context import AppContext
from src.app.core.logging_setup import get_logger

logger = get_logger("ui.settings")


class SettingsDialog(QDialog):
    """Dialog for editing application settings."""

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("Settings")
        self.setMinimumWidth(450)

        self._setup_ui()
        self._load_values()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # --- Ollama settings group ---
        ollama_group = QGroupBox("Ollama Connection")
        ollama_form = QFormLayout(ollama_group)

        self._host_input = QLineEdit()
        self._host_input.setPlaceholderText("http://localhost:11434")
        ollama_form.addRow("Host:", self._host_input)

        self._model_input = QLineEdit()
        self._model_input.setPlaceholderText("llama3.2")
        ollama_form.addRow("Model:", self._model_input)

        self._timeout_input = QSpinBox()
        self._timeout_input.setRange(10, 600)
        self._timeout_input.setSuffix(" seconds")
        ollama_form.addRow("Timeout:", self._timeout_input)

        # Test connection button
        test_layout = QHBoxLayout()
        self._test_button = QPushButton("Test Connection")
        self._test_button.clicked.connect(self._test_connection)
        self._test_result = QLabel("")
        test_layout.addWidget(self._test_button)
        test_layout.addWidget(self._test_result)
        test_layout.addStretch()
        ollama_form.addRow("", test_layout)

        # System prompt
        self._system_prompt_input = QTextEdit()
        self._system_prompt_input.setMaximumHeight(80)
        ollama_form.addRow("System Prompt:", self._system_prompt_input)

        layout.addWidget(ollama_group)

        # --- Application settings group ---
        app_group = QGroupBox("Application")
        app_form = QFormLayout(app_group)

        self._tray_checkbox = QCheckBox("Enable system tray")
        app_form.addRow("", self._tray_checkbox)

        layout.addWidget(app_group)

        # --- Buttons ---
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

    def _load_values(self) -> None:
        """Load current config values into the form fields."""
        self._host_input.setText(self._config.get("ollama_host", ""))
        self._model_input.setText(self._config.get("ollama_model", ""))
        self._timeout_input.setValue(self._config.get("ollama_timeout", 120))
        self._system_prompt_input.setPlainText(
            self._config.get("system_prompt", "")
        )
        self._tray_checkbox.setChecked(
            self._config.get("system_tray", True)
        )

    def _test_connection(self) -> None:
        """Test the connection to Ollama with the current host value."""
        host = self._host_input.text().strip()
        if not host:
            self._test_result.setText("Enter a host first.")
            return

        self._test_result.setText("Testing...")
        QApplication.processEvents()

        # Create a temporary service to test
        service = OllamaService(
            host=host,
            model=self._model_input.text().strip(),
            timeout=self._timeout_input.value(),
        )

        success, message = service.test_connection()
        if success:
            self._test_result.setText(f'<span style="color: green;">{message}</span>')
            # Also check if the model is available
            try:
                models = service.list_models()
                model_name = self._model_input.text().strip()
                if model_name:
                    found = any(m.name == model_name for m in models)
                    if not found:
                        self._test_result.setText(
                            f'<span style="color: orange;">'
                            f'Connected, but model "{model_name}" not found. '
                            f'Available: {", ".join(m.name for m in models)}</span>'
                        )
            except Exception as e:
                self._test_result.setText(
                    f'<span style="color: orange;">Connected, but could not list models: {e}</span>'
                )
        else:
            self._test_result.setText(f'<span style="color: red;">{message}</span>')

    def _save(self) -> None:
        """Save settings to config."""
        self._config.update({
            "ollama_host": self._host_input.text().strip(),
            "ollama_model": self._model_input.text().strip(),
            "ollama_timeout": self._timeout_input.value(),
            "system_prompt": self._system_prompt_input.toPlainText().strip(),
            "system_tray": self._tray_checkbox.isChecked(),
        })
        self.accept()
