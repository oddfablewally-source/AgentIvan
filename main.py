"""
Entry point for the AgentIvan application.

This module is what you run directly:

    python main.py

It sets up high DPI, configures application-level exception handling,
creates the QApplication, and launches the main window.

When packaged with PyInstaller, this script is the entry point for the
generated executable.

It also provides a headless smoke test used to verify packaged builds:

    python main.py --self-test

The self-test constructs a QApplication (offscreen), builds the full
AppContext, performs a small database round-trip, and shuts down
cleanly. It exits 0 on success, non-zero on any failure. It writes to
the normal user data directory, so run it with a redirected HOME
(Linux/macOS) or LOCALAPPDATA (Windows) to keep it hermetic;
scripts/package.py does this automatically.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.app.core.app_context import AppContext
from src.app.core.logging_setup import setup_logging
from src.app.core.paths import APP_NAME, APP_VERSION, get_log_dir
from src.app.ui.main_window import MainWindow
from src.app.ui.style import apply_dark_style


def self_test() -> int:
    """Headless startup smoke test for (packaged) builds.

    Exercises the highest-risk parts of a frozen application: Qt
    platform plugin loading, bundled-code imports, user data directory
    creation, SQLite initialization/migrations, config load, and clean
    shutdown. Prints a single "self-test OK" line and returns 0 when
    everything works.
    """
    log_dir = get_log_dir()
    log_file = log_dir / "app.log"
    setup_logging(log_file)

    app = QApplication([])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    try:
        context = AppContext()
    except Exception:
        import logging

        logging.getLogger("app").exception(
            "self-test failed while building AppContext"
        )
        return 1

    try:
        if context.chat_provider is None:
            print("self-test FAILED: chat_provider is None")
            return 1
        # Small database round-trip: proves SQLite works in this env.
        convo = context.conversations.create("self-test")
        fetched = context.conversations.get_by_id(convo.id)
        context.conversations.delete(convo.id)
        if fetched is None or fetched.id != convo.id:
            print("self-test FAILED: database round-trip mismatch")
            return 1
    except Exception:
        import logging

        logging.getLogger("app").exception("self-test failed")
        return 1
    finally:
        context.shutdown()

    print(f"self-test OK: {APP_NAME} {APP_VERSION}")
    return 0


def main() -> int:
    # --- Setup logging first so everything else can use it ---
    log_dir = get_log_dir()
    log_file = log_dir / "app.log"
    setup_logging(log_file)

    # --- High DPI attributes (must be set before QApplication) ---
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("LocalAI")

    apply_dark_style(app)

    # --- Global exception hook: log and show user-friendly dialog ---
    def _excepthook(exc_type, exc_value, exc_tb):
        import traceback
        from src.app.ui.dialogs import show_unhandled_exception
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        # Log it
        import logging
        logging.getLogger("app").critical(
            "Unhandled exception:\n%s", tb_text
        )
        # Show user-friendly dialog (only if QApplication is alive)
        if app.instance() is not None:
            show_unhandled_exception(tb_text)

    sys.excepthook = _excepthook

    # --- Build application context (services, config, db) ---
    context = AppContext()

    window = MainWindow(context)
    window.show()

    try:
        return app.exec()
    finally:
        # Guaranteed cleanup: the context owns its resources, so the entry
        # point owns their release -- even if the window's closeEvent never
        # ran. shutdown() is idempotent, so a prior closeEvent call (which
        # does GUI-specific teardown first) is harmless.
        context.shutdown()


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        # Offscreen platform: no display needed. Qt reads this variable
        # when the QApplication is constructed, so setting it here --
        # before self_test() runs -- is sufficient.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.exit(self_test())
    sys.exit(main())
