"""
Shared dialog helper functions.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox


def show_unhandled_exception(tb_text: str) -> None:
    """
    Show a user-friendly dialog for an unhandled exception.

    Includes the full traceback for diagnostic purposes.
    """
    msg_box = QMessageBox()
    msg_box.setIcon(QMessageBox.Critical)
    msg_box.setWindowTitle("Unexpected Error")
    msg_box.setText(
        "An unexpected error occurred. The application may become unstable.\n\n"
        "Please check the log file for details."
    )
    msg_box.setDetailedText(tb_text)
    msg_box.setStandardButtons(QMessageBox.Ok)
    msg_box.exec()
