"""Flow tests of the local state (plan m5 M5.2): database, migrations, writer lock, compare-and-set."""

import os
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import closing
from functools import partial
from importlib.resources import files
from pathlib import Path
from typing import Final

import pytest
from alembic import command as alembic_command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select

from support.fakes import FIXED_NOW, TEST_RUNS_LOCK_WAIT_S, app_paths_under, seeded_ports
from support.routes import database_routes
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorBody, ErrorResponse, GetAppInfoRequest, Response
from transcribe_ui.api.schema import response_adapter
from transcribe_ui.cli.constants import EXIT_ERROR, EXIT_OK
from transcribe_ui.state.constants import MIGRATIONS_FOLDER, MIGRATIONS_PACKAGE
from transcribe_ui.state.database import compare_and_set_phase
from transcribe_ui.state.errors import (
    BatchNotFoundError,
    LockOrderError,
    PhaseConflictError,
    StateBusyError,
    StateUnavailableError,
)
from transcribe_ui.state.models import Base, BatchRow, RunRow
from transcribe_ui.state.store import LocalState

GET_APP_INFO = GetAppInfoRequest(action="get_app_info")
GET_APP_INFO_JSON = '{"action": "get_app_info"}'
BATCH_ID = "batch_20261127_120000"
COMPOSING: Final = "composing"
REVIEWING: Final = "reviewing"
HEAD = "8e5b2d7c1f40"
UNKNOWN_REVISION = "ffffffffffff"
EXPECTED_TABLES = frozenset(
    (
        "alembic_version",
        "audio_objects",
        "batches",
        "extracted_subtitles",
        "items",
        "jobs",
        "placements",
        "probe_cache",
        "runs",
    ),
)
SUBPROCESS_TIMEOUT_S = 60
THREAD_TIMEOUT_S = 10
SHORT_HOLD_S = 0.2
CRASH_DETAIL = "detail kept for the log file"
TESTS_DIR = Path(__file__).resolve().parent


@pytest.fixture
def state(tmp_path: Path) -> Iterator[LocalState]:
    """A local state under the test root, closed afterwards."""
    local_state = LocalState(app_paths_under(tmp_path), runs_lock_wait_s=TEST_RUNS_LOCK_WAIT_S)
    yield local_state
    local_state.close()


def _error(response: Response) -> ErrorBody:
    assert isinstance(response, ErrorResponse)
    return response.error


def _error_code(response: Response) -> str:
    return _error(response).code


def _sqlite_value(database_file: Path, query: str) -> object:
    with closing(sqlite3.connect(database_file)) as connection, connection:
        return connection.execute(query).fetchone()[0]


def _add_batch(state: LocalState) -> None:
    with state.session() as session:
        session.add(
            BatchRow(batch_id=BATCH_ID, phase=COMPOSING, created_at=FIXED_NOW, updated_at=FIXED_NOW, payload={}),
        )


def test_fresh_start_creates_and_migrates_the_database(tmp_path: Path) -> None:
    """The first database request creates the database in WAL mode at the newest revision."""
    ports = seeded_ports(tmp_path)
    database_file = ports.state.paths.database_file
    assert Backend(ports, database_routes()).execute(GET_APP_INFO).status == "ok"
    ports.state.close()
    with closing(sqlite3.connect(database_file)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == EXPECTED_TABLES
    assert _sqlite_value(database_file, "PRAGMA journal_mode") == "wal"
    assert _sqlite_value(database_file, "SELECT version_num FROM alembic_version") == HEAD


def test_models_match_the_migrations(state: LocalState) -> None:
    """The migrated schema equals the SQLAlchemy models (no autogenerate difference)."""
    engine = state.open_database().engine
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert not differences


def test_migrations_are_package_data() -> None:
    """The Alembic environment is found through `importlib.resources` (ready for PyInstaller)."""
    migrations = files(MIGRATIONS_PACKAGE) / MIGRATIONS_FOLDER
    assert migrations.joinpath("env.py").is_file()
    assert migrations.joinpath("script.py.mako").is_file()
    assert migrations.joinpath("versions", "initial_schema.py").is_file()
    assert migrations.joinpath("versions", "composition_and_run_report.py").is_file()
    assert migrations.joinpath("versions", "result_error_code.py").is_file()


def _run_in_second_process(root: Path, request_json: str) -> tuple[int, Response]:
    request_file = root.parent / f"{root.name}-request.json"
    request_file.write_text(request_json, encoding="utf-8")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [str(TESTS_DIR), environment.get("PYTHONPATH")]))
    completed = subprocess.run(  # noqa: S603 -- runs this repo's test helper with the test interpreter
        [sys.executable, str(TESTS_DIR / "support" / "second_process.py"), str(root), str(request_file)],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
        env=environment,
    )
    assert not completed.stderr, completed.stderr
    return completed.returncode, response_adapter().validate_json(completed.stdout)


