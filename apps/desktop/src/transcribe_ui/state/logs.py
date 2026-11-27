"""Logging of the desktop app: a human-readable rotating file in the user log dir (§6).

Nothing goes to stderr (the CLI keeps it for NDJSON progress): without a log dir, or when the
log file cannot be written, records are dropped. Secrets are never logged.
"""

import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from io import TextIOWrapper
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import override

from transcribe_ui.constants import PACKAGE_NAME
from transcribe_ui.state.constants import APP_LOG_LEVEL, LOG_BACKUP_COUNT, LOG_FILE_NAME, LOG_FORMAT, LOG_MAX_BYTES
from transcribe_ui.state.paths import AppPaths


def _raise_handler_errors(*, enabled: bool) -> None:
    # False: a failing handler drops the record silently instead of printing to stderr.
    logging.raiseExceptions = enabled


class _LazyRotatingFileHandler(RotatingFileHandler):
    """A rotating file handler that creates the log dir only when it writes the first record."""

    @override
    def _open(self) -> TextIOWrapper:
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        return super()._open()


def _file_handler(log_dir: Path | None) -> logging.Handler:
    if log_dir is None:
        return logging.NullHandler()
    file_handler = _LazyRotatingFileHandler(
        log_dir / LOG_FILE_NAME,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return file_handler


@contextmanager
def log_to_file(log_dir: Path | None) -> Iterator[None]:
    """Send log records and warnings to the rotating log file in `log_dir` while the block runs.

    The app's loggers log from INFO up, other libraries from WARNING up. `log_dir` None drops
    every record. The log dir is created with the first record. A handler failure (no access,
    disk full, a rotation that another process blocks on Windows) drops the record instead of
    printing to stderr. Everything is undone when the block
    ends (tests run the CLI in-process).
    """
    with ExitStack() as cleanup:
        _attach_handler(cleanup, _file_handler(log_dir))
        app_logger = logging.getLogger(PACKAGE_NAME)
        cleanup.callback(app_logger.setLevel, app_logger.level)
        app_logger.setLevel(APP_LOG_LEVEL)
        cleanup.callback(_raise_handler_errors, enabled=logging.raiseExceptions)
        _raise_handler_errors(enabled=False)
        cleanup.callback(logging.captureWarnings, capture=False)
        logging.captureWarnings(capture=True)
        yield


def _attach_handler(cleanup: ExitStack, log_handler: logging.Handler) -> None:
    root = logging.getLogger()
    cleanup.callback(log_handler.close)
    root.addHandler(log_handler)
    cleanup.callback(root.removeHandler, log_handler)


def user_log_dir() -> Path:
    """Return the current user's log dir (the CLI's and the GUI's log file live there)."""
    return AppPaths.for_user().log_dir
