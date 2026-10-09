"""
Tests for the logging boundary (logging_setup).

Verifies centralized configuration: one setup call configures the 'app'
logger with rotating file + console handlers, repeated setup calls do not
duplicate handlers or output, and get_logger() returns children of 'app'
so records flow through the configured handlers.

Global logging state ('app' logger and root logger) is snapshotted and
restored around each test, so these tests never leak handlers into the
rest of the suite.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from src.app.core.logging_setup import get_logger, setup_logging


@pytest.fixture()
def isolated_logging():
    """Snapshot and restore global 'app' and root logger state.

    Only handlers added by the test are closed; pre-existing handlers
    are left untouched.
    """
    app_logger = logging.getLogger("app")
    root_logger = logging.getLogger()
    saved = {
        app_logger: (
            list(app_logger.handlers),
            app_logger.level,
            app_logger.propagate,
        ),
        root_logger: (
            list(root_logger.handlers),
            root_logger.level,
            root_logger.propagate,
        ),
    }
    yield
    for logger, (handlers, level, propagate) in saved.items():
        for handler in list(logger.handlers):
            if handler not in handlers:
                logger.removeHandler(handler)
                handler.close()
        for handler in handlers:
            if handler not in logger.handlers:
                logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = propagate


def test_setup_logging_configures_app_logger(
    tmp_path: Path, isolated_logging: None
) -> None:
    """One setup call attaches a rotating file handler and a console
    handler to the 'app' logger, and stops propagation to root."""
    log_file = tmp_path / "app.log"
    setup_logging(log_file)

    app_logger = logging.getLogger("app")
    assert len(app_logger.handlers) == 2
    assert any(isinstance(h, RotatingFileHandler) for h in app_logger.handlers)
    assert any(
        type(h) is logging.StreamHandler for h in app_logger.handlers
    )
    assert app_logger.propagate is False
    assert log_file.exists()


def test_setup_logging_does_not_duplicate_handlers(
    tmp_path: Path, isolated_logging: None
) -> None:
    """Repeated setup calls must not add handlers or duplicate output."""
    setup_logging(tmp_path / "one.log")
    setup_logging(tmp_path / "two.log")

    assert len(logging.getLogger("app").handlers) == 2


def test_get_logger_returns_child_of_app(isolated_logging: None) -> None:
    """Module loggers live under the 'app' namespace, so they inherit
    the centralized configuration without recreating it."""
    logger = get_logger("myservice")
    assert logger.name == "app.myservice"
    assert logger.parent is logging.getLogger("app")
    assert get_logger().name == "app"


def test_log_records_reach_the_configured_file(
    tmp_path: Path, isolated_logging: None
) -> None:
    """A record logged through get_logger() lands in the setup file."""
    log_file = tmp_path / "app.log"
    setup_logging(log_file)

    get_logger("probe").info("hello-from-test")
    for handler in logging.getLogger("app").handlers:
        handler.flush()

    content = log_file.read_text(encoding="utf-8")
    assert "hello-from-test" in content
    assert "app.probe" in content
