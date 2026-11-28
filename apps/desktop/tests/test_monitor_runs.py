"""`refresh_status` (plan m5 M5.12): awaited results, run objects, `launching` runs, failing closed."""

import shutil
from datetime import timedelta

import pytest

from support.launch import PODS, POST, error_code, launch_run, pods_of, run_row, send, set_up_cloud
from support.launch_seed import seed_audio_batch
from support.monitor import end_pod, progress_of, refresh, refreshed_run, run_objects, write_log, write_status
from support.runpod_api import Failure
from support.runs import OUTAGE, READY, RunsWorld, batch_state, seed_ready
from transcribe_ui.runpod.constants import ERROR_CLOUD_UNREACHABLE
from transcribe_ui.runs.worker_logs import worker_log_path

# A second batch of this database.
OTHER_BATCH_ID = "batch_20261127_140000"
# Its single item (the podcast).
OTHER_ITEM_ID = "01JDZZZZZZZZZZZZZZZZZZZZ03"
# Item ids of the seeded batch.
FILM_ID = "01JDZZZZZZZZZZZZZZZZZZZZ01"
PODCAST_ID = "01JDZZZZZZZZZZZZZZZZZZZZ02"
# Objects the worker leaves under `runs/<run_id>/`: its status and its log.
WORKER_OBJECTS = 2


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest published."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    return runs_world


def test_batch_published_during_a_run_stays_awaited(world: RunsWorld) -> None:
    """A batch published during a run is not in its lease: still `ready` and awaited after the run ends."""
    run_id = launch_run(world).run_id
    seed_audio_batch(world, OTHER_BATCH_ID, OTHER_ITEM_ID)
    world.step(OTHER_BATCH_ID)
    during = refresh(world)
    assert (during.awaiting_results, batch_state(world, OTHER_BATCH_ID)) == (True, READY)
    write_status(world.store, run_id, "finished", world.now(), done_jobs=2)
    end_pod(world, pods_of(world)[0][0])
    after = refresh(world)
    assert (progress_of(after, run_id).run.status, after.awaiting_results) == ("finished", True)
    assert batch_state(world, OTHER_BATCH_ID) == READY


def test_run_objects_kept_until_collected(world: RunsWorld) -> None:
    """A failed collection keeps `runs/<run_id>/` for the next refresh, which keeps and deletes it."""
    run_id = launch_run(world).run_id
    write_status(world.store, run_id, "finished", world.now(), done_jobs=2)
    write_log(world.store, run_id)
    log_dir = world.ports.state.paths.log_dir
    shutil.rmtree(log_dir, ignore_errors=True)
    log_dir.parent.mkdir(parents=True, exist_ok=True)
    log_dir.write_text("in the way")
    end_pod(world, pods_of(world)[0][0])
    ended = refreshed_run(world, run_id)
    assert (ended.run.status, ended.worker_log, len(run_objects(world, run_id))) == ("finished", None, WORKER_OBJECTS)
    assert not run_row(world, run_id)[1].objects_collected
    log_dir.unlink()
    refresh(world)
    assert (run_objects(world, run_id), worker_log_path(log_dir, run_id).is_file()) == ([], True)
    assert run_row(world, run_id)[1].objects_collected


def test_launching_run_resolved(world: RunsWorld) -> None:
    """A run whose pod request's answer was lost is adopted by the refresh once its pod is listed."""
    world.cloud.settings.list_lag = timedelta(minutes=2)
    world.cloud.api.fail(POST, PODS, Failure(lost_response=True))
    run_id = launch_run(world).run_id
    assert refreshed_run(world, run_id).stage == "launching"
    world.cloud.clock.advance(timedelta(minutes=3))
    adopted = refreshed_run(world, run_id)
    assert (adopted.run.status, adopted.run.pod_id, adopted.pod_status) == ("running", pods_of(world)[0][0], "RUNNING")


def test_refresh_fails_closed_on_runpod(world: RunsWorld) -> None:
    """The Runpod lists cannot be read: the refresh fails and the run stays as it was."""
    run_id = launch_run(world).run_id
    world.cloud.api.fail("GET", PODS, *OUTAGE)
    refused = send(world, "refresh_status")
    assert (error_code(refused), run_row(world, run_id)[0]) == (ERROR_CLOUD_UNREACHABLE, "running")
