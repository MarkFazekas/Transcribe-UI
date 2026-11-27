"""How the user's stops end a run (plan m6 M6.1 review): never final while its pod may bill.

`cancel_queued_pod` and `cancel_worker` terminate a pod the queued pod deployed even when the
queued pod is gone (404) after the cancel; a stop that stopped nothing leaves the run to
`refresh_status`; a worker that reported `finished` before the stop ends its run as finished;
every user stop records the end (time and reason) and clears an earlier stop error; a deadline
stop whose Runpod call fails is recorded on the run.
"""

from collections.abc import Callable
from datetime import timedelta
from functools import partial

import httpx
import pytest

from support.launch import (
    CANCEL,
    CANCEL_QUEUED,
    TERMINATE,
    error_code,
    launch_run,
    ok_as,
    pods_of,
    run_row,
    send,
    set_up_cloud,
)
from support.monitor import LIST_RUNS, refreshed_run, write_status
from support.runpod_api import Failure, QueuedPods, Record
from support.runs import RunsWorld, seed_uploaded
from transcribe_ui.runpod.constants import POD_TERMINATED, QUEUED_POD_EXPIRED
from transcribe_ui.runs.constants import ERROR_STOP_UNVERIFIED
from transcribe_ui.runs.responses import (
    CancelQueuedPodResponse,
    CancelWorkerResponse,
    ListRunsResponse,
    RunProgress,
    TerminatePodResponse,
)

# Run status of a run that is followed.
RUNNING = "running"
# Run status and end reason of a run the user stopped.
CANCELLED = "cancelled"
# The fake's delete of a queued pod.
type Delete = Callable[[str], httpx.Response]
# How long a new pod stays out of the pod list in the list-lag tests.
LIST_LAG = timedelta(minutes=5)
# A Runpod answer that refuses a request without retries.
BAD_REQUEST = 400


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held (the launch publishes it)."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    return runs_world


def _queued_run(world: RunsWorld, *, deploy: bool = True) -> tuple[str, str]:
    """Launch with no capacity; let the queued pod deploy at the next request; return the run and queued-pod ids."""
    world.cloud.settings.capacity = False
    run = launch_run(world)
    assert run.queued_pod_id is not None
    if deploy:
        world.cloud.api.queued_pods.schedule_deploy(run.queued_pod_id, world.now())
    return run.run_id, run.queued_pod_id


def _unlisted_from_now(world: RunsWorld) -> None:
    """Keep pods created from now on out of `GET /v2/pods` for a while (list lag after a deploy)."""
    world.cloud.settings.list_lag = LIST_LAG


def _delete_and_forget(queued: QueuedPods, delete: Delete, object_id: str) -> httpx.Response:
    response = delete(object_id)
    queued.records.pop(object_id, None)
    return response


