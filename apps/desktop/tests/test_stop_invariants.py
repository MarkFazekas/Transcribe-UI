"""A run never becomes final while a pod carrying its name may still bill (plan m6 M6.1 review).

Stops that fail for several pods, a cancel between two pods' stops, and a run that follows a pod
another stop does not handle: every path leaves the run open, following a live pod, until each
pod under its name is verified stopped or gone.
"""

from datetime import timedelta
from functools import partial

import httpx
import pytest

from support.launch import (
    CANCEL,
    CANCEL_QUEUED,
    TERMINATE,
    document,
    error_code,
    launch_run,
    ok_as,
    pods_of,
    run_row,
    send,
    set_up_cloud,
)
from support.monitor import refreshed_run
from support.runpod_api import Failure
from support.runs import RunsWorld, seed_uploaded
from support.stops import forget_queued_on_cancel, queued_run
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.runs.constants import ERROR_STOP_UNVERIFIED
from transcribe_ui.runs.responses import CancelQueuedPodResponse, CancelWorkerResponse, TerminatePodResponse
from transcribe_ui.state.models import RunRow

# Run status of a run that is followed, and of a run the user stopped.
RUNNING = "running"
CANCELLED = "cancelled"
# A Runpod answer that refuses a request without retries.
BAD_REQUEST = 400
# Path of one pod in the fake API.
POD_PATH = "/v2/pods/"


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held (the launch publishes it)."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    return runs_world


def _run_pod_name(world: RunsWorld, run_id: str) -> str:
    with world.ports.state.session() as session:
        row = session.get(RunRow, run_id)
        assert row is not None
        return row.pod_name


def _two_pods(world: RunsWorld, run_id: str) -> list[str]:
    """Add two live pods carrying the run's name; return their ids."""
    name = _run_pod_name(world, run_id)
    return [_add_pod(world, name) for _ in range(2)]


def _refuse_delete(world: RunsWorld, pod_id: str) -> None:
    """Refuse the pod's next DELETE."""
    world.cloud.api.fail("DELETE", f"{POD_PATH}{pod_id}", Failure(status=BAD_REQUEST))


def _live(world: RunsWorld) -> list[str]:
    return [pod_id for pod_id, _status in pods_of(world)]


