"""Start-up migrations: the database is upgraded to the newest Alembic revision before use.

The Alembic environment (`migrations/`) is package data, found through `importlib.resources`
(so it also works from the PyInstaller bundle). A database carrying a revision this app does
not know was written by a newer app and is refused unchanged.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine

from transcribe_ui.state.constants import HEAD_REVISION, MIGRATION_CONNECTION_KEY, MIGRATIONS_FOLDER, MIGRATIONS_PACKAGE
from transcribe_ui.state.database import sqlite_engine
from transcribe_ui.state.errors import DatabaseTooNewError, StateUnavailableError

logger = logging.getLogger(__name__)


@contextmanager
def _alembic_config() -> Iterator[Config]:
    with as_file(files(MIGRATIONS_PACKAGE) / MIGRATIONS_FOLDER) as location:
        config = Config()
        # The value goes through configparser interpolation, so "%" must be doubled.
        config.set_main_option("script_location", str(location).replace("%", "%%"))
        yield config


@contextmanager
def _migration_engine(database_file: Path) -> Iterator[Engine]:
    engine = sqlite_engine(database_file, foreign_keys=False)
    try:
        yield engine
    finally:
        engine.dispose()


def _current_revisions(connection: Connection, script: ScriptDirectory) -> tuple[str, ...]:
    """Return the database's revisions; refuse revisions this app does not know.

    Raises:
        DatabaseTooNewError: If the database carries a revision of a newer app.
    """
    current = MigrationContext.configure(connection).get_current_heads()
    known = {revision.revision for revision in script.walk_revisions()}
    unknown = tuple(sorted(set(current) - known))
    if unknown:
        raise DatabaseTooNewError(unknown)
    return current


def migrate(database_file: Path) -> None:
    """Create or upgrade the database to the newest revision, in one transaction.

    Raises:
        DatabaseTooNewError: If the database was written by a newer app; it is left unchanged.
        StateUnavailableError: If the migrated data violates a foreign key; nothing is changed.
    """
    with _alembic_config() as config, _migration_engine(database_file) as engine, engine.begin() as connection:
        script = ScriptDirectory.from_config(config)
        current = _current_revisions(connection, script)
        heads = script.get_heads()
        if set(current) == set(heads):
            return
        logger.info("Migrating the database from %s to %s", current or "empty", heads)
        config.attributes[MIGRATION_CONNECTION_KEY] = connection
        command.upgrade(config, HEAD_REVISION)
        _check_foreign_keys(connection)


def _check_foreign_keys(connection: Connection) -> None:
    """Refuse a migration that left rows pointing at missing rows (keys are off while migrating).

    Raises:
        StateUnavailableError: If any row violates a foreign key; the migration is rolled back.
    """
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if violations:
        logger.error("The migration left %d foreign key violation(s): %s", len(violations), violations[:10])
        raise StateUnavailableError
