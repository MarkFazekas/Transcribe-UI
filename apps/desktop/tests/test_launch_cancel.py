"""`cancel_worker` (plan m5 M5.11): a queued pod that deploys during the cancel, a started run, verification."""

import httpx
import pytest

from support.launch import (
    CANCEL,
    PODS,
    POST,
    error_code,
    launch_run,
    ok_as,
    pods_of,
    queued_of,
    run_row,
    send,
    set_up_cloud,
)
from support.runpod_api import Failure
from support.runs import OUTAGE, RunsWorld, seed_uploaded, set_run_status
from transcribe_ui.runpod.constants import (
    ERROR_CLOUD_UNREACHABLE,
    POD_TERMINATED,
    QUEUED_POD_CANCELLED,
    QUEUED_POD_DEPLOYED,
)
from transcribe_ui.runs.constants import (
    ERROR_LAUNCH_UNRESOLVED,
    ERROR_RUN_NOT_ACTIVE,
    ERROR_RUN_NOT_FOUND,
    ERROR_STOP_UNVERIFIED,
)
from transcribe_ui.runs.responses import CancelWorkerResponse

# A run id this database does not know.
UNKNOWN_RUN = "01JDZZZZZZZZZZZZZZZZZZZZZ9"


@pytest.fixture
def ready(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    return runs_world


def _launch(world: RunsWorld, *, capacity: bool) -> str:
    """Launch on the seeded held batch; return the run id."""
    world.cloud.settings.capacity = capacity
    return launch_run(world).run_id


def _cancel(world: RunsWorld, run_id: str) -> CancelWorkerResponse:
    return ok_as(send(world, CANCEL, run_id=run_id), CancelWorkerResponse)


def _deploy_at_next_request(world: RunsWorld) -> None:
    """Let the run's queued pod deploy at Runpod's next request (here: the cancel's DELETE)."""
    world.cloud.api.queued_pods.schedule_deploy(queued_of(world)[0][0], world.now())


def test_cancel_while_queued(ready: RunsWorld) -> None:
    """The queued pod is cancelled and reads `CANCELLED`: the run is `cancelled`."""
    cancelled = _cancel(ready, _launch(ready, capacity=False))
    assert (cancelled.run.status, cancelled.terminated_pod_id, queued_of(ready)[0][1]) == (
        "cancelled",
        None,
        QUEUED_POD_CANCELLED,
    )


def test_cancel_with_a_deploy_during_the_cancel(ready: RunsWorld) -> None:
    """The queued pod deploys while it is cancelled: its pod is terminated, verified, then the run is `cancelled`."""
    run_id = _launch(ready, capacity=False)
    _deploy_at_next_request(ready)
    cancelled = _cancel(ready, run_id)
    pod_id, pod_status = pods_of(ready)[0]
    assert (queued_of(ready)[0][1], pod_status) == (QUEUED_POD_DEPLOYED, POD_TERMINATED)
    assert (cancelled.run.status, cancelled.terminated_pod_id, cancelled.run.pod_id) == ("cancelled", pod_id, pod_id)


def test_cancelled_only_after_verified_termination(ready: RunsWorld) -> None:
    """A deployed pod whose terminate does not take effect: the run follows the pod and stays active."""
    run_id = _launch(ready, capacity=False)
    _deploy_at_next_request(ready)
    ready.cloud.settings.terminate_effective = False
    refused = send(ready, CANCEL, run_id=run_id)
    status, payload = run_row(ready, run_id)
    pod_id, pod_status = pods_of(ready)[0]
    assert (error_code(refused), status, payload.pod_id, pod_status) == (
        ERROR_STOP_UNVERIFIED,
        "running",
        pod_id,
        "RUNNING",
    )
    ready.cloud.settings.terminate_effective = True
    assert (_cancel(ready, run_id).run.status, pods_of(ready)[0][1]) == ("cancelled", POD_TERMINATED)


def test_cancel_while_running(ready: RunsWorld) -> None:
    """A started run's pod is terminated and verified; cancelling again answers the same."""
    run_id = _launch(ready, capacity=True)
    cancelled = _cancel(ready, run_id)
    pod_id, pod_status = pods_of(ready)[0]
    assert (cancelled.run.status, cancelled.terminated_pod_id, pod_status) == ("cancelled", pod_id, POD_TERMINATED)
    assert _cancel(ready, run_id).run.status == "cancelled"


def test_failed_termination_read_keeps_the_run(ready: RunsWorld) -> None:
    """A follow-up read that fails leaves the run `running` (not cancelled)."""
    run_id = _launch(ready, capacity=True)
    ready.cloud.api.fail("GET", f"{PODS}/{pods_of(ready)[0][0]}", *OUTAGE)
    assert (error_code(send(ready, CANCEL, run_id=run_id)), run_row(ready, run_id)[0]) == (
        ERROR_CLOUD_UNREACHABLE,
        "running",
    )


def test_cancel_refusals(ready: RunsWorld) -> None:
    """Unknown and finished runs are refused."""
    run_id = _launch(ready, capacity=True)
    assert error_code(send(ready, CANCEL, run_id=UNKNOWN_RUN)) == ERROR_RUN_NOT_FOUND
    set_run_status(ready, "finished", run_id)
    assert error_code(send(ready, CANCEL, run_id=run_id)) == ERROR_RUN_NOT_ACTIVE


def test_cancel_of_an_unresolved_launch(ready: RunsWorld) -> None:
    """A `launching` run whose pod is not listed cannot be stopped yet; it stays `launching`."""
    ready.cloud.api.fail(POST, PODS, Failure(status=httpx.codes.SERVICE_UNAVAILABLE))
    run_id = launch_run(ready).run_id
    assert (error_code(send(ready, CANCEL, run_id=run_id)), run_row(ready, run_id)[0]) == (
        ERROR_LAUNCH_UNRESOLVED,
        "launching",
    )
