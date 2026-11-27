"""The revision `composition_and_run_report` over a database of the initial revision (plan m6 M6.1).

A database written at the initial revision holds batch and run payloads of the old shape; opening
it upgrades it, adding the new keys as null, and the app reads it: the batch has no composition,
the run no stored report and no refresh time.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import ExitStack, closing
from importlib.resources import as_file, files
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from alembic import command
from alembic.config import Config

from support.fakes import FIXED_NOW, seeded_ports
from transcribe_ui.api.backend import Backend
from transcribe_ui.state.constants import MIGRATION_CONNECTION_KEY, MIGRATIONS_FOLDER, MIGRATIONS_PACKAGE
from transcribe_ui.state.database import sqlite_engine

# The initial revision, which the old database carries.
INITIAL_REVISION = "5d0c3b1e7a42"
# The newest revision.
NEW_REVISION = "b7e2c4a91f03"
BATCH_ID = "batch_20261127_120000"
RUN_ID = "01JDQ0000000000000000000R1"
# A fingerprint as stored (a SHA-256 in hex).
FINGERPRINT = "5d1a4c0e9b7f3a2d6e8c1b4f7a0d3e6c9b2f5a8d1e4c7b0a3f6d9c2e5b8a1f4d"
# A batch payload and a run payload as the initial revision's app wrote them.
OLD_BATCH_PAYLOAD = MappingProxyType(
    {"scan_root": "/media", "path_flavor": "posix", "languages": ["en"], "retry_of": None, "cleanup_pending": None},
)
OLD_RUN_PAYLOAD = MappingProxyType(
    {
        "max_runtime_s": 3600,
        "test_mode": False,
        "wait_s": 3600,
        "queue_expires_at": "2026-11-27T13:00:00Z",
        "name_deadline": "2026-11-27T14:10:00Z",
        "covered_batch_ids": [BATCH_ID],
        "fingerprint": FINGERPRINT,
        "pending_duration_s": 60.0,
        "estimate": {"low_usd": 0.1, "high_usd": 0.2, "price_per_hour_usd": 0.27, "price_source": "fallback"},
        "pod_created_at": "2026-11-27T11:00:00Z",
        "price_per_hour_usd": 0.27,
        "ended_at": "2026-11-27T11:30:00Z",
        "end_reason": "worker_finished",
    },
)
# Elapsed seconds of the stored run: from the pod's creation to its end.
RUN_SECONDS = 1800.0


def _initial_schema(database_file: Path) -> None:
    """Create the database at the initial revision, as the first app version left it."""
    database_file.parent.mkdir(parents=True, exist_ok=True)
    engine = sqlite_engine(database_file, foreign_keys=False)
    with ExitStack() as stack:
        stack.callback(engine.dispose)
        location = stack.enter_context(as_file(files(MIGRATIONS_PACKAGE) / MIGRATIONS_FOLDER))
        connection = stack.enter_context(engine.begin())
        config = Config()
        config.set_main_option("script_location", str(location).replace("%", "%%"))
        config.attributes[MIGRATION_CONNECTION_KEY] = connection
        command.upgrade(config, INITIAL_REVISION)


def _old_rows(database_file: Path) -> None:
    """Insert one batch and one run with payloads of the old shape."""
    moment = FIXED_NOW.replace(tzinfo=None).isoformat(sep=" ")
    with closing(sqlite3.connect(database_file)) as connection, connection:
        connection.execute(
            "INSERT INTO batches VALUES (?, 'transcribed', 0, 0, NULL, 0, ?, ?, ?)",
            (BATCH_ID, moment, moment, json.dumps(dict(OLD_BATCH_PAYLOAD))),
        )
        connection.execute(
            "INSERT INTO runs VALUES (?, 'finished', ?, ?, ?, ?)",
            (RUN_ID, f"transcribe-ui-{RUN_ID}", moment, moment, json.dumps(dict(OLD_RUN_PAYLOAD))),
        )


def _stored(database_file: Path, query: str) -> str:
    with closing(sqlite3.connect(database_file)) as connection:
        found = connection.execute(query).fetchone()[0]
    assert isinstance(found, str)
    return found


def _ok(backend: Backend, request: dict[str, object]) -> dict[str, Any]:
    response = backend.execute_json(json.dumps(request))
    assert response.status == "ok", response
    document: dict[str, Any] = json.loads(response.model_dump_json())
    return document


@pytest.fixture
def upgraded(tmp_path: Path) -> Iterator[tuple[Backend, Path]]:
    """Return a backend over an old database (upgraded on its first request) and the database file."""
    ports = seeded_ports(tmp_path)
    database_file = ports.state.paths.database_file
    _initial_schema(database_file)
    _old_rows(database_file)
    with closing(ports.state):
        yield Backend(ports), database_file


def test_old_payloads_get_the_new_keys(upgraded: tuple[Backend, Path]) -> None:
    """Opening the old database upgrades it to the newest revision, adding the new keys as null."""
    backend, database_file = upgraded
    _ok(backend, {"action": "list_batches"})
    assert _stored(database_file, "SELECT version_num FROM alembic_version") == NEW_REVISION
    batch = json.loads(_stored(database_file, "SELECT payload FROM batches"))
    run = json.loads(_stored(database_file, "SELECT payload FROM runs"))
    assert batch == {**OLD_BATCH_PAYLOAD, "composition": None}
    assert run == {**OLD_RUN_PAYLOAD, "worker": None, "stop_error": None, "refreshed_at": None}


def test_upgraded_rows_read_as_before(upgraded: tuple[Backend, Path]) -> None:
    """The upgraded batch has no composition; the run lists with its elapsed time up to its stored end."""
    backend, _database_file = upgraded
    detail = _ok(backend, {"action": "get_batch", "batch_id": BATCH_ID})
    runs = _ok(backend, {"action": "list_runs"})["runs"]
    assert (detail["batch"]["composition"], len(runs)) == (None, 1)
    run = runs[0]
    assert (run["worker"], run["refreshed_at"], run["elapsed_s"]) == (None, None, RUN_SECONDS)
    assert run["elapsed_until"] == OLD_RUN_PAYLOAD["ended_at"]
