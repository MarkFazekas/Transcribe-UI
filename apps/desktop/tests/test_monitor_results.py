"""`refresh_status` (plan m5 M5.12): job results and `_done.json` of `ready` batches."""

import pytest

from support.batches import FIRST_BATCH_ID
from support.launch import launch_run, pods_of, run_ids, set_up_cloud
from support.launch_seed import seed_audio_batch
from support.monitor import end_pod, job_statuses, progress_of, refresh, write_done, write_result
from support.runs import READY, RunsWorld, batch_state, seed_uploaded
from transcribe_contracts.layout import manifest_key, result_json_key
from transcribe_ui.state.models import BatchRow

# A second batch of this database.
OTHER_BATCH_ID = "batch_20261127_140000"
# Its single item (the podcast).
OTHER_ITEM_ID = "01JDZZZZZZZZZZZZZZZZZZZZ03"
# Item ids of the seeded batch.
FILM_ID = "01JDZZZZZZZZZZZZZZZZZZZZ01"
PODCAST_ID = "01JDZZZZZZZZZZZZZZZZZZZZ02"
# Batch state of a batch that has its `_done.json`.
TRANSCRIBED = ("transcribed", False, True)


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held (the launch publishes it)."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    return runs_world


def _seed_other_ready(world: RunsWorld) -> None:
    """Seed a second batch already `ready` (manifest in R2), as one published before the current run."""
    seed_audio_batch(world, OTHER_BATCH_ID, OTHER_ITEM_ID)
    with world.ports.state.session() as session:
        row = session.get(BatchRow, OTHER_BATCH_ID)
        assert row is not None
        row.phase = "ready"
    world.store.write(manifest_key(OTHER_BATCH_ID), b"{}", "application/json")


def test_batch_outside_the_run_gets_its_done_seen(world: RunsWorld) -> None:
    """Every `ready` batch is checked, not only the run's: one the run does not cover becomes `transcribed`."""
    run_id = launch_run(world).run_id
    _seed_other_ready(world)
    write_result(world.store, OTHER_BATCH_ID, OTHER_ITEM_ID, run_id, world.now())
    write_done(world.store, OTHER_BATCH_ID, run_id, world.now())
    refreshed = refresh(world)
    phases = {each.batch_id: each.phase for each in refreshed.batches}
    assert (phases, progress_of(refreshed, run_id).run.status) == (
        {FIRST_BATCH_ID: "ready", OTHER_BATCH_ID: "transcribed"},
        "running",
    )
    assert (batch_state(world, OTHER_BATCH_ID), job_statuses(world, OTHER_BATCH_ID)) == (
        TRANSCRIBED,
        {OTHER_ITEM_ID: "ok"},
    )


def test_results_without_a_run(world: RunsWorld) -> None:
    """With no run at all, a `ready` batch still gets its results and `_done.json` seen."""
    launch_run(world)
    run_id = run_ids(world)[0]
    end_pod(world, pods_of(world)[0][0])
    refresh(world)
    write_result(world.store, FIRST_BATCH_ID, FILM_ID, run_id, world.now(), status="failed")
    world.store.write(result_json_key(FIRST_BATCH_ID, PODCAST_ID, "en"), b"not json", "application/json")
    partial = refresh(world)
    assert (partial.runs, job_statuses(world, FIRST_BATCH_ID)) == ((), {FILM_ID: "failed", PODCAST_ID: "pending"})
    assert [(each.failed, each.pending, each.phase) for each in partial.batches] == [(1, 1, "ready")]
    write_result(world.store, FIRST_BATCH_ID, PODCAST_ID, run_id, world.now(), status="no_speech")
    write_done(world.store, FIRST_BATCH_ID, run_id, world.now())
    done = refresh(world)
    assert [(each.failed, each.no_speech, each.phase) for each in done.batches] == [(1, 1, "transcribed")]
    assert refresh(world).batches == ()


def test_busy_ready_batch_skipped(world: RunsWorld) -> None:
    """A `ready` batch whose lock another request holds is skipped until the next refresh."""
    run_id = launch_run(world).run_id
    write_done(world.store, FIRST_BATCH_ID, run_id, world.now())
    with world.ports.state.locks.batch(FIRST_BATCH_ID):
        skipped = refresh(world)
    assert (skipped.busy, skipped.batches, batch_state(world)) == ((FIRST_BATCH_ID,), (), READY)
    assert refresh(world).busy == ()
    assert batch_state(world) == TRANSCRIBED
