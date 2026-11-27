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
from transcribe_ui.runs.errors import runpod_call
from transcribe_ui.runs.records import open_run_id, read_run, update_run
from transcribe_ui.runs.settling import Listed, deployed_pod_changes, live_pod_named, queue_pod, queue_waiting
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
    still_live = _follow_other_named(context, pods, run_id, stop[1])
    return end_stopped_run(context, run_id, stop, worker, stopped_now=stopped_now and not still_live)


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
    still_live = _follow_other_named(context, pods, run_id, stop[1])
    stopped_now = stopped.stopped_live and not still_live
    return stopped, end_stopped_run(context, run_id, stop, worker, stopped_now=stopped_now)


def _report_of_open_run(context: RequestContext, run_id: str | None) -> WorkerReport | None:
    """Return the worker report of an open local run (best effort); None for no run or a final one."""
    open_id = open_run_id(context.ports.state, run_id)
    return None if open_id is None else last_report(context, open_id)


def _follow_other_named(context: RequestContext, pods: PodsApi, run_id: str | None, stopped: frozenset[str]) -> bool:
    """Keep the open local run open while something of it may still bill; return whether it stays open.

    A run never becomes final while a pod carrying its name may still bill (another pod its
    queued pod deployed, say) or its queued pod still waits (`QUEUED`). The run keeps the pod it
    follows when that pod was not stopped and is still listed live; otherwise it follows the
    live pod under its name, or keeps waiting for its queued pod (followed by `refresh_status`).

    Raises:
        DomainError: The lists could not be read (the run stays as it is).
    """
    state = context.ports.state
    open_id = open_run_id(state, run_id)
    if open_id is None:
        return False
    run = read_run(state, open_id)
    listed = Listed(runpod_call(pods.list_pods), runpod_call(pods.list_queued_pods))
    followed = run.pod_id
    if followed is not None and followed not in stopped:
        # Not stopped and not listed `TERMINATED` (live, or not listed yet): kept; whether it is
        # gone is `refresh_status`'s to decide by the `KnownPod` rules.
        named = [pod for pod in listed.pods if pod.pod_id == followed]
        if all(pod.status != POD_TERMINATED for pod in named):
            return True
    now = context.ports.clock.now()
    other = live_pod_named(listed, run.pod_name, stopped)
    if other is not None:
        changes = {"pod_id": other.pod_id, "pod_seen": True, "pod_status": other.status}
        update_run(state, open_id, now, "running", changes)
        return True
    deployed = queue_pod(listed, run.queued_pod_id, stopped)
    if deployed is not None:
        update_run(state, open_id, now, "running", deployed_pod_changes(deployed))
        return True
    return queue_waiting(listed, run.queued_pod_id)
