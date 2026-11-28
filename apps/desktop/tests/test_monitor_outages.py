"""Outages during `refresh_status` (plan m5 M5.12, review fixes).

An R2 outage never keeps a billing pod alive past the deadline (the stop needs only Runpod), and
never ends a run whose worker report cannot be read. A Runpod failure during the deadline stop
leaves the run followed, with nothing recorded as ended.
"""

from datetime import timedelta

import pytest

from support.credentials import store_request
from support.launch import PODS, error_code, launch_run, pods_of, run_row, send, set_up_cloud
from support.monitor import REFRESH, end_pod, refresh
from support.runs import OUTAGE, RUN_ID, RunsWorld, app_pod_name, seed_ready
from transcribe_ui.runpod.constants import ERROR_CLOUD_UNREACHABLE
from transcribe_ui.runs.records import insert_run, update_run
from transcribe_ui.storage.constants import ERROR_R2_BUCKET_NOT_FOUND

# A bucket that does not exist (the R2 credentials point nowhere).
MISSING_BUCKET = "no-such-bucket"


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest published."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    return runs_world


def _break_r2(world: RunsWorld) -> None:
    """Point the stored R2 credentials at a bucket that does not exist."""
    stored = world.leg.backend.execute_json(store_request(r2_bucket=MISSING_BUCKET))
    assert stored.status == "ok"


def _launched_until_deadline(world: RunsWorld) -> str:
    """Launch, see the pod running once, move the clock to the desktop deadline; return the run id."""
    run_id = launch_run(world).run_id
    world.cloud.clock.moment = refresh(world).runs[0].deadline
    return run_id


def test_r2_outage_past_the_deadline_still_stops(world: RunsWorld) -> None:
    """R2 cannot be read past the deadline: the pod is terminated and the run fails; the error is reported."""
    run_id = _launched_until_deadline(world)
    _break_r2(world)
    refused = send(world, REFRESH)
    status, payload = run_row(world, run_id)
    assert (error_code(refused), status, payload.end_reason, pods_of(world)) == (
        ERROR_R2_BUCKET_NOT_FOUND,
        "failed",
        "deadline",
        [],
    )


def test_r2_outage_never_ends_a_run(world: RunsWorld) -> None:
    """The pod is gone but the worker report cannot be read: the run is not ended (fail closed)."""
    run_id = launch_run(world).run_id
    refresh(world)
    end_pod(world, pods_of(world)[0][0])
    _break_r2(world)
    refused = send(world, REFRESH)
    status, payload = run_row(world, run_id)
    assert (error_code(refused), status, payload.ended_at) == (ERROR_R2_BUCKET_NOT_FOUND, "running", None)
    world.leg.backend.execute_json(store_request())
    assert refresh(world).runs[0].end_reason == "pod_gone"


@pytest.mark.parametrize("method", ["DELETE", "GET"])
def test_runpod_failure_during_the_deadline_stop(world: RunsWorld, method: str) -> None:
    """The pod's delete or its follow-up read fails: the run stays `running`, nothing is recorded as ended."""
    run_id = _launched_until_deadline(world)
    world.cloud.api.fail(method, f"{PODS}/{pods_of(world)[0][0]}", *OUTAGE)
    refused = send(world, REFRESH)
    status, payload = run_row(world, run_id)
    assert (error_code(refused), status, payload.ended_at, payload.end_reason) == (
        ERROR_CLOUD_UNREACHABLE,
        "running",
        None,
        None,
    )


def _seed_second_run(world: RunsWorld, like: str) -> str:
    """Record a second running run (`RUN_ID`, followed before `like`) with a pod of its own; return the pod id."""
    name = app_pod_name(RUN_ID, world.now())
    pod_id = str(world.cloud.api.pods.add(name)["id"])
    earlier = run_row(world, like)[1]
    changes = {"pod_id": pod_id, "pod_seen": True, "queued_pod_id": None, "first_running_at": None}
    payload = earlier.model_copy(update={**changes, "name_deadline": earlier.name_deadline + timedelta(days=1)})
    insert_run(world.ports.state, RUN_ID, name, world.now(), payload)
    update_run(world.ports.state, RUN_ID, world.now(), "running")
    return pod_id


def test_r2_failure_of_one_run_still_stops_another(world: RunsWorld) -> None:
    """R2 fails while one run is followed; the other, past its deadline, is still stopped; then the error."""
    late_run = _launched_until_deadline(world)
    assert late_run > RUN_ID, "the run seeded below is followed first"
    late_pod = pods_of(world)[0][0]
    other_pod = _seed_second_run(world, late_run)
    _break_r2(world)
    refused = send(world, REFRESH)
    late_status, late = run_row(world, late_run)
    pods = [pod_id for pod_id, _ in pods_of(world)]
    assert (error_code(refused), late_status, late.end_reason, run_row(world, RUN_ID)[0]) == (
        ERROR_R2_BUCKET_NOT_FOUND,
        "failed",
        "deadline",
        "running",
    )
    assert (late_pod in pods, pods) == (False, [other_pod])
