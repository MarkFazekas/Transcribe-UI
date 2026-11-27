"""Collection is housekeeping in `refresh_status` (plan m5 M5.12, review fixes).

Unreadable Runpod lists with no open run only put collection off (the batches' results still come
in), and the run's own pod, stopped by the same refresh, never blocks its collection.
"""

import pytest

from support.batches import FIRST_BATCH_ID
from support.launch import PODS, launch_run, run_row, set_up_cloud
from support.monitor import job_statuses, refresh, run_objects, write_log, write_result, write_status
from support.runs import OUTAGE, RunsWorld, seed_uploaded, set_run_status

# Item id of the seeded film.
FILM_ID = "01JDZZZZZZZZZZZZZZZZZZZZ01"


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held (the launch publishes it)."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    return runs_world


def _collected(world: RunsWorld, run_id: str) -> bool:
    return run_row(world, run_id)[1].objects_collected


def test_list_failure_without_open_run(world: RunsWorld) -> None:
    """No run open, a final run to collect, the lists unreadable: the batch's results still come in."""
    run_id = launch_run(world).run_id
    write_status(world.store, run_id, "finished", world.now(), done_jobs=2)
    set_run_status(world, "cancelled", run_id)
    write_result(world.store, FIRST_BATCH_ID, FILM_ID, run_id, world.now())
    world.cloud.api.fail("GET", PODS, *OUTAGE)
    refreshed = refresh(world)
    film = job_statuses(world, FIRST_BATCH_ID)[FILM_ID]
    assert ([each.ok for each in refreshed.batches], film, _collected(world, run_id)) == ([1], "ok", False)
    refresh(world)
    assert (_collected(world, run_id), run_objects(world, run_id)) == (True, [])


def test_own_pod_never_blocks_collection(world: RunsWorld) -> None:
    """The refresh that stops a run at its deadline collects it too: the run's own pod is not a straggler."""
    run_id = launch_run(world).run_id
    write_status(world.store, run_id, "transcribing", world.now())
    write_log(world.store, run_id)
    world.cloud.clock.moment = refresh(world).runs[0].deadline
    stopped = refresh(world).runs[0]
    assert (stopped.end_reason, _collected(world, run_id), run_objects(world, run_id)) == ("deadline", True, [])
