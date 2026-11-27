"""Stopping pods and queued pods, each verified by a follow-up read (plan m5 §1 "Cancel follows a deploy").

A terminate counts once the pod reads `TERMINATED`, or is unknown (404) after a pod read showed
it or, for a pod known only from its queued pod, `DEPLOY_SETTLE_S` after the deploy.
A cancel counts once the queued pod reads `CANCELLED`, `EXPIRED` or `FAILED` (none of them leads
to a pod), or is unknown; when it names a pod, it deployed during the cancel, and that pod is
terminated and verified too.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

from transcribe_ui.api.ports import Clock
from transcribe_ui.runpod.constants import POD_TERMINATED
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.classes import RunpodKind
from transcribe_ui.runs.constants import DEPLOY_SETTLE_S, IGNORED_QUEUED_POD_STATUSES, POD_KIND, QUEUED_POD_KIND
from transcribe_ui.runs.errors import StopUnverifiedError, runpod_call
from transcribe_ui.runs.responses import QueuedStop

# Told the pod a queued pod deployed, before that pod is terminated (so a run can follow it if
# the termination cannot be verified).
type OnDeployed = Callable[["KnownPod"], None]


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
    """A queued pod stopped: cancelled (or ended), or its deployed pod terminated."""

    outcome: QueuedStop
    pod_id: str | None = None


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


def terminate_verified(pods: PodsApi, pod: KnownPod, clock: Clock) -> None:
    """Terminate the pod and read it again.

    A 404 (on the delete or the read) verifies it only for a pod that was seen (a pod read showed
    it, or the delete was accepted), or one whose deploy has settled; otherwise it may not be
    visible yet.

    Raises:
        StopUnverifiedError: The pod still reads live, or is missing too early to count as gone.
        DomainError: A Runpod call failed (the pod may be live).
    """
    pod_id = pod.pod_id
    existed = runpod_call(lambda: pods.terminate(pod_id))
    # A delete Runpod accepted (204) shows the pod existed: a 404 after it means it is gone.
    seen = KnownPod(pod_id, seen=True) if existed else pod
    if not existed and not seen.gone_when_missing(clock.now()):
        raise StopUnverifiedError(POD_KIND, pod_id)
    found = runpod_call(lambda: pods.get_pod(pod_id))
    if found is None and seen.gone_when_missing(clock.now()):
        return
    if found is None or found.status != POD_TERMINATED:
        raise StopUnverifiedError(POD_KIND, pod_id)


def cancel_queued_verified(pods: PodsApi, queued_pod_id: str, on_deployed: OnDeployed, clock: Clock) -> QueuedStopped:
    """Cancel the queued pod and read it again; terminate (verified) a pod it deployed meanwhile.

    Raises:
        StopUnverifiedError: It still reads waiting, reads deployed without naming its pod, or its
            pod still reads live (or is missing right after the deploy).
        DomainError: A Runpod call failed.
    """
    runpod_call(lambda: pods.cancel_queued(queued_pod_id))
    entry = runpod_call(lambda: pods.get_queued_pod(queued_pod_id))
    if entry is None:
        return QueuedStopped("cancelled")
    pod_id = entry.pod_id
    if pod_id is not None:
        deployed = KnownPod(pod_id, seen=False, deployed_at=entry.resolved_at)
        on_deployed(deployed)
        terminate_verified(pods, deployed, clock)
        return QueuedStopped("pod_terminated", pod_id)
    if entry.status in IGNORED_QUEUED_POD_STATUSES:
        return QueuedStopped("cancelled")
    # QUEUED (the cancel did not take effect), DEPLOYED without its pod's id, or an unknown status.
    raise StopUnverifiedError(QUEUED_POD_KIND, queued_pod_id)
