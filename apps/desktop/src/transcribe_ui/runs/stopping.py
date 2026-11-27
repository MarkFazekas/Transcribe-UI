"""Stopping pods and queued pods, each verified by a follow-up read (plan m5 §1 "Cancel follows a deploy").

A terminate counts once the pod reads `TERMINATED` or is unknown (404 after it was seen: gone).
A cancel counts once the queued pod reads `CANCELLED`, `EXPIRED` or `FAILED` (none of them leads
to a pod), or is unknown; when it names a pod, it deployed during the cancel, and that pod is
terminated and verified too.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from transcribe_ui.runpod.constants import POD_TERMINATED
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.classes import RunpodKind
from transcribe_ui.runs.constants import IGNORED_QUEUED_POD_STATUSES, POD_KIND, QUEUED_POD_KIND
from transcribe_ui.runs.errors import StopUnverifiedError, runpod_call
from transcribe_ui.runs.responses import QueuedStop

# Told the id of the pod a queued pod deployed, before that pod is terminated (so a run can
# follow it if the termination cannot be verified).
type OnDeployed = Callable[[str], None]


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


def terminate_verified(pods: PodsApi, pod_id: str) -> None:
    """Terminate the pod and read it again.

    Raises:
        StopUnverifiedError: The pod still reads live.
        DomainError: A Runpod call failed (the pod may be live).
    """
    runpod_call(lambda: pods.terminate(pod_id))
    found = runpod_call(lambda: pods.get_pod(pod_id))
    if found is not None and found.status != POD_TERMINATED:
        raise StopUnverifiedError(POD_KIND, pod_id)


def cancel_queued_verified(pods: PodsApi, queued_pod_id: str, on_deployed: OnDeployed) -> QueuedStopped:
    """Cancel the queued pod and read it again; terminate (verified) a pod it deployed meanwhile.

    Raises:
        StopUnverifiedError: It still reads waiting, reads deployed without naming its pod, or its
            pod still reads live.
        DomainError: A Runpod call failed.
    """
    runpod_call(lambda: pods.cancel_queued(queued_pod_id))
    entry = runpod_call(lambda: pods.get_queued_pod(queued_pod_id))
    if entry is None:
        return QueuedStopped("cancelled")
    pod_id = entry.pod_id
    if pod_id is not None:
        on_deployed(pod_id)
        terminate_verified(pods, pod_id)
        return QueuedStopped("pod_terminated", pod_id)
    if entry.status in IGNORED_QUEUED_POD_STATUSES:
        return QueuedStopped("cancelled")
    # QUEUED (the cancel did not take effect), DEPLOYED without its pod's id, or an unknown status.
    raise StopUnverifiedError(QUEUED_POD_KIND, queued_pod_id)
