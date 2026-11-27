"""Stopping a run's pod or queued pod, verified (used by `cancel_worker` and the desktop deadline).

A run with a pod id terminates that pod (`KnownPod`: a pod known only from its queued pod is not
seen, so a 404 counts as gone only `DEPLOY_SETTLE_S` after the deploy, and a 204 on its delete
is no sighting). A run with only a queued-pod id cancels it; a pod it deployed meanwhile is
recorded (the run follows it as `running`) and terminated. A pod read that shows the pod is
recorded as a sighting, so a later 404 counts.
"""

from functools import partial

from transcribe_ui.api.ports import Clock
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.errors import LaunchUnresolvedError
from transcribe_ui.runs.records import read_payload, update_run
from transcribe_ui.runs.stopping import KnownPod, cancel_queued_verified, terminate_verified
from transcribe_ui.state.store import LocalState


def stop_run(state: LocalState, pods: PodsApi, run_id: str, clock: Clock) -> str | None:
    """Stop the run's pod (or queued pod), verified; return the id of the pod terminated, if any.

    The run's status is left to the caller (a pod learned on the way makes it `running`).

    Raises:
        LaunchUnresolvedError: The run knows neither a pod nor a queued pod.
        StopUnverifiedError: The stop was sent but does not show yet.
        DomainError: A Runpod call failed.
    """
    payload = read_payload(state, run_id)
    seen = partial(_mark_seen, state, run_id, clock)
    if payload.pod_id is not None:
        pod = KnownPod(payload.pod_id, seen=payload.pod_seen, deployed_at=payload.pod_deployed_at)
        terminate_verified(pods, pod, clock, seen)
        return pod.pod_id
    if payload.queued_pod_id is None:
        raise LaunchUnresolvedError(run_id)  # not final, yet neither id is known
    follow = partial(_follow_pod, state, run_id, clock)
    return cancel_queued_verified(pods, payload.queued_pod_id, clock, follow, seen).pod_id


def _follow_pod(state: LocalState, run_id: str, clock: Clock, pod: KnownPod) -> None:
    """Record the pod a queued pod deployed during the stop; the run follows it until it is terminated."""
    changes = {"pod_id": pod.pod_id, "pod_seen": pod.seen, "pod_deployed_at": pod.deployed_at}
    update_run(state, run_id, clock.now(), "running", changes)


def _mark_seen(state: LocalState, run_id: str, clock: Clock, _pod_id: str) -> None:
    """Record that a pod read showed the run's pod: from now on a 404 for it means gone."""
    update_run(state, run_id, clock.now(), changes={"pod_seen": True})
