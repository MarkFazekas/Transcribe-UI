"""Stopping a run's pod or queued pod, verified (used by `cancel_worker` and the desktop deadline).

A run with a pod id terminates that pod (`KnownPod`: a pod known only from its queued pod is not
seen, so a 404 counts as gone only `DEPLOY_SETTLE_S` after the deploy, and a 204 on its delete
is no sighting). A run with only a queued-pod id cancels it; a pod it deployed meanwhile (named
by the queued pod, or, when the queued pod is gone after the cancel, listed under the run's pod
name) is recorded (the run follows it as `running`) and terminated. A pod read that shows the
pod is recorded as a sighting, so a later 404 counts. The cancel's acknowledgement and the first
empty lookup of a vanished queued pod are recorded on the run (the settle rule needs both).
"""

from collections.abc import Callable
from datetime import datetime
from functools import partial

from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.ports import Clock
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runpod.wire import QueuedPod
from transcribe_ui.runs.constants import IGNORED_QUEUED_POD_STATUSES, QUEUED_POD_KIND
from transcribe_ui.runs.errors import LaunchUnresolvedError, StopUnverifiedError
from transcribe_ui.runs.records import read_payload, read_run, update_run
from transcribe_ui.runs.stopping import (
    KnownPod,
    OnDeployed,
    OnStillLive,
    PodIds,
    QueuedHooks,
    QueuedTarget,
    cancel_and_read,
    cancel_queued_verified,
    stop_named_pods,
    stop_pod,
    terminate_verified,
    vanished,
)
from transcribe_ui.state.store import LocalState

# A call's result, or the domain error it raised.
type Attempt[ResultT] = tuple[ResultT | None, DomainError | None]


def stop_run(state: LocalState, pods: PodsApi, run_id: str, clock: Clock) -> str | None:
    """Stop the run's pod (or queued pod), then every other live pod under its name, verified.

    Returns the id of the run's pod terminated, if any. The run's status is left to the caller
    (a pod learned on the way makes it `running`). No pod carrying the run's name may still bill,
    and no queued pod of it may still deploy, once the stop shows: the pods listed under the name
    are stopped too (`stop_named_pods`), even when the run's own stop failed; one whose stop does
    not show is recorded on the run (unless the run follows its own failed pod), and the first
    error is raised once both were tried.

    Raises:
        LaunchUnresolvedError: The run knows neither a pod nor a queued pod.
        StopUnverifiedError: The stop was sent but does not show yet.
        DomainError: A Runpod call failed.
    """
    name = read_run(state, run_id).pod_name
    stopped, own_error = _attempt(partial(_stop_recorded, state, pods, run_id, clock))
    skip = set(stopped or ())
    on_still_live: OnStillLive | None = partial(_follow_still_live, state, run_id, clock)
    following = read_payload(state, run_id).pod_id
    if own_error is not None and following is not None:
        # The run follows its own pod whose stop failed: a failed named pod is not followed instead.
        skip.add(following)
        on_still_live = None
    named_stop = partial(stop_named_pods, pods, name, frozenset(skip), clock, on_still_live)
    _named, named_error = _attempt(named_stop)
    first_error = own_error or named_error
    if first_error is not None:
        raise first_error
    return stopped[0] if stopped else None


def _attempt[ResultT](call: Callable[[], ResultT]) -> Attempt[ResultT]:
    """Run the call; return its result, or the domain error it raised (a cancel is raised at once)."""
    try:
        return call(), None
    except CancelledError:
        raise
    except DomainError as error:
        return None, error


def _stop_recorded(state: LocalState, pods: PodsApi, run_id: str, clock: Clock) -> tuple[str, ...]:
    """Stop the pod the run recorded, else its queued pod and what it deployed; return the pods terminated.

    Raises:
        LaunchUnresolvedError: The run knows neither a pod nor a queued pod.
        DomainError: The stop does not show, or a Runpod call failed.
    """
    payload = read_payload(state, run_id)
    seen = partial(mark_seen, state, run_id, clock)
    pod = payload.known_pod()
    queued_pod_id = payload.queued_pod_id
    if pod is None and queued_pod_id is None:
        raise LaunchUnresolvedError(run_id)  # not final, yet neither id is known
    hooks = run_hooks(state, run_id, clock, partial(_follow_pod, state, run_id, clock))
    if pod is not None:
        terminate_verified(pods, pod, clock, seen)
        if queued_pod_id is None:
            return (pod.pod_id,)
        # The run's queued pod may still be waiting (the run adopted a pod by name): it must not
        # deploy, and a pod it deployed meanwhile is recorded (the run follows it) and stopped.
        target = _queued_target(state, run_id)
        extra = cancel_waiting_queue(pods, target, frozenset((pod.pod_id,)), clock, hooks)
        return (pod.pod_id, *extra)
    return cancel_queued_verified(pods, _queued_target(state, run_id), clock, hooks).pod_ids


