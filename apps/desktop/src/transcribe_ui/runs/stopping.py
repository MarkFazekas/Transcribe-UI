"""Stopping pods and queued pods, each verified by a follow-up read (plan m5 §1 "Cancel follows a deploy").

A terminate counts once the pod reads `TERMINATED`, or is unknown (404) after a pod read showed
it or, for a pod known only from its queued pod, `DEPLOY_SETTLE_S` after the deploy (a 204 on
the delete is no sighting).
A cancel counts once the queued pod reads `CANCELLED`, `EXPIRED` or `FAILED` (none of them leads
to a pod), or is unknown (404) by the settle rule (`vanish_verdict`, shared with `refresh_status`);
every pod it deployed (named by it, by an earlier read, or listed under its name once it is
gone) is read, then terminated and verified unless it reads `TERMINATED`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, NamedTuple

from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.ports import Clock
from transcribe_ui.runpod.constants import POD_TERMINATED, QUEUED_POD_QUEUED
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runpod.wire import QueuedPod
from transcribe_ui.runs.classes import RunpodKind
from transcribe_ui.runs.constants import (
    DEPLOY_SETTLE_S,
    IGNORED_QUEUED_POD_STATUSES,
    LAUNCH_LOOKUP_GAP_S,
    POD_KIND,
    QUEUED_POD_KIND,
)
from transcribe_ui.runs.errors import StopUnverifiedError, runpod_call
from transcribe_ui.runs.responses import QueuedStop

# Told the pod a queued pod deployed, before that pod is terminated (so a run can follow it if
# the termination cannot be verified).
type OnDeployed = Callable[["KnownPod", frozenset[str]], None]
# Told the id of a pod known only from its queued pod once a pod read returned it (a sighting).
type OnSeen = Callable[[str], None]


class CloudObject(NamedTuple):
    """A pod or queued pod as read from Runpod."""

    kind: RunpodKind
    object_id: str
    name: str
    status: str
    # For a queued pod: the pod it deployed (None until then; always None for a pod).
    pod_id: str | None = None


@dataclass(frozen=True)
class QueuedStopped:
    """A queued pod stopped: cancelled (or ended), or the pods it deployed terminated."""

    outcome: QueuedStop
    # The pods terminated (verified), the one recorded on the run first.
    pod_ids: tuple[str, ...] = ()
    # Whether something live was stopped: a queued pod that had not ended, or a pod that read live.
    stopped_live: bool = True

    @property
    def pod_id(self) -> str | None:
        """Return the first pod terminated, or None."""
        return self.pod_ids[0] if self.pod_ids else None


@dataclass(frozen=True)
class KnownPod:
    """A pod to terminate, and whether a pod read ever showed it.

    A pod id learned only from a queued pod (`podId`) was never seen: right after the deploy
    a 404 for it may be lag, so it counts as gone only `DEPLOY_SETTLE_S` after the deploy.
    """

    pod_id: str
    seen: bool
    # The queued pod's `resolvedAt` (its deploy) for a pod that was not seen; None if unknown.
    deployed_at: datetime | None = None

    def gone_when_missing(self, now: datetime) -> bool:
        """Return whether a 404 for the pod means it is gone (not list or lookup lag)."""
        if self.seen:
            return True
        deployed = self.deployed_at
        return deployed is not None and now - deployed >= timedelta(seconds=DEPLOY_SETTLE_S)


def terminate_verified(pods: PodsApi, pod: KnownPod, clock: Clock, on_seen: OnSeen | None = None) -> None:
    """Terminate the pod and read it again.

    A 404 (on the delete or the read) verifies it only for a pod that was seen (a pod read showed
    it), or one whose deploy has settled; otherwise it may not be visible yet. A 204 on the
    delete proves nothing (Runpod may answer it for an id it does not know yet). A read that
    returns the pod, in any status, is a sighting: `on_seen` is told, so a later 404 counts.

    Raises:
        StopUnverifiedError: The pod still reads live, or is missing too early to count as gone.
        DomainError: A Runpod call failed (the pod may be live).
    """
    pod_id = pod.pod_id
    existed = runpod_call(lambda: pods.terminate(pod_id))
    if not existed and not pod.gone_when_missing(clock.now()):
        raise StopUnverifiedError(POD_KIND, pod_id)
    found = runpod_call(lambda: pods.get_pod(pod_id))
    if found is None:
        if pod.gone_when_missing(clock.now()):
            return
        raise StopUnverifiedError(POD_KIND, pod_id)
    if not pod.seen and on_seen is not None:
        on_seen(pod_id)
    if found.status != POD_TERMINATED:
        raise StopUnverifiedError(POD_KIND, pod_id)


# How a queued pod Runpod no longer knows (404) is judged: gone (no pod of it can still show up),
# the first empty lookup after the settle time (to record), or too early.
type VanishVerdict = Literal["gone", "record", "wait"]
# Told the moment the queued pod's cancel was acknowledged (a success answer to its DELETE).
type OnAcknowledged = Callable[[datetime], None]
# Ids of pods terminated by a stop.
type PodIds = tuple[str, ...]
# Whether a pod stop found the pod live, or the error that kept the stop from showing.
type StopOutcome = bool | DomainError
# A pod whose stop did not show, with the error.
type FailedStop = tuple["KnownPod", DomainError]
# Told the status of the queued pod when a read after the cancel still shows it.
type OnQueueSeen = Callable[[str], None]
# Told a pod under the run's name whose stop did not show (the run follows it).
type OnStillLive = Callable[["KnownPod"], None]
# Told the moment of an empty lookup after the settle time (the first of the two the rule needs).
type OnEmptyLookup = Callable[[datetime], None]


def queue_settled_from(expires_at: datetime | None, acknowledged_at: datetime | None) -> datetime | None:
    """Return when a pod the queued pod might have deployed is surely listed; None if unknown.

    A queued pod deploys only before its queue's `expiresAt` and before its cancel took effect,
    so the earlier of the two, plus `DEPLOY_SETTLE_S` (the list lag after a deploy), bounds it.
    `expiresAt` is Runpod's clock and the acknowledgement this computer's: the rule assumes the
    two differ by much less than `DEPLOY_SETTLE_S`.
    """
    known = [moment for moment in (expires_at, acknowledged_at) if moment is not None]
    return min(known) + timedelta(seconds=DEPLOY_SETTLE_S) if known else None


def vanish_verdict(
    settled_from: datetime | None, first_empty_lookup_at: datetime | None, now: datetime
) -> VanishVerdict:
    """Return how a queued pod Runpod no longer knows, with no pod listed under its name, is judged.

    Gone only after the settle time (`queue_settled_from`) and two empty lookups at least
    `LAUNCH_LOOKUP_GAP_S` apart (one empty answer may be lag or a glitch); the first is recorded.
    """
    if settled_from is None or now < settled_from:
        return "wait"
    if first_empty_lookup_at is None or first_empty_lookup_at < settled_from:
        return "record"
    if now - first_empty_lookup_at < timedelta(seconds=LAUNCH_LOOKUP_GAP_S):
        return "wait"
    return "gone"


@dataclass(frozen=True)
class QueuedHooks:
    """What a caller is told during a queued pod's stop, to record it on its local run.

    `on_deployed`: the pod the run follows, before it is terminated (the first pod, or later the
    first whose stop did not show), with the ids of every pod this stop handles; `on_seen`: a pod
    read showed a pod; `on_acknowledged`: the cancel was acknowledged; `on_queue_seen`: a read
    after the cancel still shows the queued pod (with its status); `on_empty_lookup`: the first
    empty lookup after the settle time.
    """

    on_deployed: OnDeployed | None = None
    on_seen: OnSeen | None = None
    on_acknowledged: OnAcknowledged | None = None
    on_queue_seen: OnQueueSeen | None = None
    on_empty_lookup: OnEmptyLookup | None = None


@dataclass(frozen=True)
class QueuedTarget:
    """A queued pod to cancel, with what is known of it and of a pod it may have deployed.

    `deployed` is the pod an earlier read named (None if none did); `name` is the queued pod's
    name, which a pod it deploys carries too, to look the pod up when the queued pod is gone
    (404) after the cancel; `expires_at` is the queue's `expiresAt` (a pod deploys only before
    it); `ended` says an earlier read showed it ended without a pod. `acknowledged_at` and
    `first_empty_lookup_at` are what an earlier stop recorded on the local run; `tracked` says
    there is such a run to record on (without one, a single empty lookup after the settle time
    counts, since nothing local becomes final).
    """

    queued_pod_id: str
    name: str | None = None
    deployed: KnownPod | None = None
    expires_at: datetime | None = None
    ended: bool = False
    acknowledged_at: datetime | None = None
    first_empty_lookup_at: datetime | None = None
    tracked: bool = False

    @classmethod
    def read_from(cls, entry: QueuedPod) -> "QueuedTarget":
        """Return the queued pod as read, with the pod this read names (not seen: it settles from the deploy)."""
        pod_id = entry.pod_id
        deployed = None
        if pod_id is not None:
            deployed = KnownPod(pod_id, seen=False, deployed_at=entry.resolved_at)
        expires_at = None if entry.queue is None else entry.queue.expires_at
        return cls(entry.queued_pod_id, entry.name, deployed, expires_at, ended=ended_without_pod(entry))

    def vanish(self, acknowledged_at: datetime | None, now: datetime) -> VanishVerdict:
        """Return how a 404 after the cancel with no pod listed under its name is judged (see `vanish_verdict`)."""
        if self.ended or not self.tracked:
            # Ended without a pod as read before; or no local run that could become final: the
            # answer is final, and a pod that shows up later is a straggler for `reconcile_cloud`.
            return "gone"
        known = [moment for moment in (self.acknowledged_at, acknowledged_at) if moment is not None]
        settled_from = queue_settled_from(self.expires_at, min(known, default=None))
        return vanish_verdict(settled_from, self.first_empty_lookup_at, now)


def ended_without_pod(entry: QueuedPod) -> bool:
    """Return whether the queued pod as read had ended without a pod (a cancel then stops nothing)."""
    return entry.pod_id is None and entry.status in IGNORED_QUEUED_POD_STATUSES


def cancel_queued_verified(
    pods: PodsApi,
    target: QueuedTarget,
    clock: Clock,
    hooks: QueuedHooks | None = None,
) -> QueuedStopped:
    """Cancel the queued pod and read it again; terminate (verified) every pod it deployed.

    The pods to terminate are the one the queued pod names now, else the one an earlier read
    named (`target.deployed`), else, when the queued pod is gone (404), every live pod listed
    under its name: a queued pod that deployed just before the cancel and then vanished must not
    leave its pod running. Every one is stopped even when one fails; the first failure is raised
    afterwards, and the run is told to follow the first pod whose stop did not show. With none
    of these pods, a 404 counts as cancelled only by the settle rule (`QueuedTarget.vanish`).

    Raises:
        StopUnverifiedError: It still reads waiting, reads deployed without naming its pod, is gone
            too early to rule out a pod not listed yet, or a pod's stop does not show.
        DomainError: A Runpod call failed.
    """
    told = hooks or QueuedHooks()
    queued_pod_id = target.queued_pod_id
    acknowledged = runpod_call(lambda: pods.cancel_queued(queued_pod_id))
    acknowledged_at = clock.now() if acknowledged else None
    if acknowledged_at is not None and told.on_acknowledged is not None:
        told.on_acknowledged(acknowledged_at)
    entry = _read_after_cancel(pods, queued_pod_id, told)
    deployed = _deployed_pods(pods, target, entry)
    if deployed:
        return _stop_deployed(pods, deployed, clock, told)
    cancelled = QueuedStopped("cancelled", stopped_live=not target.ended)
    if entry is None:
        if _vanished(target, acknowledged_at, clock.now(), told):
            return cancelled
    elif entry.status in IGNORED_QUEUED_POD_STATUSES:
        return cancelled
    # QUEUED (the cancel did not take effect), DEPLOYED without its pod's id, gone too early, or
    # an unknown status.
    raise StopUnverifiedError(QUEUED_POD_KIND, queued_pod_id)


def _read_after_cancel(pods: PodsApi, queued_pod_id: str, told: QueuedHooks) -> QueuedPod | None:
    """Read the queued pod after its cancel; a read that shows it is told (`on_queue_seen`).

    Raises:
        DomainError: The read failed.
    """
    entry = runpod_call(lambda: pods.get_queued_pod(queued_pod_id))
    if entry is not None and told.on_queue_seen is not None:
        told.on_queue_seen(entry.status)
    return entry


def _vanished(target: QueuedTarget, acknowledged_at: datetime | None, now: datetime, told: QueuedHooks) -> bool:
    """Return whether a queued pod gone after the cancel counts as cancelled; record a first empty lookup."""
    verdict = target.vanish(acknowledged_at, now)
    if verdict == "record" and told.on_empty_lookup is not None:
        told.on_empty_lookup(now)
    return verdict == "gone"


def _stop_deployed(pods: PodsApi, deployed: list[KnownPod], clock: Clock, told: QueuedHooks) -> QueuedStopped:
    """Stop every pod the queued pod deployed; raise the first failure once all were tried.

    Raises:
        DomainError: A pod's stop did not show or a Runpod call failed (the run follows that pod).
    """
    handled = frozenset(each.pod_id for each in deployed)
    if told.on_deployed is not None:
        told.on_deployed(deployed[0], handled)
    on_seen = told.on_seen
    outcomes = [_try_stop(pods, each, clock, on_seen) for each in deployed]
    failed = _first_failure(deployed, outcomes)
    if failed is not None:
        failed_pod, error = failed
        if told.on_deployed is not None and failed_pod is not deployed[0]:
            told.on_deployed(failed_pod, handled)
        raise error
    pod_ids = tuple(each.pod_id for each in deployed)
    live = [outcome is True for outcome in outcomes]
    return QueuedStopped("pod_terminated", pod_ids, stopped_live=any(live))


def _first_failure(deployed: list[KnownPod], outcomes: list[StopOutcome]) -> FailedStop | None:
    """Return the first pod whose stop did not show, with its error; None when all did."""
    for pod, outcome in zip(deployed, outcomes, strict=True):
        if isinstance(outcome, DomainError):
            return pod, outcome
    return None


def _try_stop(pods: PodsApi, pod: KnownPod, clock: Clock, on_seen: OnSeen | None) -> StopOutcome:
    """Stop the pod (`_stop_pod`); return whether it was live, or the error that kept the stop from showing.

    Raises:
        CancelledError: The request was cancelled.
    """
    try:
        return _stop_pod(pods, pod, clock, on_seen)
    except CancelledError:
        raise
    except DomainError as error:
        return error


def _deployed_pods(pods: PodsApi, target: QueuedTarget, entry: QueuedPod | None) -> list[KnownPod]:
    """Return the pods the queued pod deployed, as far as is known (see `cancel_queued_verified`).

    Raises:
        DomainError: The pod list could not be read.
    """
    if entry is not None and entry.pod_id is not None:
        return [KnownPod(entry.pod_id, seen=False, deployed_at=entry.resolved_at)]
    if target.deployed is not None:
        return [target.deployed]
    if entry is None and target.name is not None:
        return _listed_pods(pods, target.name)
    return []


def _stop_pod(pods: PodsApi, pod: KnownPod, clock: Clock, on_seen: OnSeen | None) -> bool:
    """Terminate the pod (verified) unless it reads `TERMINATED`; return whether it was live.

    A read that returns the pod is a sighting (`on_seen` is told). A pod that reads `TERMINATED`
    had ended: nothing to stop. A 404 still sends the DELETE (harmless when the pod is gone; list
    and lookup lag may hide a live pod) and is verified as `terminate_verified` does; the pod
    counts as live unless the 404 already meant gone.

    Raises:
        StopUnverifiedError: The pod still reads live, or is missing too early to count as gone.
        DomainError: A Runpod call failed.
    """
    pod_id = pod.pod_id
    found = runpod_call(lambda: pods.get_pod(pod_id))
    if found is None:
        gone = pod.gone_when_missing(clock.now())
        terminate_verified(pods, pod, clock, on_seen)
        return not gone
    if not pod.seen and on_seen is not None:
        on_seen(pod_id)
    if found.status == POD_TERMINATED:
        return False
    terminate_verified(pods, KnownPod(pod_id, seen=True), clock, on_seen)
    return True


def cancel_waiting_queue(pods: PodsApi, queued_pod_id: str, skip: frozenset[str], clock: Clock) -> PodIds:
    """Cancel a queued pod that may still be waiting, after the run's pod was stopped; return the pods terminated.

    Done once a read shows it is not `QUEUED` (or Runpod no longer knows it); a pod it names that
    was not stopped already (`skip`) is terminated and verified. The DELETE is harmless when the
    queued pod deployed or ended.

    Raises:
        StopUnverifiedError: It still reads `QUEUED`.
        DomainError: A Runpod call failed, or the stop of its pod does not show.
    """
    runpod_call(lambda: pods.cancel_queued(queued_pod_id))
    entry = runpod_call(lambda: pods.get_queued_pod(queued_pod_id))
    if entry is None:
        return ()
    if entry.status == QUEUED_POD_QUEUED:
        raise StopUnverifiedError(QUEUED_POD_KIND, queued_pod_id)
    pod_id = entry.pod_id
    if pod_id is None or pod_id in skip:
        return ()
    deployed = KnownPod(pod_id, seen=False, deployed_at=entry.resolved_at)
    _stop_pod(pods, deployed, clock, None)
    return (pod_id,)


def stop_named_pods(
    pods: PodsApi,
    name: str,
    skip: frozenset[str],
    clock: Clock,
    on_still_live: OnStillLive | None = None,
) -> tuple[str, ...]:
    """Terminate (verified) every live pod listed under the name but the ones in `skip`; return their ids.

    A run must not become final while a pod carrying its name may still bill: a queued pod may
    have deployed more than one, a stop may have been cut short. Every one is tried; the first
    pod whose stop did not show is told to `on_still_live` and its error raised afterwards.

    Raises:
        DomainError: The pod list could not be read, or a pod's stop did not show.
    """
    named = [pod for pod in _listed_pods(pods, name) if pod.pod_id not in skip]
    outcomes = [_try_stop(pods, pod, clock, None) for pod in named]
    failed = _first_failure(named, outcomes)
    if failed is not None:
        failed_pod, error = failed
        if on_still_live is not None:
            on_still_live(failed_pod)
        raise error
    return tuple(pod.pod_id for pod in named)


def _listed_pods(pods: PodsApi, name: str) -> list[KnownPod]:
    """Return every live pod listed under the name (a listing is a sighting).

    Raises:
        DomainError: The pod list could not be read.
    """
    named = [pod for pod in runpod_call(pods.list_pods) if pod.name == name]
    live = [pod.pod_id for pod in named if pod.status != POD_TERMINATED]
    return [KnownPod(pod_id, seen=True) for pod_id in live]
