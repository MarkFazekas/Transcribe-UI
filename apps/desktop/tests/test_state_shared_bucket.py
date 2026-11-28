"""The revision `shared_bucket` (plan m6 M6.13): a batch held back before the upgrade, published after it.

A database of the previous revision may hold an `uploading` batch with `manifest_held` set (the
upload complete, its manifest kept back during a run). The revision drops the flag; the batch
stays `uploading`, and `run_upload` publishes its manifest. The old database is made by taking a
current one back with the revision's own downgrade.
"""

import json
import sqlite3
from contextlib import ExitStack, closing
from importlib.resources import as_file, files

from alembic import command
from alembic.config import Config

from support.batches import FIRST_BATCH_ID
from support.launch import send
from support.runs import READY, RunsWorld, batch_state, seed_uploaded
from transcribe_ui.state.constants import MIGRATION_CONNECTION_KEY, MIGRATIONS_FOLDER, MIGRATIONS_PACKAGE
from transcribe_ui.state.database import sqlite_engine

# The revision before `shared_bucket`, and `shared_bucket` itself.
PREVIOUS_REVISION = "609bed4e68d4"
SHARED_BUCKET_REVISION = "8e5b2d7c1f40"


def _downgrade(world: RunsWorld) -> None:
    """Take the world's closed database back to the previous revision."""
    engine = sqlite_engine(world.ports.state.paths.database_file, foreign_keys=False)
    with ExitStack() as stack:
        stack.callback(engine.dispose)
        location = stack.enter_context(as_file(files(MIGRATIONS_PACKAGE) / MIGRATIONS_FOLDER))
        connection = stack.enter_context(engine.begin())
        config = Config()
        config.set_main_option("script_location", str(location).replace("%", "%%"))
        config.attributes[MIGRATION_CONNECTION_KEY] = connection
        command.downgrade(config, PREVIOUS_REVISION)


def _query(world: RunsWorld, sql: str) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(world.ports.state.paths.database_file)) as connection, connection:
        return connection.execute(sql).fetchall()


def test_held_batch_is_published_after_the_upgrade(runs_world: RunsWorld) -> None:
    """A batch held at the upgrade stays `uploading`; `run_upload` then publishes it."""
    seed_uploaded(runs_world)
    runs_world.ports.state.close()
    _downgrade(runs_world)
    _query(runs_world, "UPDATE batches SET manifest_held = 1")
    uploaded = send(runs_world, "run_upload", batch_id=FIRST_BATCH_ID)
    assert uploaded.status == "ok", uploaded
    columns = {str(row[1]) for row in _query(runs_world, "PRAGMA table_info(batches)")}
    payload = json.loads(str(_query(runs_world, "SELECT payload FROM batches")[0][0]))
    revision = _query(runs_world, "SELECT version_num FROM alembic_version")[0][0]
    assert (revision, "manifest_held" in columns, "removed_from_cloud" in columns) == (
        SHARED_BUCKET_REVISION,
        False,
        True,
    )
    assert (payload["adopted"], batch_state(runs_world)) == (None, READY)
