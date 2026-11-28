"""`cancel_worker` (plan m5 M5.11): a queued pod that deploys during the cancel, a started run, verification."""

from datetime import timedelta
from functools import partial

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
from support.runpod_api import Failure, Record
from support.runs import OUTAGE, RunsWorld, seed_ready, set_run_status
from transcribe_ui.api.classes import NeedsConfirmation
from transcribe_ui.runpod.constants import ERROR_CLOUD_UNREACHABLE, QUEUED_POD_CANCELLED
from transcribe_ui.runs.constants import (
    DEPLOY_SETTLE_S,
    ERROR_LAUNCH_UNRESOLVED,
    ERROR_RUN_NOT_ACTIVE,
    ERROR_RUN_NOT_FOUND,
    ERROR_STOP_UNVERIFIED,
)
from transcribe_ui.runs.responses import CancelWorkerResponse

# A run id this database does not know.
UNKNOWN_RUN = "01JDZZZZZZZZZZZZZZZZZZZZZ9"
# Status of a cancelled run.
CANCELLED = "cancelled"
# Path prefix of one pod.
POD_PATH = f"{PODS}/"
# Long enough after a deploy for a missing pod to count as gone.
SETTLED = timedelta(seconds=DEPLOY_SETTLE_S)


@pytest.fixture
def ready(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest published."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    return runs_world


def _launch(world: RunsWorld, *, capacity: bool) -> str:
    """Launch on the seeded published batch; return the run id."""
    world.cloud.settings.capacity = capacity
    return launch_run(world).run_id


def _cancel(world: RunsWorld, run_id: str) -> CancelWorkerResponse:
    return ok_as(send(world, CANCEL, run_id=run_id, confirm=True), CancelWorkerResponse)


def _deploy_at_next_request(world: RunsWorld) -> None:
    """Let the run's queued pod deploy at Runpod's next request (here: the cancel's DELETE)."""
    world.cloud.api.queued_pods.schedule_deploy(queued_of(world)[0][0], world.now())


def _hide(records: dict[str, Record], request: httpx.Request) -> None:
    """Drop the pod a request names before it is answered (404), as if it were not visible yet."""
    path = request.url.path
    if path.startswith(POD_PATH):
        records.pop(path.removeprefix(POD_PATH), None)


def _hide_pods(world: RunsWorld) -> None:
    """Make every pod disappear just before a read or a DELETE of it is answered."""
    world.cloud.settings.on_request = partial(_hide, world.cloud.api.pods.records)


def test_cancel_while_queued(ready: RunsWorld) -> None:
    """The queued pod is cancelled and reads `CANCELLED`: the run is `cancelled`."""
    cancelled = _cancel(ready, _launch(ready, capacity=False))
    assert (cancelled.run.status, cancelled.terminated_pod_id, queued_of(ready)[0][1]) == (
        CANCELLED,
        None,
        QUEUED_POD_CANCELLED,
    )


def test_cancel_with_a_deploy_during_the_cancel(ready: RunsWorld) -> None:
    """The queued pod deploys while it is cancelled: a read shows its pod, which is terminated and verified at once."""
    run_id = _launch(ready, capacity=False)
    _deploy_at_next_request(ready)
    cancelled = _cancel(ready, run_id)
    status, payload = run_row(ready, run_id)
    assert (status, payload.pod_seen, cancelled.terminated_pod_id, pods_of(ready)) == (
        CANCELLED,
        True,
        payload.pod_id,
        [],
    )


def test_unseen_pod_missing_after_deploy_not_gone(ready: RunsWorld) -> None:
    """A pod known only from its queued pod that reads 404 right after the deploy may be lag: not cancelled.

    No read shows it and a DELETE finds nothing; re-sending the cancel once the deploy settled finishes it.
    """
    run_id = _launch(ready, capacity=False)
    _deploy_at_next_request(ready)
    _hide_pods(ready)
    refused = send(ready, CANCEL, run_id=run_id, confirm=True)
    status, payload = run_row(ready, run_id)
    assert (error_code(refused), status, payload.pod_id is not None, payload.pod_seen) == (
        ERROR_STOP_UNVERIFIED,
        "running",
        True,
        False,
    )
    ready.cloud.clock.advance(SETTLED)
    assert _cancel(ready, run_id).run.status == CANCELLED


def test_cancelled_only_after_verified_termination(ready: RunsWorld) -> None:
    """A deployed pod whose terminate does not take effect: the run follows the pod and stays active."""
    run_id = _launch(ready, capacity=False)
    _deploy_at_next_request(ready)
    ready.cloud.settings.terminate_effective = False
    refused = send(ready, CANCEL, run_id=run_id, confirm=True)
    status, payload = run_row(ready, run_id)
    assert (error_code(refused), status, [(payload.pod_id, "RUNNING")]) == (
        ERROR_STOP_UNVERIFIED,
        "running",
        pods_of(ready),
    )
    ready.cloud.settings.terminate_effective = True
    assert (_cancel(ready, run_id).run.status, pods_of(ready)) == (CANCELLED, [])


def test_cancel_while_running(ready: RunsWorld) -> None:
    """A started run's pod is terminated and verified; cancelling again answers the same."""
    run_id = _launch(ready, capacity=True)
    pod_id = pods_of(ready)[0][0]
    cancelled = _cancel(ready, run_id)
    assert (cancelled.run.status, cancelled.terminated_pod_id, pods_of(ready)) == (CANCELLED, pod_id, [])
    assert _cancel(ready, run_id).run.status == CANCELLED


def test_failed_termination_read_keeps_the_run(ready: RunsWorld) -> None:
    """A follow-up read that fails leaves the run `running` (not cancelled)."""
    run_id = _launch(ready, capacity=True)
    ready.cloud.api.fail("GET", f"{POD_PATH}{pods_of(ready)[0][0]}", *OUTAGE)
    assert (error_code(send(ready, CANCEL, run_id=run_id, confirm=True)), run_row(ready, run_id)[0]) == (
        ERROR_CLOUD_UNREACHABLE,
        "running",
    )


def test_cancel_refusals(ready: RunsWorld) -> None:
    """Unknown and finished runs are refused."""
    run_id = _launch(ready, capacity=True)
    assert error_code(send(ready, CANCEL, run_id=UNKNOWN_RUN, confirm=True)) == ERROR_RUN_NOT_FOUND
    set_run_status(ready, "finished", run_id)
    assert error_code(send(ready, CANCEL, run_id=run_id, confirm=True)) == ERROR_RUN_NOT_ACTIVE


def test_cancel_of_an_unresolved_launch(ready: RunsWorld) -> None:
    """A `launching` run whose pod is not listed cannot be stopped yet; it stays `launching`."""
    ready.cloud.api.fail(POST, PODS, Failure(status=httpx.codes.SERVICE_UNAVAILABLE))
    run_id = launch_run(ready).run_id
    assert (error_code(send(ready, CANCEL, run_id=run_id, confirm=True)), run_row(ready, run_id)[0]) == (
        ERROR_LAUNCH_UNRESOLVED,
        "launching",
    )


def _asked(world: RunsWorld, run_id: str) -> NeedsConfirmation:
    """Send `cancel_worker` without `confirm`; it must ask."""
    return ok_as(send(world, CANCEL, run_id=run_id), NeedsConfirmation)


def test_cancel_asks_first_and_changes_nothing(ready: RunsWorld) -> None:
    """Without `confirm` (Q34) the answer names the run and its stage; the run and its queued pod stay as they are.

    Confirmed, a waiting run is cancelled; nothing reaches Runpod before the confirmation.
    """
    world = ready
    run_id = _launch(world, capacity=False)
    before = (run_row(world, run_id), queued_of(world), len(world.cloud.api.seen()))
    asked = _asked(world, run_id).confirmations[0]
    after = (run_row(world, run_id), queued_of(world), len(world.cloud.api.seen()))
    shown = (asked.code, asked.field, asked.set_to, asked.details)
    assert shown == ("cancel_worker", "confirm", True, {"run_id": run_id, "stage": "waiting", "run_status": "queued"})
    assert after == before
    assert _cancel(world, run_id).run.status == CANCELLED


def test_stop_asks_with_the_stage_then_stops(ready: RunsWorld) -> None:
    """A started run is asked about at its stage and keeps its pod until confirmed; confirmed, it stops."""
    world = ready
    run_id = _launch(world, capacity=True)
    asked = _asked(world, run_id).confirmations[0]
    assert (asked.details["stage"], run_row(world, run_id)[0], len(pods_of(world))) == ("starting", "running", 1)
    stopped = _cancel(world, run_id)
    assert (stopped.run.status, pods_of(world)) == (CANCELLED, [])


def test_asking_about_an_ended_run(ready: RunsWorld) -> None:
    """Unasked, a cancelled run answers as done, another ended run and an unknown run are refused."""
    world = ready
    run_id = _launch(world, capacity=True)
    _cancel(world, run_id)
    again = ok_as(send(world, CANCEL, run_id=run_id), CancelWorkerResponse)
    assert again.run.status == CANCELLED
    set_run_status(world, "finished", run_id)
    assert error_code(send(world, CANCEL, run_id=run_id)) == ERROR_RUN_NOT_ACTIVE
    assert error_code(send(world, CANCEL, run_id=UNKNOWN_RUN)) == ERROR_RUN_NOT_FOUND
