"""Outages during `refresh_status` (plan m5 M5.12, review fixes).

An R2 outage never keeps a billing pod alive past the deadline (the stop needs only Runpod), and
never ends a run whose worker report cannot be read. A Runpod failure during the deadline stop
leaves the run followed, with nothing recorded as ended.
"""

import pytest

from support.credentials import store_request
from support.launch import PODS, error_code, launch_run, pods_of, run_row, send, set_up_cloud
from support.monitor import REFRESH, end_pod, refresh
from support.runs import OUTAGE, RunsWorld, seed_uploaded
from transcribe_ui.runpod.constants import ERROR_CLOUD_UNREACHABLE
from transcribe_ui.storage.constants import ERROR_R2_BUCKET_NOT_FOUND

# A bucket that does not exist (the R2 credentials point nowhere).
MISSING_BUCKET = "no-such-bucket"


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held (the launch publishes it)."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
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
