"""Collecting run objects (plan m5 M5.12, review fixes): the order of the steps, and stragglers.

The worker's last status is kept in the run row before anything is deleted, and the run is
marked collected last. A straggler pod carrying a collected run's name may write objects again;
stopping it marks the run uncollected, so the next refresh collects them.
"""

import pytest

from support.credentials import TEST_BUCKET
from support.launch import TERMINATE, launch_run, ok_as, pods_of, run_row, send, set_up_cloud
from support.monitor import end_pod, refresh, run_objects, write_log, write_status
from support.runs import RunsWorld, seed_uploaded, set_run_status
from support.storage import store_client
from transcribe_ui.runs.collect import collect_run_objects
from transcribe_ui.runs.responses import TerminatePodResponse
from transcribe_ui.storage.constants import ERROR_R2_UNREACHABLE
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import R2ObjectStore

# Objects the worker leaves under `runs/<run_id>/`: its status and its log.
WORKER_OBJECTS = 2


class FailingDeletes(R2ObjectStore):
    """The test bucket, where every delete fails (R2 unreachable)."""

    def __init__(self) -> None:
        """Use the test bucket."""
        super().__init__(store_client(), TEST_BUCKET)

    def delete(self, key: str) -> None:  # noqa: ARG002 -- every delete fails, whatever the key
        """Fail (the object stays)."""
        raise StorageError(ERROR_R2_UNREACHABLE, "delete", "ConnectionError")


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held (the launch publishes it)."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
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
        WORKER_OBJECTS,
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