def test_second_process_serves_only_db_free_requests(tmp_path: Path) -> None:
    """While one process holds the database, another one is served only DB-free requests."""
    ports = seeded_ports(tmp_path)
    with closing(ports.state):
        assert Backend(ports, database_routes()).execute(GET_APP_INFO).status == "ok"
        busy = _run_in_second_process(tmp_path, GET_APP_INFO_JSON)
        served = _run_in_second_process(tmp_path, '{"action": "get_settings"}')
    assert (busy[0], _error_code(busy[1])) == (EXIT_ERROR, "app_busy")
    assert (served[0], served[1].status) == (EXIT_OK, "ok")
    code, response = _run_in_second_process(tmp_path, GET_APP_INFO_JSON)
    assert (code, response.status) == (EXIT_OK, "ok")


def test_second_state_in_one_process_gets_app_busy(tmp_path: Path, state: LocalState) -> None:
    """The writer lock also holds against a second backend in the same process until closed."""
    state.open_database()
    other_ports = seeded_ports(tmp_path)
    other = Backend(other_ports, database_routes())
    with closing(other_ports.state):
        refused = other.execute(GET_APP_INFO)
        state.close()
        served = other.execute(GET_APP_INFO)
    assert _error_code(refused) == "app_busy"
    assert served.status == "ok"


def test_newer_database_is_refused(tmp_path: Path, state: LocalState) -> None:
    """A database stamped with a revision this app does not know is refused and left unchanged."""
    state.open_database()
    state.close()
    database_file = state.paths.database_file
    with closing(sqlite3.connect(database_file)) as connection, connection:
        connection.execute("UPDATE alembic_version SET version_num = ?", (UNKNOWN_REVISION,))
    backend = Backend(seeded_ports(tmp_path), database_routes())
    for _ in range(2):
        # The second attempt proves the writer lock was given up (else it would be `app_busy`).
        refused = _error(backend.execute(GET_APP_INFO))
        assert (refused.code, refused.details) == ("database_too_new", {"revisions": [UNKNOWN_REVISION]})
    assert _sqlite_value(database_file, "SELECT version_num FROM alembic_version") == UNKNOWN_REVISION


def _race(state: LocalState, barrier: threading.Barrier, run_id: str, outcomes: dict[str, str]) -> None:
    barrier.wait(THREAD_TIMEOUT_S)
    try:
        with state.session() as session:
            # A write before the phase change: it must not survive a lost race.
            session.add(
                RunRow(
                    run_id=run_id,
                    status="launching",
                    pod_name=run_id,
                    created_at=FIXED_NOW,
                    updated_at=FIXED_NOW,
                    payload={},
                ),
            )
            session.flush()
            compare_and_set_phase(session, BATCH_ID, COMPOSING, REVIEWING, now=FIXED_NOW, also_set={"hidden": True})
    except PhaseConflictError as error:
        outcomes[run_id] = error.code
    else:
        outcomes[run_id] = "won"


