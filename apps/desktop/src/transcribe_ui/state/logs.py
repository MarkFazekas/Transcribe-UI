"""Logging of the desktop app: a human-readable rotating file in the user log dir (§6).

Each frontend writes its own file (`transcribe-ui-cli.log`, `transcribe-ui-gui.log`), so the
GUI and a CLI run never rotate one file. Two runs of one frontend can still share a file; the
handler copes with that (`SharedRotatingFileHandler`). Nothing goes to stderr (the CLI keeps
it for NDJSON progress). Secrets are never logged.
"""

import logging
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from io import TextIOWrapper
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import override

from transcribe_ui.constants import PACKAGE_NAME
from transcribe_ui.state.constants import (
    APP_LOG_LEVEL,
    LOG_BACKUP_COUNT,
    LOG_ENCODING,
    LOG_FORMAT,
    LOG_MAX_BYTES,
    LOG_ROLLOVER_RETRY_BYTES,
    LOG_ROTATING_SUFFIX,
)
from transcribe_ui.state.paths import AppPaths


def _raise_handler_errors(*, enabled: bool) -> None:
    # False: a failing handler drops the record silently instead of printing to stderr.
    logging.raiseExceptions = enabled


class SharedRotatingFileHandler(RotatingFileHandler):
    """A rotating file handler that tolerates other processes writing the same file.

    - The log dir is created with the first record.
    - Rotation renames the current file first and shifts the backups only once that worked. If
      the rename fails (Windows refuses it while another process has the file open), the
      handler keeps appending to the current file and tries again after
      `LOG_ROLLOVER_RETRY_BYTES` more bytes, so no record is dropped.
    - Before each record it checks that its open file is still the one at the path; when
      another process rotated it away (POSIX allows renaming an open file), it reopens the path
      instead of writing into the renamed backup.
    """

    def __init__(self, path: Path, max_bytes: int = LOG_MAX_BYTES, backup_count: int = LOG_BACKUP_COUNT) -> None:
        """Write to `path`, rotating at `max_bytes` and keeping `backup_count` older files."""
        super().__init__(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding=LOG_ENCODING,
            delay=True,
        )
        # The size from which a rotation is tried again after a failed one.
        self._retry_from_size = 0

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Reopen the file if another process rotated it away, then write the record."""
        self._reopen_if_moved()
        super().emit(record)

    @override
    def shouldRollover(self, record: logging.LogRecord) -> int:
        """Return whether to rotate now; after a failed rotation, only once enough was appended."""
        if self.stream is not None and self.stream.tell() < self._retry_from_size:
            return 0
        return super().shouldRollover(record)

    @override
    def doRollover(self) -> None:
        """Rename the current file away first; shift the backups only once that worked."""
        if self.stream is not None:
            self.stream.close()
        base = Path(self.baseFilename)
        staged = base.with_name(f"{base.name}{LOG_ROTATING_SUFFIX}")
        try:
            base.replace(staged)
        except FileNotFoundError:
            # Another process rotated it a moment ago; start a new file.
            self._retry_from_size = 0
        except OSError:
            # Another process has it open (Windows): keep appending, try again later.
            self.stream = self._open()
            self._retry_from_size = self.stream.tell() + LOG_ROLLOVER_RETRY_BYTES
            return
        else:
            self._retry_from_size = 0
            self._shift_backups(staged)
        self.stream = self._open()

    @override
    def _open(self) -> TextIOWrapper:
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        return super()._open()

    def _shift_backups(self, staged: Path) -> None:
        # A failed shift loses at most an old backup, never the current records.
        for index in range(self.backupCount - 1, 0, -1):
            with suppress(OSError):
                self._backup(index).replace(self._backup(index + 1))
        with suppress(OSError):
            staged.replace(self._backup(1))

    def _backup(self, index: int) -> Path:
        return Path(self.rotation_filename(f"{self.baseFilename}.{index}"))

    def _reopen_if_moved(self) -> None:
        if self.stream is None:
            return
        try:
            opened = os.fstat(self.stream.fileno())
            current = Path(self.baseFilename).stat()
        except OSError:
            moved = True
        else:
            moved = (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        if moved:
            self.stream.close()
            self.stream = self._open()


def _file_handler(log_file: Path | None) -> logging.Handler:
    if log_file is None:
        return logging.NullHandler()
    file_handler = SharedRotatingFileHandler(log_file)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return file_handler


@contextmanager
def log_to_file(log_file: Path | None) -> Iterator[None]:
    """Send log records and warnings to the rotating `log_file` while the block runs.

    The app's loggers log from INFO up, other libraries from WARNING up. `log_file` None drops
    every record. The log dir is created with the first record. A rotation that another
    process blocks is retried later while records keep being appended; a record that cannot be
    written at all (no access, disk full) is dropped instead of being printed to stderr.
    Everything is undone when the block ends (tests run the CLI in-process).
    """
    with ExitStack() as cleanup:
        _attach_handler(cleanup, _file_handler(log_file))
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


def user_log_file(file_name: str) -> Path:
    """Return a frontend's log file in the current user's log dir (each frontend names its own)."""
    return AppPaths.for_user().log_dir / file_name
