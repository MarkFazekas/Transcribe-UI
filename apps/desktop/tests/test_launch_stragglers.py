"""`terminate_pod` and `cancel_queued_pod` (plan m5 M5.11): confirmed, verified, app pods only."""

from datetime import timedelta

import pytest

from support.launch import (
    CANCEL_QUEUED,
    TERMINATE,
    error_code,
    launch_run,
    ok_as,
    pods_of,
    queued_of,
    send,
    set_up_cloud,
)
from support.runs import RUN_ID, RunsWorld, app_pod_name, seed_uploaded
from transcribe_ui.runpod.constants import QUEUED_POD_CANCELLED
from transcribe_ui.runs.constants import DEPLOY_SETTLE_S, ERROR_NOT_APP_POD, ERROR_POD_NOT_FOUND, ERROR_STOP_UNVERIFIED
from transcribe_ui.runs.responses import CancelQueuedPodResponse, TerminatePodResponse

# A pod that is not one of the app's.
FOREIGN_POD = "somebody-elses-pod"


def _cancel(world: RunsWorld, queued_id: str) -> CancelQueuedPodResponse:
    return ok_as(send(world, CANCEL_QUEUED, queued_pod_id=queued_id, confirm=True), CancelQueuedPodResponse)


def test_cancel_a_straggler_queued_pod(runs_world: RunsWorld) -> None:
    """`cancel_queued_pod` asks first, then cancels and verifies."""
    queued_id = str(runs_world.cloud.api.queued_pods.add(app_pod_name(RUN_ID, runs_world.now()))["id"])
    asked = send(runs_world, CANCEL_QUEUED, queued_pod_id=queued_id)
    assert (asked.status, queued_of(runs_world)[0][1]) == ("needs_confirmation", "QUEUED")
    stopped = _cancel(runs_world, queued_id)
    assert (stopped.outcome, stopped.local_status, queued_of(runs_world)[0][1]) == (
        "cancelled",
        None,
        QUEUED_POD_CANCELLED,
    )


@pytest.mark.parametrize("deploy", [False, True])
def test_cancel_the_queued_pod_of_a_local_run(runs_world: RunsWorld, *, deploy: bool) -> None:
    """The run's queued pod (deploying during the cancel or not) is stopped; the run becomes `cancelled`."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    runs_world.cloud.settings.capacity = False
    run = launch_run(runs_world)
    queued_id = str(run.queued_pod_id)
    if deploy:
        runs_world.cloud.api.queued_pods.schedule_deploy(queued_id, runs_world.now())
        # Its pod, never seen, counts as gone only once the deploy has settled: re-send then.
        first = send(runs_world, CANCEL_QUEUED, queued_pod_id=queued_id, confirm=True)
        assert error_code(first) == ERROR_STOP_UNVERIFIED
        runs_world.cloud.clock.advance(timedelta(seconds=DEPLOY_SETTLE_S))
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
    seed_uploaded(runs_world, held=True)
    run = launch_run(runs_world)
    stray_id = str(runs_world.cloud.api.pods.add(run.pod_name)["id"])
    stopped = ok_as(send(runs_world, TERMINATE, pod_id=stray_id, confirm=True), TerminatePodResponse)
    assert (stopped.run_id, stopped.local_status, [pod_id for pod_id, _ in pods_of(runs_world)]) == (
        run.run_id,
        "running",
        [run.pod_id],
    )
