"""Results of a `ready` batch read while the worker writes them (plan m5 M5.12, review fixes).

`_done.json` is looked for before the results are listed, so a batch never becomes `transcribed`
with a job whose result the listing missed; an unreadable result fails its job once the batch is
done, so a retry can take it.
"""

from collections.abc import Callable
from functools import partial

import pytest

from support.batches import FIRST_BATCH_ID
from support.credentials import TEST_BUCKET
from support.launch import launch_run, set_up_cloud
from support.monitor import job_statuses, refresh, write_done, write_result
from support.runs import READY, RunsWorld, batch_state, seed_uploaded
from support.storage import store_client
from transcribe_contracts.layout import result_json_key, results_prefix
from transcribe_ui.batches.payloads import JobPayload
from transcribe_ui.runs.constants import (
    MISSING_RESULT_CODE,
    MISSING_RESULT_ERROR,
    UNREADABLE_RESULT_CODE,
    UNREADABLE_RESULT_ERROR,
)
from transcribe_ui.runs.results import check_ready_batches
from transcribe_ui.state.models import JobRow
from transcribe_ui.storage.r2 import ObjectInfo, R2ObjectStore

# Item ids of the seeded batch.
FILM_ID = "01JDZZZZZZZZZZZZZZZZZZZZ01"
PODCAST_ID = "01JDZZZZZZZZZZZZZZZZZZZZ02"
# Batch state of a batch that has its `_done.json`.
TRANSCRIBED = ("transcribed", False, True)
# Batch state of a batch with `_done.json` whose every job failed (none blocks `downloaded`).
DOWNLOADED = ("downloaded", False, True)


class WorkerFinishingStore(R2ObjectStore):
    """The test bucket, where the worker finishes the batch right after its results were listed."""

    def __init__(self, finish: Callable[[], None]) -> None:
        """Run `finish` once, after the first listing of the seeded batch's results."""
        super().__init__(store_client(), TEST_BUCKET)
        self._finish: Callable[[], None] | None = finish

    def list_objects(self, prefix: str) -> tuple[ObjectInfo, ...]:
        """List, then let the worker write (once)."""
        listed = super().list_objects(prefix)
        finish = self._finish
        if prefix == results_prefix(FIRST_BATCH_ID) and finish is not None:
            self._finish = None
            finish()
        return listed


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held (the launch publishes it)."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    return runs_world


def test_done_written_after_the_listing(world: RunsWorld) -> None:
    """The last result and `_done.json` arrive between the listing and the check: nothing is lost."""
    run_id = launch_run(world).run_id
    write_result(world.store, FIRST_BATCH_ID, FILM_ID, run_id, world.now())

    check_ready_batches(world.context(), WorkerFinishingStore(partial(_finish, world, run_id)))
    assert (batch_state(world), job_statuses(world, FIRST_BATCH_ID)) == (
        READY,
        {FILM_ID: "ok", PODCAST_ID: "pending"},
    )
    refresh(world)
    assert (batch_state(world), job_statuses(world, FIRST_BATCH_ID)) == (
        TRANSCRIBED,
        {FILM_ID: "ok", PODCAST_ID: "no_speech"},
    )


def _finish(world: RunsWorld, run_id: str) -> None:
    """The worker writes the batch's last result, then `_done.json`."""
    write_result(world.store, FIRST_BATCH_ID, PODCAST_ID, run_id, world.now(), status="no_speech")
    write_done(world.store, FIRST_BATCH_ID, run_id, world.now())


def _result_error(world: RunsWorld, item_id: str) -> tuple[str | None, str | None]:
    """Return the stored failure of an item's job: its text and its code."""
    with world.ports.state.session() as session:
        row = session.query(JobRow).filter(JobRow.item_id == item_id).one()
        payload = JobPayload.model_validate(row.payload)
        return payload.result_error, payload.result_error_code


def test_unreadable_result_fails_once_done(world: RunsWorld) -> None:
    """An unreadable result stays pending until `_done.json` is there; then its job fails with the reason."""
    run_id = launch_run(world).run_id
    write_result(world.store, FIRST_BATCH_ID, FILM_ID, run_id, world.now(), status="failed")
    world.store.write(result_json_key(FIRST_BATCH_ID, PODCAST_ID, "en"), b"not json", "application/json")
    refresh(world)
    assert job_statuses(world, FIRST_BATCH_ID) == {FILM_ID: "failed", PODCAST_ID: "pending"}
    write_done(world.store, FIRST_BATCH_ID, run_id, world.now())
    done = refresh(world)
    assert (job_statuses(world, FIRST_BATCH_ID), [each.failed for each in done.batches]) == (
        {FILM_ID: "failed", PODCAST_ID: "failed"},
        [2],
    )
    reasons = (_result_error(world, FILM_ID), _result_error(world, PODCAST_ID))
    # Every job failed: nothing is left to download, so the `downloaded` rule moves the batch on.
    # The worker's own error has no code; the desktop's has one (the frontends translate it).
    expected = (("decoding failed", None), (UNREADABLE_RESULT_ERROR, UNREADABLE_RESULT_CODE))
    assert (reasons, batch_state(world)) == (expected, DOWNLOADED)


def test_missing_result_fails_once_done(world: RunsWorld) -> None:
    """A job without any result JSON fails (`missing result`) once its batch has `_done.json`."""
    run_id = launch_run(world).run_id
    write_result(world.store, FIRST_BATCH_ID, FILM_ID, run_id, world.now())
    write_done(world.store, FIRST_BATCH_ID, run_id, world.now())
    refresh(world)
    assert (job_statuses(world, FIRST_BATCH_ID), _result_error(world, PODCAST_ID)) == (
        {FILM_ID: "ok", PODCAST_ID: "failed"},
        (MISSING_RESULT_ERROR, MISSING_RESULT_CODE),
    )