def _forget_queued_on_cancel(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make Runpod forget a queued pod once it is cancelled: the read after the cancel is a 404."""
    queued = world.cloud.api.queued_pods
    monkeypatch.setattr(queued, "delete", partial(_delete_and_forget, queued, queued.delete))


def _pod_records(world: RunsWorld) -> dict[str, Record]:
    api = world.cloud.api
    return api.pods.records


def _queued_record(world: RunsWorld, queued_id: str) -> Record:
    queued = world.cloud.api.queued_pods
    return queued.records[queued_id]


def _listed(world: RunsWorld) -> RunProgress:
    runs = ok_as(send(world, LIST_RUNS), ListRunsResponse).runs
    assert len(runs) == 1
    return runs[0]


def _pod_status(world: RunsWorld, pod_id: str) -> str | None:
    record = _pod_records(world).get(pod_id)
    return None if record is None else str(record.body["status"])


def test_queued_cancel_stops_its_pod(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """The queued pod reads deployed, then 404 after the cancel: its pod is terminated before the run ends."""
    run_id, queued_id = _queued_run(world)
    world.cloud.settings.keep_terminal = True
    # Not listed: only the pod the first read of the queued pod names can be found.
    _unlisted_from_now(world)
    _forget_queued_on_cancel(world, monkeypatch)
    stopped = ok_as(send(world, CANCEL_QUEUED, queued_pod_id=queued_id, confirm=True), CancelQueuedPodResponse)
    pod_id = stopped.terminated_pod_id
    assert pod_id is not None
    assert (stopped.outcome, stopped.local_status, _pod_status(world, pod_id)) == (
        "pod_terminated",
        CANCELLED,
        POD_TERMINATED,
    )
    status, payload = run_row(world, run_id)
    assert (status, payload.pod_id, payload.end_reason, payload.ended_at) == (CANCELLED, pod_id, CANCELLED, world.now())


def test_cancel_worker_finds_the_deployed_pod_by_name(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """The queued pod deploys during the cancel and is then gone: its pod, listed under the run's name, is stopped."""
    run_id, _queued_id = _queued_run(world)
    _forget_queued_on_cancel(world, monkeypatch)
    cancelled = ok_as(send(world, CANCEL, run_id=run_id), CancelWorkerResponse)
    assert cancelled.terminated_pod_id is not None
    assert (cancelled.run.status, pods_of(world)) == (CANCELLED, [])
    _status, payload = run_row(world, run_id)
    assert (payload.pod_id, payload.end_reason, payload.ended_at) == (
        cancelled.terminated_pod_id,
        CANCELLED,
        world.now(),
    )


def test_ended_pod_left_to_refresh(world: RunsWorld) -> None:
    """The pod had ended after the worker finished: the terminate stops nothing, the refresh ends the run finished."""
    world.cloud.settings.keep_terminal = True
    run = launch_run(world)
    pod_id = str(run.pod_id)
    write_status(world.store, run.run_id, "finished", world.now(), done_jobs=2)
    _pod_records(world)[pod_id].body["status"] = POD_TERMINATED
    stopped = ok_as(send(world, TERMINATE, pod_id=pod_id, confirm=True), TerminatePodResponse)
    assert stopped.local_status == RUNNING
    ended = refreshed_run(world, run.run_id)
    assert (ended.run.status, ended.end_reason) == ("finished", "worker_finished")


def test_cancel_after_finish_is_finished(world: RunsWorld) -> None:
    """A worker that reported `finished` before the cancel did its work: the run ends finished, not cancelled."""
    run = launch_run(world)
    write_status(world.store, run.run_id, "finished", world.now(), done_jobs=2)
    stopped = ok_as(send(world, CANCEL, run_id=run.run_id), CancelWorkerResponse)
    assert (stopped.run.status, _listed(world).end_reason) == ("finished", "worker_finished")


def test_cancel_clears_the_stop_error(world: RunsWorld) -> None:
    """A deadline stop that did not show leaves its error; a cancel that is verified ends the run without it."""
    run_id = launch_run(world).run_id
    deadline = refreshed_run(world, run_id).deadline
    world.cloud.settings.terminate_effective = False
    world.cloud.clock.moment = deadline
    assert refreshed_run(world, run_id).stop_error == ERROR_STOP_UNVERIFIED
    world.cloud.settings.terminate_effective = True
    world.cloud.clock.advance(timedelta(minutes=1))
    ok_as(send(world, CANCEL, run_id=run_id), CancelWorkerResponse)
    listed = _listed(world)
    assert (listed.stage, listed.end_reason, listed.ended_at, listed.stop_error) == (
        CANCELLED,
        CANCELLED,
        world.now(),
        None,
    )


def test_failed_deadline_stop_is_recorded(world: RunsWorld) -> None:
    """A deadline stop whose Runpod call fails fails the refresh, and the run keeps that error and the refresh time."""
    run = launch_run(world)
    deadline = refreshed_run(world, run.run_id).deadline
    world.cloud.clock.moment = deadline
    world.cloud.api.fail("DELETE", f"/v2/pods/{run.pod_id}", Failure(status=BAD_REQUEST))
    failed = send(world, "refresh_status")
    assert failed.status == "error"
    listed = _listed(world)
    assert (listed.run.status, listed.stop_error, listed.refreshed_at) == (RUNNING, failed.error.code, deadline)


def test_vanished_queued_pod_with_unlisted_pod(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gone after the cancel, its new pod not listed yet: not cancelled until the pod shows, then it is stopped."""
    run_id, _queued_id = _queued_run(world)
    _unlisted_from_now(world)
    _forget_queued_on_cancel(world, monkeypatch)
    refused = send(world, CANCEL, run_id=run_id)
    assert (error_code(refused), run_row(world, run_id)[0]) == (ERROR_STOP_UNVERIFIED, "queued")
    world.cloud.clock.advance(LIST_LAG)
    cancelled = ok_as(send(world, CANCEL, run_id=run_id), CancelWorkerResponse)
    assert (cancelled.run.status, cancelled.terminated_pod_id is not None, pods_of(world)) == (CANCELLED, True, [])


def test_sighting_during_a_queued_cancel_is_kept(world: RunsWorld) -> None:
    """A read shows the deployed pod but its terminate does not take: the run follows it as seen; a retry stops it."""
    run_id, queued_id = _queued_run(world)
    world.cloud.settings.terminate_effective = False
    refused = send(world, CANCEL_QUEUED, queued_pod_id=queued_id, confirm=True)
    status, payload = run_row(world, run_id)
    assert (error_code(refused), status, payload.pod_id is not None, payload.pod_seen) == (
        ERROR_STOP_UNVERIFIED,
        RUNNING,
        True,
        True,
    )
    world.cloud.settings.terminate_effective = True
    stopped = ok_as(send(world, CANCEL_QUEUED, queued_pod_id=queued_id, confirm=True), CancelQueuedPodResponse)
    assert (stopped.local_status, stopped.terminated_pod_id) == (CANCELLED, payload.pod_id)


def test_cancel_of_an_ended_pod_leaves_the_run(world: RunsWorld) -> None:
    """The queued pod deployed a pod that is terminated already: nothing live is stopped, the refresh ends the run."""
    run_id, queued_id = _queued_run(world)
    world.cloud.settings.keep_terminal = True
    world.cloud.api.queued_pods.advance()
    deployed = _queued_record(world, queued_id).body["podId"]
    _pod_records(world)[str(deployed)].body["status"] = POD_TERMINATED
    stopped = ok_as(send(world, CANCEL_QUEUED, queued_pod_id=queued_id, confirm=True), CancelQueuedPodResponse)
    assert stopped.local_status == RUNNING
    ended = refreshed_run(world, run_id)
    assert (ended.run.status, ended.end_reason) == ("failed", "pod_gone")


def test_cancel_of_an_ended_queue_leaves_the_run(world: RunsWorld) -> None:
    """The queued pod had expired without a pod: the cancel stops nothing, the run is left to the refresh."""
    run_id, queued_id = _queued_run(world, deploy=False)
    _queued_record(world, queued_id).body["status"] = QUEUED_POD_EXPIRED
    stopped = ok_as(send(world, CANCEL_QUEUED, queued_pod_id=queued_id, confirm=True), CancelQueuedPodResponse)
    assert (stopped.outcome, stopped.local_status) == (CANCELLED, "queued")
    ended = refreshed_run(world, run_id)
    assert (ended.run.status, ended.end_reason) == ("failed", "queue_ended")


def test_terminating_the_deployed_pod_ends_the_run(world: RunsWorld) -> None:
    """The run knows only its queued pod, which deployed the pod `terminate_pod` stops: the run records it and ends."""
    run_id, _queued_id = _queued_run(world)
    world.cloud.api.queued_pods.advance()
    pod_id = pods_of(world)[0][0]
    stopped = ok_as(send(world, TERMINATE, pod_id=pod_id, confirm=True), TerminatePodResponse)
    status, payload = run_row(world, run_id)
    assert (stopped.local_status, status, payload.pod_id, payload.pod_seen) == (CANCELLED, CANCELLED, pod_id, True)


def test_stop_missing_the_recorded_pod_keeps_the_run(world: RunsWorld) -> None:
    """A stray queued pod with the run's name is cancelled: the run's own pod still bills, so the run stays followed."""
    run = launch_run(world)
    stray_id = str(world.cloud.api.queued_pods.add(run.pod_name)["id"])
    stopped = ok_as(send(world, CANCEL_QUEUED, queued_pod_id=stray_id, confirm=True), CancelQueuedPodResponse)
    assert (stopped.outcome, stopped.local_status, run_row(world, run.run_id)[0]) == (CANCELLED, RUNNING, RUNNING)
