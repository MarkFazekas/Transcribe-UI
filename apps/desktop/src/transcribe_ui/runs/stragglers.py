"""`terminate_pod` and `cancel_queued_pod`: stop one of the app's pods or queued pods by id.

For stragglers and overdue entries that `reconcile_cloud` reports. Each needs `confirm` (the
first answer names the pod and its run), refuses pods that are not the app's, and verifies the
stop with a follow-up read. A local run of that id that is not final ends as `cancel_worker` ends
it (`runs/stopped.py`), only when the stop covered the pod it recorded; a final one whose run
objects were collected is marked uncollected, since the stopped pod may have written some again
(the next `refresh_status` collects them). Under the runs lock.
"""

from functools import partial
from typing import TYPE_CHECKING

from transcribe_contracts.pods import is_app_pod_name, parse_pod_name
from transcribe_ui.api.classes import Confirmation, NeedsConfirmation
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.constants import POD_TERMINATED
from transcribe_ui.runs.classes import CancelQueuedPodRequest, TerminatePodRequest
from transcribe_ui.runs.cloud import open_runpod
from transcribe_ui.runs.constants import POD_KIND, QUEUED_POD_KIND
from transcribe_ui.runs.errors import NotAppPodError, PodNotFoundError, runpod_call
from transcribe_ui.runs.responses import CancelQueuedPodResponse, TerminatePodResponse
from transcribe_ui.runs.stopped import end_stopped_run, follow_deployed
from transcribe_ui.runs.stopping import (
    CloudObject,
    KnownPod,
    QueuedTarget,
    cancel_queued_verified,
    ended_without_pod,
    terminate_verified,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

# Request field that confirms a stop.
CONFIRM_FIELD = "confirm"


def terminate_pod(request: TerminatePodRequest, context: RequestContext) -> TerminatePodResponse | NeedsConfirmation:
    """Terminate one of the app's pods (verified); its local run, if not final, becomes `cancelled`.

    Raises:
        PodNotFoundError: Runpod does not know the pod.
        NotAppPodError: The pod is not one of the app's.
        StopUnverifiedError: The pod still reads live after the terminate.
        DomainError: A Runpod call failed.
    """
    ports = context.ports
    pod_id = request.pod_id
    with ports.state.locks.runs(context.cancel.is_cancelled), open_runpod(context) as client:
        pods = client.pods
        pod = runpod_call(lambda: pods.get_pod(pod_id))
        if pod is None:
            raise PodNotFoundError(POD_KIND, pod_id)
        found = CloudObject(POD_KIND, pod_id, pod.name, pod.status)
        run_id = _app_run_id(found)
        if not request.confirm:
            return _confirmation(request.action, found, run_id)
        stopped_now = pod.status != POD_TERMINATED
        if stopped_now:
            terminate_verified(pods, KnownPod(pod_id, seen=True), ports.clock)
        local = end_stopped_run(context, run_id, {"pod_id": pod_id, "pod_seen": True}, stopped_now=stopped_now)
    return TerminatePodResponse(pod_id=pod_id, name=pod.name, run_id=run_id, local_status=local)


def cancel_queued_pod(
    request: CancelQueuedPodRequest,
    context: RequestContext,
) -> CancelQueuedPodResponse | NeedsConfirmation:
    """Cancel one of the app's queued pods, terminating a pod it deployed meanwhile (verified).

    Raises:
        PodNotFoundError: Runpod does not know the queued pod.
        NotAppPodError: The queued pod is not one of the app's.
        StopUnverifiedError: The stop does not show after the follow-up read.
        DomainError: A Runpod call failed.
    """
    ports = context.ports
    queued_id = request.queued_pod_id
    with ports.state.locks.runs(context.cancel.is_cancelled), open_runpod(context) as client:
        pods = client.pods
        entry = runpod_call(lambda: pods.get_queued_pod(queued_id))
        if entry is None:
            raise PodNotFoundError(QUEUED_POD_KIND, queued_id)
        found = CloudObject(QUEUED_POD_KIND, queued_id, entry.name, entry.status)
        run_id = _app_run_id(found)
        if not request.confirm:
            return _confirmation(request.action, found, run_id)
        stopped = cancel_queued_verified(
            pods,
            QueuedTarget.read_from(entry),
            ports.clock,
            partial(follow_deployed, context, run_id, queued_id),
        )
        changes = {"queued_pod_id": queued_id, "pod_id": stopped.pod_id}
        local = end_stopped_run(context, run_id, changes, stopped_now=not ended_without_pod(entry))
    return CancelQueuedPodResponse(
        queued_pod_id=queued_id,
        name=entry.name,
        outcome=stopped.outcome,
        terminated_pod_id=stopped.pod_id,
        run_id=run_id,
        local_status=local,
    )


def _app_run_id(found: CloudObject) -> str | None:
    """Return the run id in an app name (None if it does not parse).

    Raises:
        NotAppPodError: The name is not one of the app's.
    """
    if not is_app_pod_name(found.name):
        raise NotAppPodError(found.kind, found.object_id)
    parsed = parse_pod_name(found.name)
    return None if parsed is None else parsed.run_id


def _confirmation(action: str, found: CloudObject, run_id: str | None) -> NeedsConfirmation:
    what = "Terminate the pod" if found.kind == POD_KIND else "Cancel the queued pod"
    details: dict[str, JsonValue] = {"kind": found.kind, "object_id": found.object_id, "name": found.name}
    details.update({"status": found.status, "run_id": run_id})
    message = (
        f"{what} {found.name} ({found.status})? A worker it runs stops at once; finished results stay in the cloud."
    )
    confirmation = Confirmation(field=CONFIRM_FIELD, set_to=True, message=message, details=details)
    return NeedsConfirmation(action=action, confirmations=(confirmation,))
