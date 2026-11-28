"""The local state of one backend: paths, settings, locks and the lazily opened database."""

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from transcribe_ui.state.constants import (
    DATABASE_BUSY_TIMEOUT_S,
    RUNS_LOCK_WAIT_S,
    SQLITE_PRIMARY_CODE_MASK,
)
from transcribe_ui.state.database import Database
from transcribe_ui.state.errors import LockOrderError, StateBusyError, StateUnavailableError
from transcribe_ui.state.locks import LockRegistry, WriterLock
from transcribe_ui.state.paths import AppPaths
from transcribe_ui.state.settings import SettingsStore
from transcribe_ui.state.upgrade import migrate

logger = logging.getLogger(__name__)


def _is_busy(error: OperationalError) -> bool:
    code = getattr(error.orig, "sqlite_errorcode", None)
    return isinstance(code, int) and code & SQLITE_PRIMARY_CODE_MASK == sqlite3.SQLITE_BUSY


class LocalState:
    """Everything a backend keeps on this computer; safe to use from several threads.

    The database is opened by the first request that needs it (`open_database`): the writer
    lock is taken first, then the migrations run. Requests that need no database never open it,
    so a second process can serve them while another one holds the lock.
    """

    def __init__(
        self,
        paths: AppPaths,
        *,
        runs_lock_wait_s: float = RUNS_LOCK_WAIT_S,
        busy_timeout_s: float = DATABASE_BUSY_TIMEOUT_S,
    ) -> None:
        """Keep the state under these paths; tests shorten the runs lock wait and busy timeout."""
        self.paths = paths
        self.settings = SettingsStore(paths.settings_file)
        self.locks = LockRegistry(runs_lock_wait_s, self.in_session)
        self._busy_timeout_s = busy_timeout_s
        self._sessions_open = threading.local()
        self._writer = WriterLock(paths.writer_lock_file)
        self._guard = threading.Lock()
        self._database: Database | None = None

    def open_database(self) -> Database:
        """Return the open database, opening it on the first call.

        A failed opening holds nothing (not even the writer lock): the next request tries again.

        Raises:
            AppBusyError: If another process holds the writer lock.
            DatabaseTooNewError: If the database was written by a newer app.
            StateUnavailableError: If the database cannot be opened.
        """
        with self._guard:
            if self._database is None:
                self._database = self._open()
            return self._database

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Yield a session in one transaction: committed when the block ends, rolled back on error.

        Opens the database first if needed (see `open_database`). The transaction is the
        innermost lock: take batch and runs locks before it, and never wait for a lock or do
        network I/O inside it.

        Raises:
            StateBusyError: If another transaction kept the database locked past the busy timeout.
            LockOrderError: If this thread already has a session open (sessions do not nest).
        """
        if self.in_session():
            msg = "A session was opened inside another one on the same thread; sessions do not nest."
            raise LockOrderError(msg)
        database = self.open_database()
        owner = threading.get_ident()
        self._sessions_open.depth = 1
        try:
            with database.sessions.begin() as session:
                yield session
        except OperationalError as error:
            if _is_busy(error):
                raise StateBusyError from error
            raise
        finally:
            self._leave_session(owner)

    def in_session(self) -> bool:
        """Return whether the calling thread has a session open."""
        return self._session_depth() > 0

    def close(self) -> None:
        """Close the database and give the writer lock up; a later request opens it again."""
        with self._guard, ExitStack() as cleanup:
            cleanup.callback(self._writer.release)
            database = self._database
            self._database = None
            if database is not None:
                database.close()

    def _leave_session(self, owner: int) -> None:
        assert threading.get_ident() == owner, "a session must end on the thread that opened it"
        depth = self._session_depth() - 1
        assert depth >= 0, "more sessions ended than were opened on this thread"
        self._sessions_open.depth = depth

    def _session_depth(self) -> int:
        depth: int = getattr(self._sessions_open, "depth", 0)
        return depth

    def _open(self) -> Database:
        self._writer.acquire()
        with ExitStack() as on_failure:
            on_failure.callback(self._writer.release)
            database = self._migrated_database()
            on_failure.pop_all()
        return database

    def _migrated_database(self) -> Database:
        database_file = self.paths.database_file
        try:
            migrate(database_file)
        except DBAPIError as error:
            logger.exception("Cannot open the database %s", database_file)
            raise StateUnavailableError from error
        return Database.connect(database_file, self._busy_timeout_s)
