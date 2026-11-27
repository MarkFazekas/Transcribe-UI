"""The local state of one backend: paths, settings, locks and the lazily opened database."""

import logging
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from transcribe_ui.state.constants import RUNS_LOCK_WAIT_S
from transcribe_ui.state.database import Database
from transcribe_ui.state.errors import StateUnavailableError
from transcribe_ui.state.locks import LockRegistry, WriterLock
from transcribe_ui.state.paths import AppPaths
from transcribe_ui.state.settings import SettingsStore
from transcribe_ui.state.upgrade import migrate

logger = logging.getLogger(__name__)


class LocalState:
    """Everything a backend keeps on this computer; safe to use from several threads.

    The database is opened by the first request that needs it (`open_database`): the writer
    lock is taken first, then the migrations run. Requests that need no database never open it,
    so a second process can serve them while another one holds the lock.
    """

    def __init__(self, paths: AppPaths, *, runs_lock_wait_s: float = RUNS_LOCK_WAIT_S) -> None:
        """Keep the state under these paths; tests shorten the runs lock wait."""
        self.paths = paths
        self.settings = SettingsStore(paths.settings_file)
        self.locks = LockRegistry(runs_lock_wait_s)
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

        Opens the database first if needed (see `open_database`).
        """
        database = self.open_database()
        with database.sessions.begin() as session:
            yield session

    def close(self) -> None:
        """Close the database and give the writer lock up; a later request opens it again."""
        with self._guard, ExitStack() as cleanup:
            cleanup.callback(self._writer.release)
            database = self._database
            self._database = None
            if database is not None:
                database.close()

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
        return Database.connect(database_file)
