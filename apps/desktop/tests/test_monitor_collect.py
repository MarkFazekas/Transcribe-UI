"""Collecting run objects (plan m5 M5.12, review fixes): the order of the steps, and stragglers.

The worker's last status is kept in the run row before anything is deleted, and the run is
marked collected last. A straggler pod carrying a collected run's name may write objects again;
listing or stopping it marks the run uncollected, and the first refresh after it is gone collects
them. A kept log is never written twice or over another.
"""

import pytest

from support.launch import TERMINATE, launch_run, ok_as, pods_of, run_row, send, set_up_cloud
from support.monitor import WORKER_LOG, FailingDeletes, end_pod, refresh, run_objects, write_log, write_status
from support.runs import RunsWorld, seed_ready, set_run_status
from transcribe_ui.runs.collect import collect_run_objects
from transcribe_ui.runs.responses import TerminatePodResponse
from transcribe_ui.runs.worker_logs import worker_log_path

# Objects under `runs/<run_id>/` here: the worker's status and log, and the run's lease (the run's
# status is set directly, so no request ended the run and deleted its lease).
RUN_OBJECTS = 3
# Log a straggler of a collected run writes.
STRAGGLER_LOG = "straggler started\n"


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest published."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    return runs_world


def test_status_kept_before_the_deletes(world: RunsWorld) -> None:
    """A delete fails: the worker's status is already kept, the run is not marked collected, the next refresh is."""
    run_id = launch_run(world).run_id
    write_status(world.store, run_id, "finished", world.now(), done_jobs=2)
    write_log(world.store, run_id)
    set_run_status(world, "cancelled", run_id)
    assert collect_run_objects(world.ports.state, FailingDeletes(), world.now()) == ()
    payload = run_row(world, run_id)[1]
    assert payload.final_status is not None
    assert (payload.final_status.state, payload.objects_collected, len(run_objects(world, run_id))) == (
        "finished",
        False,
        RUN_OBJECTS,
    )
    refresh(world)
    assert (run_row(world, run_id)[1].objects_collected, run_objects(world, run_id)) == (True, [])


def test_straggler_of_a_collected_run(world: RunsWorld) -> None:
    """A straggler with a collected run's name writes again; stopping it gets the run's objects collected again."""
    run = launch_run(world)
    end_pod(world, pods_of(world)[0][0])
    refresh(world)
    assert run_row(world, run.run_id)[1].objects_collected
    straggler = world.cloud.api.pods.add(run.pod_name)
    write_status(world.store, run.run_id, "transcribing", world.now())
    stopped = ok_as(send(world, TERMINATE, pod_id=straggler["id"], confirm=True), TerminatePodResponse)
    assert (stopped.local_status, run_row(world, run.run_id)[1].objects_collected) == ("failed", False)
    refresh(world)
    assert (run_row(world, run.run_id)[1].objects_collected, run_objects(world, run.run_id)) == (True, [])


def test_recollection_keeps_the_first_log(world: RunsWorld) -> None:
    """A straggler's log collected again goes to `worker-<run_id>-2.log`; the log kept first stays."""
    run = launch_run(world)
    write_log(world.store, run.run_id)
    end_pod(world, pods_of(world)[0][0])
    refresh(world)
    log_dir = world.ports.state.paths.log_dir
    straggler = world.cloud.api.pods.add(run.pod_name)
    write_log(world.store, run.run_id, STRAGGLER_LOG)
    send(world, TERMINATE, pod_id=straggler["id"], confirm=True)
    refresh(world)
    first = worker_log_path(log_dir, run.run_id)
    second = log_dir / f"worker-{run.run_id}-2.log"
    assert (first.read_text(), second.read_text()) == (WORKER_LOG, STRAGGLER_LOG)
