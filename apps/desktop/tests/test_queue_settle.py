"""The settle rule of a queued pod gone (404) after its cancel, and stops of several pods (plan m6 M6.1 review).

A cancel counts as done for a vanished queued pod only `DEPLOY_SETTLE_S` after the earlier of
its queue's `expiresAt` and the cancel's acknowledgement, and after two empty lookups at least
`LAUNCH_LOOKUP_GAP_S` apart (`stopping.vanish_verdict`, as `refresh_status` judges it). A queued
pod an earlier read showed ended needs none of this; without an `expiresAt` the pod name's
deadline bounds it. Every pod under the run's name is stopped even when one stop fails, and the
run follows the pod whose stop did not show.
"""

from datetime import datetime, timedelta

import pytest

from support.launch import CANCEL, CANCEL_QUEUED, error_code, ok_as, pods_of, run_row, send, set_up_cloud
from support.runpod_api import Failure, Json
from support.runs import RUN_ID, RunsWorld, seed_uploaded
from support.stops import forget_queued_on_cancel, queued_run
from transcribe_contracts.pods import pod_name
from transcribe_ui.runpod.constants import QUEUED_POD_DEPLOYED, QUEUED_POD_EXPIRED
from transcribe_ui.runs.constants import DEPLOY_SETTLE_S, ERROR_STOP_UNVERIFIED, LAUNCH_LOOKUP_GAP_S
from transcribe_ui.runs.payloads import RunPayload
from transcribe_ui.runs.responses import CancelQueuedPodResponse, CancelWorkerResponse
from transcribe_ui.state.models import RunRow

# Run status and end reason of a run the user stopped.
CANCELLED = "cancelled"
# A second, and a minute (a pod name's deadline is rounded up to the minute).
SECOND = timedelta(seconds=1)
MINUTE = timedelta(minutes=1)
# The settle time after the anchor, and the gap between the two empty lookups.
SETTLE = timedelta(seconds=DEPLOY_SETTLE_S)
GAP = timedelta(seconds=LAUNCH_LOOKUP_GAP_S)
# A Runpod answer that refuses a request without retries, and one that reads "not found".
BAD_REQUEST = 400
NOT_FOUND = 404
# Path of one pod in the fake API.
POD_PATH = "/v2/pods/"


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held (the launch publishes it)."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    return runs_world


def _cancel_at(world: RunsWorld, run_id: str, moment: datetime) -> str:
    """Send `cancel_worker` at that moment; return the run's status after it (or the error code)."""
    world.cloud.clock.moment = moment
    response = send(world, CANCEL, run_id=run_id)
    if response.status == "error":
        assert error_code(response) == ERROR_STOP_UNVERIFIED
        return ERROR_STOP_UNVERIFIED
    return ok_as(response, CancelWorkerResponse).run.status


