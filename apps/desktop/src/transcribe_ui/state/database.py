"""The SQLite database: engine (WAL), sessions and the compare-and-set phase helper (§3.3).

Every transaction starts with `BEGIN IMMEDIATE`, so concurrent requests of one process queue
for the write lock (SQLite busy timeout) instead of failing on a read-to-write upgrade.
"""

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Self, cast

from sqlalchemy import URL, ColumnElement, Connection, CursorResult, Engine, create_engine, event, select, update
from sqlalchemy.orm import Session, sessionmaker

from transcribe_ui.state.constants import DATABASE_BUSY_TIMEOUT_S
from transcribe_ui.state.errors import BatchNotFoundError, PhaseConflictError
from transcribe_ui.state.models import BatchPhase, BatchRow


def _configure_connection(dbapi_connection: sqlite3.Connection, _record: object) -> None:
    # The own transaction handling of pysqlite is turned off; `_begin_immediate` starts transactions.
    dbapi_connection.isolation_level = None
    dbapi_connection.execute("PRAGMA journal_mode=WAL")
    dbapi_connection.execute("PRAGMA synchronous=NORMAL")
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


def _disable_foreign_keys(dbapi_connection: sqlite3.Connection, _record: object) -> None:
    # Batch migrations copy and drop tables; enforced foreign keys would cascade those drops.
    dbapi_connection.execute("PRAGMA foreign_keys=OFF")


def _begin_immediate(connection: Connection) -> None:
    connection.exec_driver_sql("BEGIN IMMEDIATE")


def sqlite_engine(database_file: Path, *, foreign_keys: bool = True) -> Engine:
    """Return an engine on the file: WAL mode, `BEGIN IMMEDIATE` transactions, busy timeout.

    Foreign keys are enforced unless `foreign_keys` is false (migrations only).
    """
    url = URL.create("sqlite", database=str(database_file))
    engine = create_engine(url, connect_args={"timeout": DATABASE_BUSY_TIMEOUT_S})
    event.listen(engine, "connect", _configure_connection)
    if not foreign_keys:
        event.listen(engine, "connect", _disable_foreign_keys)
    event.listen(engine, "begin", _begin_immediate)
    return engine


@dataclass(frozen=True)
class Database:
    """An open database: the engine and the session factory (one session per request)."""

    engine: Engine
    sessions: sessionmaker[Session]

    @classmethod
    def connect(cls, database_file: Path) -> Self:
        """Connect to a migrated database (the caller holds the writer lock)."""
        engine = sqlite_engine(database_file)
        return cls(engine, sessionmaker(engine, expire_on_commit=False))

    def close(self) -> None:
        """Close every pooled connection."""
        self.engine.dispose()


def compare_and_set_phase(  # noqa: PLR0913, WPS211 -- the phase change plus optional extra conditions and values
    session: Session,
    batch_id: str,
    expected: BatchPhase,
    new: BatchPhase,
    *,
    now: datetime,
    conditions: Sequence[ColumnElement[bool]] = (),
    also_set: Mapping[str, object] | None = None,
) -> None:
    """Move a batch from `expected` to `new` in one `UPDATE ... WHERE phase = :expected`.

    `conditions` narrow the match further (e.g. `BatchRow.manifest_held`); `also_set` are column
    values written together with the phase. A request that lost a race changes nothing: the
    error rolls back its whole transaction.

    Raises:
        PhaseConflictError: If the batch is not (or no longer) in `expected` or a condition fails.
        BatchNotFoundError: If the batch does not exist.
    """
    changes = {"phase": new, "updated_at": now, **(also_set or {})}
    matches = (BatchRow.batch_id == batch_id, BatchRow.phase == expected, *conditions)
    statement = update(BatchRow).where(*matches).values(changes)
    outcome = cast("CursorResult[tuple[()]]", session.execute(statement))
    if outcome.rowcount == 1:
        return
    current_phase = select(BatchRow.phase).where(matches[0])
    actual = session.scalar(current_phase)
    if actual is None:
        raise BatchNotFoundError(batch_id)
    raise PhaseConflictError(batch_id, expected, actual)
