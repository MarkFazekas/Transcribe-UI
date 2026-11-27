"""Locks of the local state (plan m5 §1 "Concurrency inside the process").

- Writer lock (cross-process): an OS file lock held while a process has the database open, so
  only one process (the GUI or one CLI run) writes it.
- Batch locks (in-process, one per batch): never waited for; a held lock gives `batch_busy`.
- Runs lock (in-process, one): waited for at most `RUNS_LOCK_WAIT_S`, then `runs_busy`.

Lock order is runs → batch → database transaction (innermost). Since batch locks are never
waited for and the runs lock wait is bounded, no two requests can wait on each other for ever.
The database transaction (`LocalState.session`) is always the innermost: never wait for a lock
or do network I/O inside it. `LockRegistry` enforces the lock half of that rule: asking for a
batch or the runs lock while this thread has a session open raises `LockOrderError`.
"""

import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from transcribe_ui.api.errors import CancelledError
from transcribe_ui.state.constants import (
    RUNS_LOCK_POLL_S,
    RUNS_LOCK_WAIT_S,
    WRITER_LOCK_FILE_MODE,
    WRITER_LOCK_HELD_ERRNOS,
)
from transcribe_ui.state.errors import (
    AppBusyError,
    BatchBusyError,
    LockOrderError,
    RunsBusyError,
    StateUnavailableError,
)

logger = logging.getLogger(__name__)

# Returns whether cancellation was requested (the `is_cancelled` of a cancel token).
type CancelCheck = Callable[[], bool]
# Returns whether the calling thread has a database session open.
type TransactionCheck = Callable[[], bool]


if sys.platform == "win32":
    import msvcrt

    def _lock_file(descriptor: int) -> None:
        # Locks the first byte (beyond the end of the empty file, which Windows allows).
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)

    def _unlock_file(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


class WriterLock:
    """The cross-process lock on the database; the OS releases it when the process ends."""

    def __init__(self, path: Path) -> None:
        """Lock this file (created when missing)."""
        self._path = path
        self._descriptor: int | None = None

    @property
    def held(self) -> bool:
        """Return whether this object holds the lock."""
        return self._descriptor is not None

    def acquire(self) -> None:
        """Take the lock without waiting.

        Raises:
            AppBusyError: If another process (or another `WriterLock` on the file) holds it.
            StateUnavailableError: If the lock file cannot be created or locked for another reason.
        """
        if self.held:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self._path, os.O_RDWR | os.O_CREAT, WRITER_LOCK_FILE_MODE)
        except OSError as error:
            logger.exception("Cannot create the writer lock file %s", self._path)
            raise StateUnavailableError from error
        try:
            _lock_file(descriptor)
        except OSError as error:
            os.close(descriptor)
            if error.errno in WRITER_LOCK_HELD_ERRNOS:
                raise AppBusyError from error
            logger.exception("Cannot lock the writer lock file %s", self._path)
            raise StateUnavailableError from error
        self._descriptor = descriptor

    def release(self) -> None:
        """Give the lock up (a no-op when it is not held)."""
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            _unlock_file(descriptor)
        except OSError:
            # Closing the descriptor below releases the lock anyway.
            logger.warning("Unlocking %s failed; closing the file releases it", self._path, exc_info=True)
        finally:
            os.close(descriptor)


class LockRegistry:
    """The in-process batch locks and the runs lock of one backend; safe across threads."""

    def __init__(self, runs_wait_s: float = RUNS_LOCK_WAIT_S, in_transaction: TransactionCheck | None = None) -> None:
        """Wait at most `runs_wait_s` seconds for the runs lock (tests shorten it).

        `in_transaction` tells whether the calling thread has a database session open; locks
        are refused then (lock order).
        """
        self._runs_wait_s = runs_wait_s
        self._in_transaction = in_transaction
        self._guard = threading.Lock()
        self._busy_batches: set[str] = set()
        self._runs = threading.Lock()
        # Thread id of the runs lock's holder (None while free), for `holds_runs`.
        self._runs_owner: int | None = None

    @contextmanager
    def batch(self, batch_id: str) -> Iterator[None]:
        """Hold the batch's lock while the block runs; never waits.

        Raises:
            BatchBusyError: If another request holds it.
        """
        self._check_order()
        with self._guard:
            if batch_id in self._busy_batches:
                raise BatchBusyError(batch_id)
            self._busy_batches.add(batch_id)
        try:
            yield
        finally:
            with self._guard:
                self._busy_batches.discard(batch_id)

    @contextmanager
    def try_batch(self, batch_id: str) -> Iterator[bool]:
        """Hold the batch's lock while the block runs if it is free; yield whether it was taken.

        For work that skips a busy batch instead of failing (`publish_held_manifests`).
        """
        self._check_order()
        with self._guard:
            taken = batch_id not in self._busy_batches
            self._busy_batches.add(batch_id)
        try:
            yield taken
        finally:
            if taken:
                with self._guard:
                    self._busy_batches.discard(batch_id)

    @contextmanager
    def runs(self, cancelled: CancelCheck | None = None) -> Iterator[None]:
        """Hold the runs lock while the block runs, waiting for it at most the bounded wait.

        `cancelled` (the request's `CancelToken.is_cancelled`), if given, is checked while
        waiting. Take the runs lock before any batch lock, or, while holding batch locks, only
        through this bounded wait.

        Raises:
            RunsBusyError: If the lock stayed held for the whole wait.
            CancelledError: If cancellation was requested while waiting.
        """
        self._check_order()
        self._acquire_runs(cancelled)
        self._runs_owner = threading.get_ident()
        try:
            yield
        finally:
            self._runs_owner = None
            self._runs.release()

    def holds_runs(self) -> bool:
        """Return whether the calling thread holds the runs lock."""
        return self._runs_owner == threading.get_ident()

    def _check_order(self) -> None:
        if self._in_transaction is not None and self._in_transaction():
            msg = "A lock was requested inside a database session; take locks before opening one."
            raise LockOrderError(msg)

    def _acquire_runs(self, cancelled: CancelCheck | None) -> None:
        deadline = time.monotonic() + self._runs_wait_s
        while not self._runs.acquire(timeout=RUNS_LOCK_POLL_S):
            if cancelled is not None and cancelled():
                raise CancelledError
            if time.monotonic() >= deadline:
                raise RunsBusyError