def test_two_failed_stops_keep_the_run_open(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither pod's stop shows: the run follows one; a retry stops it and follows the other, refused."""
    run_id, _queued_id = queued_run(world, deploy=False)
    forget_queued_on_cancel(world, monkeypatch)
    both = _two_pods(world, run_id)
    world.cloud.settings.terminate_effective = False
    assert send(world, CANCEL, run_id=run_id).status == "error"
    world.cloud.settings.terminate_effective = True
    status, payload = run_row(world, run_id)
    followed = payload.pod_id
    assert (status, followed in both, sorted(_live(world))) == (RUNNING, True, sorted(both))
    other = next(candidate for candidate in both if candidate != followed)
    _refuse_delete(world, other)
    assert send(world, CANCEL, run_id=run_id).status == "error"
    status, payload = run_row(world, run_id)
    assert (status, payload.pod_id, _live(world)) == (RUNNING, other, [other])
    retried = ok_as(send(world, CANCEL, run_id=run_id), CancelWorkerResponse)
    assert (retried.run.status, _live(world)) == (CANCELLED, [])


def test_cancel_between_two_stops(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancelled after one pod's DELETE: the other is not tried; the refresh follows it, a retry stops it."""
    run_id, _queued_id = queued_run(world, deploy=False)
    forget_queued_on_cancel(world, monkeypatch)
    both = _two_pods(world, run_id)
    untried = _cancel_during_the_stops(world, run_id, both)
    followed = refreshed_run(world, run_id)
    assert (followed.run.status, followed.run.pod_id) == (RUNNING, untried)
    retried = ok_as(send(world, CANCEL, run_id=run_id), CancelWorkerResponse)
    assert (retried.run.status, _live(world)) == (CANCELLED, [])


def _cancel_during_the_stops(world: RunsWorld, run_id: str, both: list[str]) -> str:
    """Send `cancel_worker`, cancelled at the first pod DELETE; return the pod that was not tried."""
    cancel = CancelFlag()
    world.cloud.settings.on_request = partial(_cancel_on_pod_delete, cancel)
    interrupted = world.leg.backend.execute_json(document(CANCEL, run_id=run_id), cancel=cancel)
    world.cloud.settings.on_request = None
    api = world.cloud.api
    deletes = {pod_id: api.count("DELETE", f"{POD_PATH}{pod_id}") for pod_id in both}
    assert (error_code(interrupted), sorted(deletes.values())) == (ERROR_CANCELLED, [0, 1])
    return next(pod_id for pod_id, count in deletes.items() if count == 0)


def _cancel_on_pod_delete(cancel: CancelFlag, request: httpx.Request) -> None:
    if request.method == "DELETE" and request.url.path.startswith(POD_PATH):
        cancel.cancel()


def test_stop_never_replaces_the_runs_pod(world: RunsWorld) -> None:
    """The run follows P2 (adopted by name); cancelling its queued pod stops the pod it names, P1: the run stays open."""
    world.cloud.settings.capacity = False
    run_id, queued_id = queued_run(world, deploy=False)
    adopted = _add_pod(world, _run_pod_name(world, run_id))
    assert refreshed_run(world, run_id).run.pod_id == adopted
    world.cloud.api.queued_pods.schedule_deploy(queued_id, world.now())
    world.cloud.clock.advance(timedelta(seconds=1))
    stopped = ok_as(send(world, CANCEL_QUEUED, queued_pod_id=queued_id, confirm=True), CancelQueuedPodResponse)
    status, payload = run_row(world, run_id)
    assert stopped.terminated_pod_id not in {None, adopted}
    assert (stopped.local_status, status, payload.pod_id, _live(world)) == (RUNNING, RUNNING, adopted, [adopted])


def _adopted_with_waiting_queue(world: RunsWorld) -> tuple[str, str, str]:
    """A queued run that adopted a pod listed under its name while its queued pod still waits."""
    run_id, queued_id = queued_run(world, deploy=False)
    adopted = _add_pod(world, _run_pod_name(world, run_id))
    assert refreshed_run(world, run_id).run.pod_id == adopted
    return run_id, queued_id, adopted


def _queued_status(world: RunsWorld, queued_id: str) -> str:
    queued = world.cloud.api.queued_pods
    return str(queued.records[queued_id].body["status"])


def test_cancel_also_stops_the_waiting_queue(world: RunsWorld) -> None:
    """The run follows its adopted pod while its queued pod waits: the cancel stops both before the run ends."""
    run_id, queued_id, _adopted = _adopted_with_waiting_queue(world)
    cancelled = ok_as(send(world, CANCEL, run_id=run_id), CancelWorkerResponse)
    assert (cancelled.run.status, _live(world), _queued_status(world, queued_id)) == (CANCELLED, [], "CANCELLED")


def test_queue_still_waiting_keeps_the_run_open(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """The queued pod's cancel does not take: the cancel is not verified and the run stays open."""
    run_id, _queued_id, _adopted = _adopted_with_waiting_queue(world)
    monkeypatch.setattr(world.cloud.api.queued_pods, "delete", _ignored_delete)
    refused = send(world, CANCEL, run_id=run_id)
    assert (error_code(refused), run_row(world, run_id)[0]) == (ERROR_STOP_UNVERIFIED, RUNNING)


def test_refresh_waits_for_the_queue(world: RunsWorld) -> None:
    """The adopted pod is gone while the queued pod still waits: the refresh keeps the run open."""
    run_id, _queued_id, adopted = _adopted_with_waiting_queue(world)
    pods = world.cloud.api.pods
    pods.records.pop(adopted)
    world.cloud.clock.advance(timedelta(minutes=1))
    assert refreshed_run(world, run_id).run.status == RUNNING


def test_deadline_stop_follows_a_named_pod_that_stays(world: RunsWorld) -> None:
    """At the deadline the run's pod stops but another under its name refuses: stop error kept, run not failed."""
    run = launch_run(world)
    other = _add_pod(world, run.pod_name)
    deadline = refreshed_run(world, run.run_id).deadline
    world.cloud.clock.moment = deadline
    _refuse_delete(world, other)
    assert send(world, "refresh_status").status == "error"
    status, payload = run_row(world, run.run_id)
    assert (status, payload.pod_id, payload.stop_error is not None) == (RUNNING, other, True)


def test_terminating_the_runs_pod_follows_the_other(world: RunsWorld) -> None:
    """`terminate_pod` on the run's pod while another pod carries its name: the run follows that one."""
    run = launch_run(world)
    other = _add_pod(world, run.pod_name)
    stopped = ok_as(send(world, TERMINATE, pod_id=str(run.pod_id), confirm=True), TerminatePodResponse)
    status, payload = run_row(world, run.run_id)
    assert (stopped.local_status, status, payload.pod_id) == (RUNNING, RUNNING, other)


def test_terminating_a_stray_keeps_the_followed_pod(world: RunsWorld) -> None:
    """`terminate_pod` on a pod under the run's name the run does not follow: the run keeps its own pod."""
    run = launch_run(world)
    stray = _add_pod(world, run.pod_name)
    world.cloud.api.pods.add(run.pod_name)
    ok_as(send(world, TERMINATE, pod_id=stray, confirm=True), TerminatePodResponse)
    assert run_row(world, run.run_id)[1].pod_id == run.pod_id


def _ignored_delete(_object_id: str) -> httpx.Response:
    """A queued-pod DELETE answered 204 that does not take effect."""
    return httpx.Response(httpx.codes.NO_CONTENT)


def _add_pod(world: RunsWorld, name: str) -> str:
    """Add a live pod with this name to the fake Runpod; return its id."""
    pods = world.cloud.api.pods
    return str(pods.add(name)["id"])
