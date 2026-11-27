"""Stopping a run's pod or queued pod, verified (used by `cancel_worker` and the desktop deadline).

A run with a pod id terminates that pod (`KnownPod`: a pod known only from its queued pod is not
seen, so a 404 counts as gone only `DEPLOY_SETTLE_S` after the deploy, and a 204 on its delete
is no sighting). A run with only a queued-pod id cancels it; a pod it deployed meanwhile (named
by the queued pod, or, when the queued pod is gone after the cancel, listed under the run's pod
name) is recorded (the run follows it as `running`) and terminated. A pod read that shows the
pod is recorded as a sighting, so a later 404 counts. The cancel's acknowledgement and the first
empty lookup of a vanished queued pod are recorded on the run (the settle rule needs both).
"""

from datetime import datetime
from functools import partial

from transcribe_ui.api.ports import Clock
from transcribe_ui.runpod.constants import QUEUED_POD_QUEUED
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.errors import LaunchUnresolvedError
from transcribe_ui.runs.records import read_payload, read_run, update_run
from transcribe_ui.runs.stopping import (
    KnownPod,
    OnDeployed,
    QueuedHooks,
    QueuedTarget,
    cancel_queued_verified,
    stop_named_pods,
    terminate_verified,
)
from transcribe_ui.state.store import LocalState


def stop_run(state: LocalState, pods: PodsApi, run_id: str, clock: Clock) -> str | None:
    """Stop the run's pod (or queued pod), then every other live pod under its name, verified.

    Returns the id of the run's pod terminated, if any. The run's status is left to the caller
    (a pod learned on the way makes it `running`). No pod carrying the run's name may still bill
    once the stop shows, so the pods listed under the name are stopped too (`stop_named_pods`);
    one whose stop does not show is recorded on the run, which follows it.

    Raises:
        LaunchUnresolvedError: The run knows neither a pod nor a queued pod.
        StopUnverifiedError: The stop was sent but does not show yet.
        DomainError: A Runpod call failed.
    """
    name = read_run(state, run_id).pod_name
    stopped = _stop_recorded(state, pods, run_id, clock)
    skip = frozenset(stopped)
    stop_named_pods(pods, name, skip, clock, partial(_follow_still_live, state, run_id, clock))
    return stopped[0] if stopped else None


def _stop_recorded(state: LocalState, pods: PodsApi, run_id: str, clock: Clock) -> tuple[str, ...]:
    """Stop the pod the run recorded, else its queued pod and what it deployed; return the pods terminated.

    Raises:
        LaunchUnresolvedError: The run knows neither a pod nor a queued pod.
        DomainError: The stop does not show, or a Runpod call failed.
    """
    payload = read_payload(state, run_id)
    seen = partial(mark_seen, state, run_id, clock)
    pod = payload.known_pod()
    if pod is not None:
        terminate_verified(pods, pod, clock, seen)
        return (pod.pod_id,)
    if payload.queued_pod_id is None:
        raise LaunchUnresolvedError(run_id)  # not final, yet neither id is known
    # A pod the queued pod deployed carries the run's pod name: found by it if the queued pod is gone.
    name = read_run(state, run_id).pod_name
    target = QueuedTarget(
        payload.queued_pod_id,
        name=name,
        expires_at=payload.queue_expires_at,
        acknowledged_at=payload.cancel_acknowledged_at,
        first_empty_lookup_at=payload.first_empty_lookup_at,
        tracked=True,
    )
    hooks = run_hooks(state, run_id, clock, partial(_follow_pod, state, run_id, clock))
    return cancel_queued_verified(pods, target, clock, hooks).pod_ids


def run_hooks(state: LocalState, run_id: str, clock: Clock, on_deployed: OnDeployed) -> QueuedHooks:
    """Return the hooks that record a queued pod's stop on its local run."""
    return QueuedHooks(
        on_deployed=on_deployed,
        on_seen=partial(mark_seen, state, run_id, clock),
        on_acknowledged=partial(_record_acknowledgement, state, run_id),
        on_queue_seen=partial(_record_queue_seen, state, run_id, clock),
        on_empty_lookup=partial(_record_empty_lookup, state, run_id),
    )


def _record_acknowledgement(state: LocalState, run_id: str, acknowledged_at: datetime) -> None:
    """Record when the run's queued pod cancel was first acknowledged (the settle anchor of a vanished queued pod)."""
    if read_payload(state, run_id).cancel_acknowledged_at is None:
        update_run(state, run_id, acknowledged_at, changes={"cancel_acknowledged_at": acknowledged_at})


def _record_empty_lookup(state: LocalState, run_id: str, now: datetime) -> None:
    """Record the first empty lookup after the settle time of the run's vanished queued pod."""
    update_run(state, run_id, now, changes={"first_empty_lookup_at": now})


def _follow_pod(state: LocalState, run_id: str, clock: Clock, pod: KnownPod, _handled: frozenset[str]) -> None:
    """Record the pod a queued pod deployed during the stop; the run follows it until it is terminated.

    The run recorded no pod (else its pod was stopped instead), so any pod of its queued pod is its own.
    """
    changes = {"pod_id": pod.pod_id, "pod_seen": pod.seen, "pod_deployed_at": pod.deployed_at}
    update_run(state, run_id, clock.now(), "running", changes)


def _follow_still_live(state: LocalState, run_id: str, clock: Clock, pod: KnownPod) -> None:
    """Record a pod under the run's name whose stop did not show: the run follows it until it is gone."""
    changes = {"pod_id": pod.pod_id, "pod_seen": pod.seen, "pod_deployed_at": pod.deployed_at}
    update_run(state, run_id, clock.now(), "running", changes)


def _record_queue_seen(state: LocalState, run_id: str, clock: Clock, status: str) -> None:
    """A read after the cancel still shows the queued pod: an earlier empty lookup no longer counts.

    Still `QUEUED`: the cancel did not take effect, so its acknowledgement bounds nothing either.
    """
    changes: dict[str, object] = {"first_empty_lookup_at": None}
    if status == QUEUED_POD_QUEUED:
        changes["cancel_acknowledged_at"] = None
    update_run(state, run_id, clock.now(), changes=changes)


def mark_seen(state: LocalState, run_id: str, clock: Clock, pod_id: str) -> None:
    """Record that a pod read showed the run's pod: from now on a 404 for it means gone.

    Only for the pod the run recorded (another pod carrying its name is not the run's).
    """
    if read_payload(state, run_id).pod_id == pod_id:
        update_run(state, run_id, clock.now(), changes={"pod_seen": True})