def _queued_target(state: LocalState, run_id: str) -> QueuedTarget:
    """Return the run's queued pod as its payload records it (a pod it deploys carries the run's name)."""
    payload = read_payload(state, run_id)
    queued_pod_id = payload.queued_pod_id
    assert queued_pod_id is not None, "the caller checked that the run has a queued pod"
    return QueuedTarget(
        queued_pod_id,
        name=read_run(state, run_id).pod_name,
        expires_at=payload.queue_expires_at,
        acknowledged_at=payload.cancel_acknowledged_at,
        first_empty_lookup_at=payload.first_empty_lookup_at,
        tracked=True,
    )


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

    The run recorded no pod, or its pod is verified stopped already, so a pod of its queued pod is
    the one to follow.
    """
    changes = {"pod_id": pod.pod_id, "pod_seen": pod.seen, "pod_deployed_at": pod.deployed_at}
    update_run(state, run_id, clock.now(), "running", changes)


def _follow_still_live(state: LocalState, run_id: str, clock: Clock, pod: KnownPod) -> None:
    """Record a pod under the run's name whose stop did not show: the run follows it until it is gone."""
    changes = {"pod_id": pod.pod_id, "pod_seen": pod.seen, "pod_deployed_at": pod.deployed_at}
    update_run(state, run_id, clock.now(), "running", changes)


def _record_queue_seen(state: LocalState, run_id: str, clock: Clock, _status: str) -> None:
    """A read after the cancel still shows the queued pod: an earlier empty lookup no longer counts.

    The acknowledgement stays, even on a `QUEUED` read: that read follows the 204 in the same call
    (lag after the cancel), and voiding it could leave the run waiting for hours should Runpod
    then forget the queued pod. `refresh_status` voids it on a later `QUEUED` read.
    """
    update_run(state, run_id, clock.now(), changes={"first_empty_lookup_at": None})


def mark_seen(state: LocalState, run_id: str, clock: Clock, pod_id: str) -> None:
    """Record that a pod read showed the run's pod: from now on a 404 for it means gone.

    Only for the pod the run recorded (another pod carrying its name is not the run's).
    """
    if read_payload(state, run_id).pod_id == pod_id:
        update_run(state, run_id, clock.now(), changes={"pod_seen": True})


def cancel_waiting_queue(
    pods: PodsApi,
    target: QueuedTarget,
    skip: frozenset[str],
    clock: Clock,
    hooks: QueuedHooks,
) -> PodIds:
    """Cancel a queued pod that may still be waiting, after the run's pod was stopped; return the pods terminated.

    The same reading as `cancel_queued_verified`: done once a read shows it ended (`CANCELLED`,
    `EXPIRED`, `FAILED`), or it deployed a pod that is stopped (`skip`, or terminated and
    verified here, recorded on the run first through `on_deployed`), or it is gone (404) by the
    settle rule. Still `QUEUED`, deployed without its pod's id, or gone too early: not verified.
    Pods listed under the run's name are the caller's (`stop_named_pods`). The DELETE is harmless
    when it deployed or ended.

    Raises:
        StopUnverifiedError: The cancel or the stop of its pod does not show yet.
        DomainError: A Runpod call failed.
    """
    entry, acknowledged_at = cancel_and_read(pods, target.queued_pod_id, clock, hooks)
    if entry is None:
        if vanished(target, acknowledged_at, clock.now(), hooks):
            return ()
        raise StopUnverifiedError(QUEUED_POD_KIND, target.queued_pod_id)
    if entry.pod_id is None:
        if entry.status in IGNORED_QUEUED_POD_STATUSES:
            return ()
        raise StopUnverifiedError(QUEUED_POD_KIND, target.queued_pod_id)  # QUEUED, or DEPLOYED without its pod
    return _stop_queued_pod(pods, entry, skip, clock, hooks)


def _stop_queued_pod(pods: PodsApi, entry: QueuedPod, skip: frozenset[str], clock: Clock, hooks: QueuedHooks) -> PodIds:
    """Stop the pod the queued pod names unless it was stopped already; the run follows it first."""
    pod_id = entry.pod_id
    if pod_id is None or pod_id in skip:
        return ()
    deployed = KnownPod(pod_id, seen=False, deployed_at=entry.resolved_at)
    if hooks.on_deployed is not None:
        hooks.on_deployed(deployed, frozenset((pod_id,)))
    stop_pod(pods, deployed, clock, hooks.on_seen)
    return (pod_id,)