def test_vanished_queue_waits_for_the_settle(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """No deploy, no pod listed: refused until the settle time after the acknowledgement, then a first empty lookup."""
    run_id, _queued_id = queued_run(world, deploy=False)
    forget_queued_on_cancel(world, monkeypatch)
    acknowledged = world.now()
    assert _cancel_at(world, run_id, acknowledged) == ERROR_STOP_UNVERIFIED
    assert _payload(world, run_id).cancel_acknowledged_at == acknowledged
    settled = acknowledged + SETTLE
    early = (_cancel_at(world, run_id, settled - SECOND), _payload(world, run_id).first_empty_lookup_at)
    at_settle = (_cancel_at(world, run_id, settled), _payload(world, run_id).first_empty_lookup_at)
    assert (early, at_settle) == ((ERROR_STOP_UNVERIFIED, None), (ERROR_STOP_UNVERIFIED, settled))


def test_vanished_queue_needs_two_lookups(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """After the first empty lookup at the settle time, the second counts only a lookup gap later."""
    run_id, _queued_id = queued_run(world, deploy=False)
    forget_queued_on_cancel(world, monkeypatch)
    settled = world.now() + SETTLE
    assert _cancel_at(world, run_id, world.now()) == ERROR_STOP_UNVERIFIED
    assert _cancel_at(world, run_id, settled) == ERROR_STOP_UNVERIFIED
    assert _cancel_at(world, run_id, settled + GAP - SECOND) == ERROR_STOP_UNVERIFIED
    assert _cancel_at(world, run_id, settled + GAP) == CANCELLED


def test_queue_read_ended_counts_at_once(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first read shows the queued pod expired without a pod: its 404 after the cancel needs no settling."""
    run_id, queued_id = queued_run(world, deploy=False)
    _queued_body(world, queued_id)["status"] = QUEUED_POD_EXPIRED
    forget_queued_on_cancel(world, monkeypatch)
    stopped = ok_as(send(world, CANCEL_QUEUED, queued_pod_id=queued_id, confirm=True), CancelQueuedPodResponse)
    assert (stopped.outcome, stopped.local_status, run_row(world, run_id)[0]) == (CANCELLED, "queued", "queued")


@pytest.mark.parametrize(("deadline_offset", "cancelled"), [(-SETTLE - MINUTE, True), (SETTLE, False)])
def test_name_deadline_bounds_a_queue_without_expiry(
    world: RunsWorld,
    monkeypatch: pytest.MonkeyPatch,
    deadline_offset: timedelta,
    *,
    cancelled: bool,
) -> None:
    """A queued pod read without `expiresAt` and no local run: the deadline in its name stands in for it."""
    name = pod_name(RUN_ID, world.now() + deadline_offset)
    queued_id = str(world.cloud.api.queued_pods.add(name)["id"])
    body = _queued_body(world, queued_id)
    body.pop("queue")
    body["status"] = QUEUED_POD_DEPLOYED
    forget_queued_on_cancel(world, monkeypatch)
    response = send(world, CANCEL_QUEUED, queued_pod_id=queued_id, confirm=True)
    if cancelled:
        assert ok_as(response, CancelQueuedPodResponse).outcome == CANCELLED
    else:
        assert error_code(response) == ERROR_STOP_UNVERIFIED


@pytest.mark.parametrize("failing", [0, 1])
def test_every_listed_pod_is_stopped(world: RunsWorld, monkeypatch: pytest.MonkeyPatch, failing: int) -> None:
    """Two live pods under the run's name and one stop fails: the other is stopped, the run follows the failed one."""
    run_id, _queued_id = queued_run(world, deploy=False)
    forget_queued_on_cancel(world, monkeypatch)
    run_name = _run_pod_name(world, run_id)
    pod_ids = [str(world.cloud.api.pods.add(run_name)["id"]) for _ in range(2)]
    failed_id = pod_ids[failing]
    world.cloud.api.fail("DELETE", f"{POD_PATH}{failed_id}", Failure(status=BAD_REQUEST))
    assert send(world, CANCEL, run_id=run_id).status == "error"
    status, payload = run_row(world, run_id)
    assert (status, payload.pod_id, [pod_id for pod_id, _status in pods_of(world)]) == (
        "running",
        failed_id,
        [failed_id],
    )
    retried = ok_as(send(world, CANCEL, run_id=run_id), CancelWorkerResponse)
    assert (retried.run.status, pods_of(world)) == (CANCELLED, [])


def test_delete_answer_is_no_sighting(world: RunsWorld) -> None:
    """A pod known only from its queued pod that reads 404 while its DELETE answers 204: not seen, not verified."""
    run_id, queued_id = queued_run(world)
    world.cloud.api.queued_pods.advance()
    pod_id = str(_queued_body(world, queued_id)["podId"])
    hidden = Failure(status=NOT_FOUND)
    world.cloud.api.fail("GET", f"{POD_PATH}{pod_id}", hidden, hidden)
    refused = send(world, CANCEL, run_id=run_id)
    status, payload = run_row(world, run_id)
    assert (error_code(refused), status, payload.pod_id, payload.pod_seen) == (
        ERROR_STOP_UNVERIFIED,
        "running",
        pod_id,
        False,
    )


def _run_pod_name(world: RunsWorld, run_id: str) -> str:
    with world.ports.state.session() as session:
        row = session.get(RunRow, run_id)
        assert row is not None
        return row.pod_name


def _payload(world: RunsWorld, run_id: str) -> RunPayload:
    return run_row(world, run_id)[1]


def _queued_body(world: RunsWorld, queued_id: str) -> Json:
    queued = world.cloud.api.queued_pods
    return queued.records[queued_id].body
