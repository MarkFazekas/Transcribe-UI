"""Flow tests of the local state (plan m5 M5.2): database, migrations, locks, compare-and-set, logs."""

import io
import os
import sqlite3
import subprocess
import sys
import threading
import time
import warnings
from collections.abc import Iterator
from contextlib import closing
from functools import partial
from importlib.resources import files
from pathlib import Path
from typing import Final

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select

from support.fakes import FIXED_NOW, TEST_RUNS_LOCK_WAIT_S, app_paths_under, seeded_ports
from support.routes import database_routes
from transcribe_ui.api.backend import Backend, Route
from transcribe_ui.api.classes import ErrorBody, ErrorResponse, GetAppInfoRequest, GetAppInfoResponse, Response
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.info import get_app_info
from transcribe_ui.api.ports import CancelFlag, RequestContext
from transcribe_ui.api.schema import response_adapter
from transcribe_ui.cli.constants import EXIT_ERROR, EXIT_OK
from transcribe_ui.cli.main import main
from transcribe_ui.state.constants import LOG_FILE_NAME, MIGRATIONS_FOLDER, MIGRATIONS_PACKAGE
from transcribe_ui.state.database import compare_and_set_phase
from transcribe_ui.state.errors import BatchNotFoundError, PhaseConflictError, RunsBusyError
from transcribe_ui.state.locks import LockRegistry
from transcribe_ui.state.models import Base, BatchRow, RunRow
from transcribe_ui.state.store import LocalState

GET_APP_INFO = GetAppInfoRequest(action="get_app_info")
GET_APP_INFO_JSON = '{"action": "get_app_info"}'
BATCH_ID = "batch_20261127_120000"
COMPOSING: Final = "composing"
REVIEWING: Final = "reviewing"
HEAD = "5d0c3b1e7a42"
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


def _hold_batch(
    entered: threading.Event,
    release: threading.Event,
    request: GetAppInfoRequest,
    context: RequestContext,
) -> Response:
    with context.ports.state.locks.batch(BATCH_ID):
        entered.set()
        release.wait(THREAD_TIMEOUT_S)
        return get_app_info(request, context)


def test_batch_lock_refuses_a_concurrent_request(tmp_path: Path) -> None:
    """A request that finds its batch locked by another thread gets `batch_busy` at once."""
    entered = threading.Event()
    release = threading.Event()
    run = partial(_hold_batch, entered, release)
    backend = Backend(seeded_ports(tmp_path), {"get_app_info": Route(run=run, needs_database=False)})
    outcomes: list[Response] = []
    holder = threading.Thread(target=lambda: outcomes.append(backend.execute(GET_APP_INFO)))
    holder.start()
    assert entered.wait(THREAD_TIMEOUT_S)
    started = time.monotonic()
    refused = backend.execute(GET_APP_INFO)
    assert time.monotonic() - started < TEST_RUNS_LOCK_WAIT_S
    release.set()
    holder.join(THREAD_TIMEOUT_S)
    assert (_error_code(refused), _error(refused).details) == ("batch_busy", {"batch_id": BATCH_ID})
    assert isinstance(outcomes[0], GetAppInfoResponse)
    assert isinstance(backend.execute(GET_APP_INFO), GetAppInfoResponse)


def _hold_runs(locks: LockRegistry, entered: threading.Event, hold_s: float) -> None:
    with locks.runs():
        entered.set()
        time.sleep(hold_s)


def _holder(locks: LockRegistry, hold_s: float) -> threading.Thread:
    entered = threading.Event()
    thread = threading.Thread(target=_hold_runs, args=(locks, entered, hold_s))
    thread.start()
    assert entered.wait(THREAD_TIMEOUT_S)
    return thread


def test_runs_lock_waits_then_gives_runs_busy() -> None:
    """The runs lock is waited for at most the bounded wait, then the request gets `runs_busy`."""
    wait_s = 0.3
    locks = LockRegistry(runs_wait_s=wait_s)
    holder = _holder(locks, hold_s=wait_s * 4)
    started = time.monotonic()
    with pytest.raises(RunsBusyError), locks.runs():
        pytest.fail("the runs lock was taken while held")
    waited = time.monotonic() - started
    holder.join(THREAD_TIMEOUT_S)
    assert wait_s <= waited < wait_s * 3


def test_runs_lock_is_handed_over_within_the_wait() -> None:
    """A holder that finishes within the wait hands the runs lock over."""
    locks = LockRegistry(runs_wait_s=THREAD_TIMEOUT_S)
    holder = _holder(locks, hold_s=SHORT_HOLD_S)
    with locks.runs():
        assert not holder.is_alive()
    holder.join(THREAD_TIMEOUT_S)


def test_runs_lock_wait_stops_on_cancel() -> None:
    """Cancelling while the runs lock is waited for stops the wait with `cancelled`."""
    locks = LockRegistry(runs_wait_s=THREAD_TIMEOUT_S)
    holder = _holder(locks, hold_s=1)
    flag = CancelFlag()
    threading.Timer(SHORT_HOLD_S, flag.cancel).start()
    started = time.monotonic()
    with pytest.raises(CancelledError), locks.runs(flag.is_cancelled):
        pytest.fail("the runs lock was taken while held")
    assert time.monotonic() - started < THREAD_TIMEOUT_S / 2
    holder.join(THREAD_TIMEOUT_S)


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


def _crash_with_warning(_request: GetAppInfoRequest, _context: RequestContext) -> Response:
    warnings.warn("a library warning", UserWarning, stacklevel=1)
    raise RuntimeError(CRASH_DETAIL)


def test_cli_logs_go_to_the_log_file_not_stderr(tmp_path: Path) -> None:
    """Warnings and the traceback of a crash land in the log file; stderr stays empty."""
    request_file = tmp_path / "request.json"
    request_file.write_text(GET_APP_INFO_JSON, encoding="utf-8")
    backend = Backend(seeded_ports(tmp_path), {"get_app_info": Route(run=_crash_with_warning, needs_database=False)})
    log_dir = tmp_path / "log"
    stderr = io.BytesIO()
    code = main(
        ["run", str(request_file)],
        make_backend=lambda: backend,
        stdout=io.BytesIO(),
        stderr=stderr,
        log_dir=log_dir,
    )
    assert code == EXIT_ERROR
    assert not stderr.getvalue()
    log_text = (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "a library warning" in log_text
    assert f"RuntimeError: {CRASH_DETAIL}" in log_text
