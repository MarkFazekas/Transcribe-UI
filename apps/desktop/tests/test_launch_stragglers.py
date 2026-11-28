"""`terminate_pod` and `cancel_queued_pod` (plan m5 M5.11): confirmed, verified, app pods only."""

from datetime import timedelta

import pytest

from support.confirm import confirmed_document
from support.launch import (
    CANCEL_QUEUED,
    TERMINATE,
    document,
    error_code,
    launch_run,
    ok_as,
    pods_of,
    queued_of,
    send,
    set_up_cloud,
)
from support.runs import RUN_ID, RunsWorld, app_pod_name, seed_ready
from transcribe_ui.runpod.constants import QUEUED_POD_CANCELLED
from transcribe_ui.runs.constants import ERROR_NOT_APP_POD, ERROR_POD_NOT_FOUND
from transcribe_ui.runs.responses import (
    CancelQueuedPodResponse,
    ListRunsResponse,
    RefreshStatusResponse,
    TerminatePodResponse,
)

# A pod that is not one of the app's.
FOREIGN_POD = "somebody-elses-pod"

# Run status and end reason of a run the user stopped.
CANCELLED = "cancelled"
# Minutes between the refresh and the terminate of the run.
TERMINATED_AFTER_MIN = 50


def _cancel(world: RunsWorld, queued_id: str) -> CancelQueuedPodResponse:
    return ok_as(send(world, CANCEL_QUEUED, queued_pod_id=queued_id, confirm=True), CancelQueuedPodResponse)


def test_cancel_a_straggler_queued_pod(runs_world: RunsWorld) -> None:
    """`cancel_queued_pod` asks first, then cancels and verifies."""
    queued_id = str(runs_world.cloud.api.queued_pods.add(app_pod_name(RUN_ID, runs_world.now()))["id"])
    asked = send(runs_world, CANCEL_QUEUED, queued_pod_id=queued_id)
    assert (asked.status, queued_of(runs_world)[0][1]) == ("needs_confirmation", "QUEUED")
    resent = confirmed_document(document(CANCEL_QUEUED, queued_pod_id=queued_id), asked)
    stopped = ok_as(runs_world.leg.backend.execute_json(resent), CancelQueuedPodResponse)
    assert (stopped.outcome, stopped.local_status, queued_of(runs_world)[0][1]) == (
        "cancelled",
        None,
        QUEUED_POD_CANCELLED,
    )


@pytest.mark.parametrize("deploy", [False, True])
def test_cancel_the_queued_pod_of_a_local_run(runs_world: RunsWorld, *, deploy: bool) -> None:
    """The run's queued pod (deploying during the cancel or not) is stopped; the run becomes `cancelled`."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    runs_world.cloud.settings.capacity = False
    run = launch_run(runs_world)
    queued_id = str(run.queued_pod_id)
    if deploy:
        # It deploys at the next request; a read of its pod is a sighting, so the stop shows at once.
        runs_world.cloud.api.queued_pods.schedule_deploy(queued_id, runs_world.now())
    stopped = _cancel(runs_world, queued_id)
    expected_outcome = "pod_terminated" if deploy else "cancelled"
    assert (stopped.outcome, stopped.run_id, stopped.local_status) == (expected_outcome, run.run_id, "cancelled")
    assert (stopped.terminated_pod_id is not None, pods_of(runs_world)) == (deploy, [])


def test_stopping_refuses_foreign_and_unknown_pods(runs_world: RunsWorld) -> None:
    """Pods that are not the app's are left alone; unknown ids are reported."""
    foreign_id = str(runs_world.cloud.api.pods.add(FOREIGN_POD)["id"])
    refused = send(runs_world, TERMINATE, pod_id=foreign_id, confirm=True)
    assert (error_code(refused), pods_of(runs_world)) == (ERROR_NOT_APP_POD, [(foreign_id, "RUNNING")])
    assert error_code(send(runs_world, TERMINATE, pod_id="pod-unknown")) == ERROR_POD_NOT_FOUND
    assert error_code(send(runs_world, CANCEL_QUEUED, queued_pod_id="qpod-unknown")) == ERROR_POD_NOT_FOUND


def test_stray_pod_with_run_name_keeps_the_run(runs_world: RunsWorld) -> None:
    """Terminating a pod the run did not record leaves the run as it is."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    run = launch_run(runs_world)
    stray_id = str(runs_world.cloud.api.pods.add(run.pod_name)["id"])
    stopped = ok_as(send(runs_world, TERMINATE, pod_id=stray_id, confirm=True), TerminatePodResponse)
    assert (stopped.run_id, stopped.local_status, [pod_id for pod_id, _ in pods_of(runs_world)]) == (
        run.run_id,
        "running",
        [run.pod_id],
    )


def test_terminated_run_lists_up_to_its_end(runs_world: RunsWorld) -> None:
    """Refreshed, then terminated 50 minutes later: the run ends then, and its time and cost stop there."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    run = launch_run(runs_world)
    refreshed = ok_as(send(runs_world, "refresh_status"), RefreshStatusResponse).runs[0]
    assert refreshed.elapsed_s is not None
    runs_world.cloud.clock.advance(timedelta(minutes=TERMINATED_AFTER_MIN))
    stopped = ok_as(send(runs_world, TERMINATE, pod_id=str(run.pod_id), confirm=True), TerminatePodResponse)
    assert stopped.local_status == CANCELLED
    ended_at = runs_world.now()
    runs_world.cloud.clock.advance(timedelta(hours=1))
    listed = ok_as(send(runs_world, "list_runs"), ListRunsResponse).runs[0]
    assert (listed.stage, listed.ended_at, listed.end_reason, listed.stop_error) == (
        CANCELLED,
        ended_at,
        CANCELLED,
        None,
    )
    assert (listed.elapsed_until, listed.elapsed_s) == (ended_at, refreshed.elapsed_s + TERMINATED_AFTER_MIN * 60)
