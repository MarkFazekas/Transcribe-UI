"""The stops of `terminate_pod` and `cancel_queued_pod` and what they record on the local run.

A pod a queued pod deployed is recorded on the run that recorded the queued pod (so the run
follows it should its stop not show, and stopping it by id ends the run); a pod read that shows
the run's pod marks it seen. The worker's report is read before the stop; the run then ends as
`runs/stopped.py` decides, or is left to `refresh_status` when the stop found nothing live.
"""

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.constants import POD_TERMINATED
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runpod.wire import Pod, QueuedPod
from transcribe_ui.runs.records import open_run_id
from transcribe_ui.runs.stop_records import adopt_deployed, queued_stop
from transcribe_ui.runs.stopped import end_stopped_run, last_report
from transcribe_ui.runs.stopping import KnownPod, QueuedStopped, cancel_queued_verified, terminate_verified
from transcribe_ui.runs.worker import WorkerReport
from transcribe_ui.state.models import RunStatus


def stop_app_pod(context: RequestContext, pods: PodsApi, run_id: str | None, pod: Pod) -> RunStatus | None:
    """Terminate one of the app's pods as read (verified) and end its local run; return the run's status.

    The worker's report is read before the stop, so a cancel during that read leaves nothing
    stopped. A pod its run knows only through the run's queued pod is recorded on the run first
    (`adopt_deployed`). A pod that reads `TERMINATED` already is not stopped again, and its run
    is left to `refresh_status`.

    Raises:
        StopUnverifiedError: The pod still reads live after the terminate.
        DomainError: A Runpod call failed.
    """
    worker = _report_of_open_run(context, run_id)
    pod_id = pod.pod_id
    adopt_deployed(context, pods, run_id, pod_id)
    stopped_now = pod.status != POD_TERMINATED
    if stopped_now:
        terminate_verified(pods, KnownPod(pod_id, seen=True), context.ports.clock)
    stop = ({"pod_id": pod_id, "pod_seen": True}, frozenset((pod_id,)))
    return end_stopped_run(context, run_id, stop, worker, stopped_now=stopped_now)


def stop_app_queued_pod(
    context: RequestContext,
    pods: PodsApi,
    run_id: str | None,
    entry: QueuedPod,
) -> tuple[QueuedStopped, RunStatus | None]:
    """Cancel one of the app's queued pods as read, terminating what it deployed (verified); end its local run.

    Returns the stop and the run's status. The worker's report is read before the stop. A pod
    the queued pod deployed is recorded on its run before it is terminated, and a pod read that
    shows it marks it seen there. A stop that found nothing live (the queued pod had ended, its
    pod was terminated already) leaves the run to `refresh_status`.

    Raises:
        StopUnverifiedError: The stop does not show after the follow-up read.
        DomainError: A Runpod call failed.
    """
    worker = _report_of_open_run(context, run_id)
    queued_id = entry.queued_pod_id
    target, hooks = queued_stop(context, run_id, entry)
    stopped = cancel_queued_verified(pods, target, context.ports.clock, hooks)
    changes = {"queued_pod_id": queued_id, "pod_id": stopped.pod_id}
    stop = (changes, frozenset((queued_id, *stopped.pod_ids)))
    return stopped, end_stopped_run(context, run_id, stop, worker, stopped_now=stopped.stopped_live)


def _report_of_open_run(context: RequestContext, run_id: str | None) -> WorkerReport | None:
    """Return the worker report of an open local run (best effort); None for no run or a final one."""
    open_id = open_run_id(context.ports.state, run_id)
    return None if open_id is None else last_report(context, open_id)
