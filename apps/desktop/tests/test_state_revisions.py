"""The revisions after the initial one, over a database of the initial revision (plan m6 M6.1, M6.14, M6.12, M6.13).

A database written at the initial revision holds batch, run and job payloads of the old shape;
opening it upgrades it, adding the new keys, and the app reads it: the batch has no composition,
the run no stored report and no refresh time (`composition_and_run_report`), a job that failed
with the desktop's own texts gets their codes (`result_error_code`), and the run has no progress
and the jobs no result run or times (`run_progress`).
"""

import json
import sqlite3
from collections.abc import Iterator, Mapping
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
NEW_REVISION = "8e5b2d7c1f40"
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
# Job payloads as the initial revision's app wrote them, by job id: a worker error, the desktop's
# two texts, and a job that did not fail.
OLD_JOB_PAYLOADS = MappingProxyType(
    {
        1: {"mode": "normal", "track_index": 0, "stream_index": None, "result_error": "RuntimeError: out of memory"},
        2: {"mode": "normal", "track_index": 0, "stream_index": None, "result_error": "unreadable result"},
        3: {"mode": "normal", "track_index": 0, "stream_index": None, "result_error": "missing result"},
        4: {"mode": "normal", "track_index": 0, "stream_index": None, "result_error": None},
    },
)
# The code each old job gets.
JOB_CODES = MappingProxyType({1: None, 2: "result_unreadable", 3: "result_missing", 4: None})
ITEM_ID = "01JDQ0000000000000000000T1"
# The item the jobs belong to, as the initial revision's app wrote it (an audio file).
OLD_ITEM_PAYLOAD: Mapping[str, object] = MappingProxyType(
    {
        "position": 0,
        "path": "/media/talk.mp3",
        "relative_path": "talk.mp3",
        "name": "talk.mp3",
        "kind": "audio",
        "size_bytes": 1000,
        "mtime_ns": 0,
        "subtitle_languages": [],
        "probe": None,
        "problem": None,
        "copy_sources": [],
    },
)
LANGUAGES = MappingProxyType({1: "en", 2: "de", 3: "fr", 4: "hu"})
# The run payload keys of the revision `run_progress` (plan m6 M6.12), added as null.
PROGRESS_KEYS = MappingProxyType(
    {
        "weighted_known_duration_s": None,
        "unknown_runtime_s": None,
        "transcribing_since": None,
        "progress_fraction": None,
        "remaining_estimate_s": None,
        "estimated_finish_at": None,
    },
)
# The job payload keys of the revision `run_progress`, added as null.
RESULT_KEYS = MappingProxyType({"result_run_id": None, "result_started_at": None, "result_finished_at": None})
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
        connection.execute(
            "INSERT INTO items (item_id, batch_id, phase, payload) VALUES (?, ?, 'uploaded', ?)",
            (ITEM_ID, BATCH_ID, json.dumps(dict(OLD_ITEM_PAYLOAD))),
        )
        for job_id, payload in OLD_JOB_PAYLOADS.items():
            connection.execute(
                "INSERT INTO jobs (job_id, item_id, batch_id, language, kind, status, payload) "
                "VALUES (?, ?, ?, ?, 'transcribe', 'failed', ?)",
                (job_id, ITEM_ID, BATCH_ID, LANGUAGES[job_id], json.dumps(payload)),
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
    assert batch == {**OLD_BATCH_PAYLOAD, "composition": None, "adopted": None}
    added = {"worker": None, "stop_error": None, "refreshed_at": None, "cancel_acknowledged_at": None}
    assert run == {**OLD_RUN_PAYLOAD, **added, **PROGRESS_KEYS}


def test_upgraded_rows_read_as_before(upgraded: tuple[Backend, Path]) -> None:
    """The upgraded batch has no composition; the run lists with its elapsed time up to its stored end."""
    backend, _database_file = upgraded
    detail = _ok(backend, {"action": "get_batch", "batch_id": BATCH_ID})
    runs = _ok(backend, {"action": "list_runs"})["runs"]
    assert (detail["batch"]["composition"], len(runs)) == (None, 1)
    run = runs[0]
    assert (run["worker"], run["refreshed_at"], run["elapsed_s"]) == (None, None, RUN_SECONDS)
    assert run["elapsed_until"] == OLD_RUN_PAYLOAD["ended_at"]
    progress = (run["progress_fraction"], run["remaining_estimate_s"], run["estimated_finish_at"])
    assert progress == (None, None, None)
    jobs = detail["batch"]["items"][0]["jobs"]
    codes = {job["language"]: job["result_error_code"] for job in jobs}
    assert codes == {LANGUAGES[job_id]: code for job_id, code in JOB_CODES.items()}


def test_old_jobs_get_their_result_error_code(upgraded: tuple[Backend, Path]) -> None:
    """The desktop's two failure texts get their codes; a worker's error and no error get null."""
    backend, database_file = upgraded
    _ok(backend, {"action": "list_batches"})
    with closing(sqlite3.connect(database_file)) as connection:
        rows = connection.execute("SELECT job_id, payload FROM jobs ORDER BY job_id").fetchall()
    stored = {job_id: json.loads(payload) for job_id, payload in rows}
    expected = {
        job_id: {**OLD_JOB_PAYLOADS[job_id], "result_error_code": code, **RESULT_KEYS}
        for job_id, code in JOB_CODES.items()
    }
    assert stored == expected
