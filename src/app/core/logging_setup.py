"""
Application logging setup.

Configures file logging with rotation and console output.
All modules use the logger named 'app'.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_file: Path) -> None:
    """
    Configure application-wide logging.

    - Writes to a rotating file (5 MB max, 3 backups).
    - Also outputs to console (useful in development).
    - Sets up root logger and 'app' logger.
    - Idempotent: if the 'app' logger already has handlers, the call is a
      no-op, so repeated setup never duplicates handlers or log output.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler with rotation
    file_handler = RotatingFileHandler(
        str(log_file),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    # Configure the 'app' logger
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.DEBUG)
    # Avoid duplicate handlers if called multiple times
    if not app_logger.handlers:
        app_logger.addHandler(file_handler)
        app_logger.addHandler(console_handler)
        app_logger.propagate = False  # Don't pass to root logger

    # Configure root logger for third-party libraries
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    if not root_logger.handlers:
        root_logger.addHandler(file_handler)


def get_logger(name: str = "app") -> logging.Logger:
    """Get a logger under the 'app' namespace."""
    return logging.getLogger(name if name == "app" else f"app.{name}")