def test_compare_and_set_loser_changes_nothing(state: LocalState) -> None:
    """Of two racing phase changes exactly one wins; the loser's whole transaction is rolled back."""
    _add_batch(state)
    run_ids = ("01JDQ0000000000000000000A1", "01JDQ0000000000000000000B2")
    barrier = threading.Barrier(len(run_ids))
    outcomes: dict[str, str] = {}
    threads = [threading.Thread(target=_race, args=(state, barrier, run_id, outcomes)) for run_id in run_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(THREAD_TIMEOUT_S)
    assert sorted(outcomes.values()) == ["phase_conflict", "won"]
    winner = next(run_id for run_id, outcome in outcomes.items() if outcome == "won")
    with state.session() as session:
        batch = session.get_one(BatchRow, BATCH_ID)
        assert (batch.phase, batch.hidden) == (REVIEWING, True)
        assert list(session.scalars(select(RunRow.run_id))) == [winner]


def test_compare_and_set_condition_and_missing_batch(state: LocalState) -> None:
    """An extra condition that fails is a conflict; an unknown batch is `batch_not_found`."""
    _add_batch(state)
    hidden = BatchRow.hidden.is_(True)
    with pytest.raises(PhaseConflictError) as conflict, state.session() as session:
        compare_and_set_phase(session, BATCH_ID, COMPOSING, REVIEWING, now=FIXED_NOW, conditions=[hidden])
    assert conflict.value.details == {"batch_id": BATCH_ID, "expected": COMPOSING, "actual": COMPOSING}
    with pytest.raises(BatchNotFoundError), state.session() as session:
        compare_and_set_phase(session, "batch_20261127_120001", COMPOSING, REVIEWING, now=FIXED_NOW)


def test_conditional_change_refreshes_loaded_row(state: LocalState) -> None:
    """A change whose condition holds succeeds, and a row the session loaded shows the new phase."""
    _add_batch(state)
    not_hidden = BatchRow.hidden.is_(False)
    with state.session() as session:
        loaded = session.get_one(BatchRow, BATCH_ID)
        compare_and_set_phase(session, BATCH_ID, COMPOSING, REVIEWING, now=FIXED_NOW, conditions=[not_hidden])
        assert loaded.phase == REVIEWING


def test_locks_are_refused_inside_a_session(state: LocalState) -> None:
    """Lock order runs → batch → transaction: asking for a lock inside a session is a bug."""
    with state.session():
        with pytest.raises(LockOrderError), state.locks.batch(BATCH_ID):
            pytest.fail("a batch lock was taken inside a session")
        with pytest.raises(LockOrderError), state.locks.runs():
            pytest.fail("the runs lock was taken inside a session")
    with state.locks.runs(), state.locks.batch(BATCH_ID), state.session():
        assert state.in_session()
    assert not state.in_session()


def test_sessions_do_not_nest(state: LocalState) -> None:
    """A second session on a thread that has one open is a lock-order bug."""
    with state.session(), pytest.raises(LockOrderError), state.session():
        pytest.fail("a nested session was opened")
    assert not state.in_session()


def test_locked_database_gives_state_busy(tmp_path: Path) -> None:
    """A transaction that waits past the busy timeout gives `state_busy`, not `internal_error`."""
    state = LocalState(app_paths_under(tmp_path), busy_timeout_s=SHORT_HOLD_S)
    with closing(state):
        state.open_database()
        blocker = sqlite3.connect(state.paths.database_file, isolation_level=None)
        with closing(blocker):
            blocker.execute("BEGIN IMMEDIATE")
            with pytest.raises(StateBusyError) as busy, state.session() as session:
                session.get(BatchRow, BATCH_ID)
            blocker.execute("ROLLBACK")
    assert busy.value.code == "state_busy"


def _upgrade_leaving_an_orphan(upgrade: Callable[[Config, str], None], config: Config, revision: str) -> None:
    upgrade(config, revision)
    connection = config.attributes["connection"]
    connection.exec_driver_sql(
        "INSERT INTO items (item_id, batch_id, phase, payload) VALUES ('01JDQ0000000000000000000C3', 'missing', 'x', '{}')",
    )


def test_migration_breaking_foreign_keys_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows a migration leaves pointing at missing rows abort it; the database stays empty."""
    monkeypatch.setattr(alembic_command, "upgrade", partial(_upgrade_leaving_an_orphan, alembic_command.upgrade))
    state = LocalState(app_paths_under(tmp_path))
    with closing(state), pytest.raises(StateUnavailableError):
        state.open_database()
    with closing(sqlite3.connect(state.paths.database_file)) as connection:
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    assert not tables
