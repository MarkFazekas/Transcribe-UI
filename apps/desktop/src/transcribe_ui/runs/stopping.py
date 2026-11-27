"""Stopping pods and queued pods, each verified by a follow-up read (plan m5 §1 "Cancel follows a deploy").

A terminate counts once the pod reads `TERMINATED`, or is unknown (404) after a pod read showed
it or, for a pod known only from its queued pod, `DEPLOY_SETTLE_S` after the deploy (a 204 on
the delete is no sighting).
A cancel counts once the queued pod reads `CANCELLED`, `EXPIRED` or `FAILED` (none of them leads
to a pod), or is unknown once that is settled (`QueuedTarget.vanish_settled`); every pod it
deployed (named by it, by an earlier read, or listed under its name once it is gone) is read,
then terminated and verified unless it had ended.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

from transcribe_ui.api.ports import Clock
from transcribe_ui.runpod.constants import POD_TERMINATED
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runpod.wire import QueuedPod
from transcribe_ui.runs.classes import RunpodKind
from transcribe_ui.runs.constants import DEPLOY_SETTLE_S, IGNORED_QUEUED_POD_STATUSES, POD_KIND, QUEUED_POD_KIND
from transcribe_ui.runs.errors import StopUnverifiedError, runpod_call
from transcribe_ui.runs.responses import QueuedStop

# Told the pod a queued pod deployed, before that pod is terminated (so a run can follow it if
# the termination cannot be verified).
type OnDeployed = Callable[["KnownPod"], None]
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


@dataclass(frozen=True)
class QueuedTarget:
    """A queued pod to cancel, with what is known of it and of a pod it may have deployed.

    `deployed` is the pod an earlier read named (None if none did); `name` is the queued pod's
    name, which a pod it deploys carries too, to look the pod up when the queued pod is gone
    (404) after the cancel; `expires_at` is the queue's `expiresAt` (a pod deploys only before
    it); `ended` says an earlier read showed it ended without a pod.
    """

    queued_pod_id: str
    name: str | None = None
    deployed: KnownPod | None = None
    expires_at: datetime | None = None
    ended: bool = False

    @classmethod
    def read_from(cls, entry: QueuedPod) -> "QueuedTarget":
        """Return the queued pod as read, with the pod this read names (not seen: it settles from the deploy)."""
        pod_id = entry.pod_id
        deployed = None
        if pod_id is not None:
            deployed = KnownPod(pod_id, seen=False, deployed_at=entry.resolved_at)
        expires_at = None if entry.queue is None else entry.queue.expires_at
        return cls(entry.queued_pod_id, entry.name, deployed, expires_at, ended=ended_without_pod(entry))

    def vanish_settled(self, now: datetime) -> bool:
        """Return whether a 404 with no pod listed under its name means it deployed none.

        True when an earlier read showed it ended, or `DEPLOY_SETTLE_S` has passed since its queue
        expired (a pod deployed just before then would be listed by now).
        """
        if self.ended:
            return True
        expires = self.expires_at
        return expires is not None and now - expires >= timedelta(seconds=DEPLOY_SETTLE_S)


def ended_without_pod(entry: QueuedPod) -> bool:
    """Return whether the queued pod as read had ended without a pod (a cancel then stops nothing)."""
    return entry.pod_id is None and entry.status in IGNORED_QUEUED_POD_STATUSES


def cancel_queued_verified(
    pods: PodsApi,
    target: QueuedTarget,
    clock: Clock,
    on_deployed: OnDeployed | None = None,
    on_seen: OnSeen | None = None,
) -> QueuedStopped:
    """Cancel the queued pod and read it again; terminate (verified) every pod it deployed.

    The pods to terminate are the one the queued pod names now, else the one an earlier read
    named (`target.deployed`), else, when the queued pod is gone (404), every live pod listed
    under its name: a queued pod that deployed just before the cancel and then vanished must not
    leave its pod running. With none of these, a 404 counts as cancelled only once it is settled
    (`QueuedTarget.vanish_settled`): a pod just deployed may not be listed yet. `on_deployed` is
    told the first pod (the run follows it should its stop not show).

    Raises:
        StopUnverifiedError: It still reads waiting, reads deployed without naming its pod, is gone
            too early to rule out a pod not listed yet, or a pod still reads live (or is missing
            right after the deploy).
        DomainError: A Runpod call failed.
    """
    queued_pod_id = target.queued_pod_id
    runpod_call(lambda: pods.cancel_queued(queued_pod_id))
    entry = runpod_call(lambda: pods.get_queued_pod(queued_pod_id))
    deployed = _deployed_pods(pods, target, entry)
    if deployed:
        if on_deployed is not None:
            on_deployed(deployed[0])
        live = [_stop_pod(pods, pod, clock, on_seen) for pod in deployed]
        pod_ids = tuple(pod.pod_id for pod in deployed)
        return QueuedStopped("pod_terminated", pod_ids, stopped_live=any(live))
    gone = entry is None and target.vanish_settled(clock.now())
    if gone or (entry is not None and entry.status in IGNORED_QUEUED_POD_STATUSES):
        return QueuedStopped("cancelled", stopped_live=not target.ended)
    # QUEUED (the cancel did not take effect), DEPLOYED without its pod's id, gone too early, or
    # an unknown status.
    raise StopUnverifiedError(QUEUED_POD_KIND, queued_pod_id)


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
    """Terminate the pod (verified) unless it had ended; return whether it read live before.

    A read that returns the pod is a sighting (`on_seen` is told). A pod that reads `TERMINATED`,
    or is missing once a 404 means gone, had ended: nothing to stop.

    Raises:
        StopUnverifiedError: The pod still reads live, or is missing too early to count as gone.
        DomainError: A Runpod call failed.
    """
    pod_id = pod.pod_id
    found = runpod_call(lambda: pods.get_pod(pod_id))
    if found is None and pod.gone_when_missing(clock.now()):
        return False
    if found is not None:
        if not pod.seen and on_seen is not None:
            on_seen(pod_id)
        pod = KnownPod(pod_id, seen=True)
        if found.status == POD_TERMINATED:
            return False
    terminate_verified(pods, pod, clock, on_seen)
    return True


def _listed_pods(pods: PodsApi, name: str) -> list[KnownPod]:
    """Return every live pod listed under the name (a listing is a sighting).

    Raises:
        DomainError: The pod list could not be read.
    """
    named = [pod for pod in runpod_call(pods.list_pods) if pod.name == name]
    live = [pod.pod_id for pod in named if pod.status != POD_TERMINATED]
    return [KnownPod(pod_id, seen=True) for pod_id in live]
