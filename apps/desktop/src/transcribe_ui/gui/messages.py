"""Messages of Qt and exceptions escaping a Qt slot go to the GUI log, never to stderr."""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import TracebackType

from PySide6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler

from transcribe_ui.gui.constants import QT_LOG_LEVELS, QT_LOGGER_NAME

logger = logging.getLogger(__name__)
qt_logger = logging.getLogger(QT_LOGGER_NAME)


def log_qt_message(kind: QtMsgType, _context: QMessageLogContext, message: str) -> None:
    """Send one Qt message to the GUI log."""
    qt_logger.log(QT_LOG_LEVELS.get(kind.name, logging.WARNING), "%s", message)


@contextmanager
def qt_messages_to_log() -> Iterator[None]:
    """Route the messages of Qt to the log while the block runs (`qInstallMessageHandler`; restored after)."""
    previous = qInstallMessageHandler(log_qt_message)
    try:
        yield
    finally:
        qInstallMessageHandler(previous)


def _log_unhandled(kind: type[BaseException], error: BaseException, traceback: TracebackType | None) -> None:
    logger.error("Unhandled exception in the GUI", exc_info=(kind, error, traceback))


@contextmanager
def exceptions_to_log() -> Iterator[None]:
    """Send exceptions escaping a Qt slot to the log instead of stderr while the block runs."""
    previous = sys.excepthook
    sys.excepthook = _log_unhandled
    try:
        yield
    finally:
        sys.excepthook = previous
