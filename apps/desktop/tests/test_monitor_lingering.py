"""Collection and stragglers in `refresh_status` (plan m5 M5.12, review fixes).

Queued-pod stragglers block collection like pods do, a deployed one judged by its pod (looked up by
id when the list does not show it); a failed lookup only puts collection off (it is housekeeping).
"""

from datetime import timedelta

import pytest

from support.launch import PODS, launch_run, pods_of, reconcile, run_row, send, set_up_cloud
from support.monitor import REFRESH, end_pod, refresh, run_objects, write_status
from support.runpod_api import iso
from support.runs import OUTAGE, RunsWorld, seed_ready
from transcribe_ui.runs.constants import DEPLOY_SETTLE_S
from transcribe_ui.runs.responses import RunInfo

# Item id of the seeded film.
FILM_ID = "01JDZZZZZZZZZZZZZZZZZZZZ01"
# Name of the pod a deployed straggler names (not an app name, so only the lookup finds it).
ELSEWHERE = "renamed-elsewhere"


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest published."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    return runs_world


def _collected(world: RunsWorld, run_id: str) -> bool:
    return run_row(world, run_id)[1].objects_collected


def _ended_and_collected(world: RunsWorld) -> RunInfo:
    """Launch, let the pod end itself, collect the run; return it."""
    run = launch_run(world)
    end_pod(world, pods_of(world)[0][0])
    refresh(world)
    assert _collected(world, run.run_id)
    return run


def _deployed_straggler(world: RunsWorld, run: RunInfo) -> str:
    """A queued pod with the run's name that deployed a pod the lists do not show; return that pod's id."""
    entry = world.cloud.api.queued_pods.add(run.pod_name, status="DEPLOYED")
    world.cloud.settings.list_lag = timedelta(days=1)
    pod_id = str(world.cloud.api.pods.add(ELSEWHERE)["id"])
    entry.update({"podId": pod_id, "resolvedAt": iso(world.now())})
    return pod_id


def test_queued_straggler_blocks_collection(world: RunsWorld) -> None:
    """A waiting queued pod with the run's name: nothing collected until it is cancelled."""
    run = _ended_and_collected(world)
    entry = world.cloud.api.queued_pods.add(run.pod_name)
    write_status(world.store, run.run_id, "starting", world.now())
    reconcile(world)
    refresh(world)
    assert (_collected(world, run.run_id), len(run_objects(world, run.run_id))) == (False, 1)
    entry["status"] = "CANCELLED"
    refresh(world)
    assert (_collected(world, run.run_id), run_objects(world, run.run_id)) == (True, [])


def test_deployed_straggler_looked_up(world: RunsWorld) -> None:
    """A deployed queued pod whose pod only a lookup by id shows: collection waits until it is gone and settled."""
    run = _ended_and_collected(world)
    pod_id = _deployed_straggler(world, run)
    write_status(world.store, run.run_id, "transcribing", world.now())
    reconcile(world)
    refresh(world)
    assert not _collected(world, run.run_id)
    pods = world.cloud.api.pods
    pods.records.pop(pod_id)
    world.cloud.clock.advance(timedelta(seconds=DEPLOY_SETTLE_S))
    refresh(world)
    assert (_collected(world, run.run_id), run_objects(world, run.run_id)) == (True, [])


def test_failed_lookup_puts_collection_off(world: RunsWorld) -> None:
    """The straggler's pod cannot be looked up: the refresh succeeds and the run waits, counted as straggling."""
    run = _ended_and_collected(world)
    pod_id = _deployed_straggler(world, run)
    write_status(world.store, run.run_id, "transcribing", world.now())
    reconcile(world)
    world.cloud.api.fail("GET", f"{PODS}/{pod_id}", *OUTAGE)
    assert (send(world, REFRESH).status, _collected(world, run.run_id)) == ("ok", False)
