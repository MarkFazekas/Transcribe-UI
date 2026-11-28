"""Collecting a run's objects again after a straggler (plan m5 M5.12, review fixes).

`reconcile_cloud` reopens a collected run whose name a straggler carries; collection waits while
the straggler is live in the Runpod lists, then keeps its log once, whole.
"""

import pytest

from support.launch import launch_run, pods_of, reconcile, run_row, set_up_cloud
from support.monitor import FailingDeletes, end_pod, refresh, run_objects, write_log, write_status
from support.runs import RunsWorld, seed_ready, set_run_status
from transcribe_ui.runs.collect import collect_run_objects


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest published."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    return runs_world


def test_reconcile_reopens_a_collected_run(world: RunsWorld) -> None:
    """A straggler listed by `reconcile_cloud` reopens its run; collection waits until the straggler is gone."""
    run = launch_run(world)
    end_pod(world, pods_of(world)[0][0])
    refresh(world)
    straggler = world.cloud.api.pods.add(run.pod_name)
    write_status(world.store, run.run_id, "transcribing", world.now())
    reconciled = reconcile(world)
    roles = [entry["role"] for entry in reconciled["entries"]]
    assert (roles, run_row(world, run.run_id)[1].objects_collected) == (["straggler"], False)
    refresh(world)
    waiting = run_row(world, run.run_id)[1].objects_collected
    assert (waiting, len(run_objects(world, run.run_id))) == (False, 1), "nothing collected while it is listed"
    end_pod(world, straggler["id"])
    refresh(world)
    assert (run_row(world, run.run_id)[1].objects_collected, run_objects(world, run.run_id)) == (True, [])


def test_identical_log_not_written_twice(world: RunsWorld) -> None:
    """A collection retried after a failed delete keeps the log once (no `-2` file)."""
    run_id = launch_run(world).run_id
    write_log(world.store, run_id)
    set_run_status(world, "cancelled", run_id)
    assert collect_run_objects(world.ports.state, FailingDeletes(), world.now()) == ()
    refresh(world)
    log_dir = world.ports.state.paths.log_dir
    kept = sorted(path.name for path in log_dir.glob(f"worker-{run_id}*"))
    assert (kept, run_row(world, run_id)[1].objects_collected) == ([f"worker-{run_id}.log"], True)
