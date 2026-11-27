"""`cancel_worker`: stop a run's queued pod or pod, verified (plan m5 §1 "Cancel follows a deploy").

A queued pod is cancelled, then read again: if it deployed meanwhile, its pod is terminated and
the termination verified. The run becomes `cancelled` only once that is verified; until then it
stays as it was (a pod learned on the way is recorded and the run follows it as `running`), so
`refresh_status` keeps following it. Under the runs lock.
"""

from datetime import datetime
from functools import partial

from transcribe_ui.api.ports import Clock, RequestContext
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.classes import CancelWorkerRequest
from transcribe_ui.runs.cloud import open_runpod
from transcribe_ui.runs.constants import FINAL_RUN_STATUSES
from transcribe_ui.runs.errors import LaunchUnresolvedError, RunNotActiveError, runpod_call
from transcribe_ui.runs.records import read_run, update_run
from transcribe_ui.runs.responses import CancelWorkerResponse, RunInfo
from transcribe_ui.runs.settling import Listed, resolve_launching
from transcribe_ui.runs.stopping import cancel_queued_verified, terminate_verified
from transcribe_ui.state.store import LocalState


def cancel_worker(request: CancelWorkerRequest, context: RequestContext) -> CancelWorkerResponse:
    """Stop the run's queued pod or pod and mark the run `cancelled` once that is verified.

    Raises:
        RunNotFoundError: The database does not know the run.
        RunNotActiveError: The run has ended already (`cancelled` is answered as done).
        LaunchUnresolvedError: The run is `launching` and its pod is not listed (yet).
        StopUnverifiedError: The stop was sent but does not show yet (the run stays active).
        DomainError: A Runpod call failed.
    """
    ports = context.ports
    state = ports.state
    with state.locks.runs(context.cancel.is_cancelled), open_runpod(context) as client:
        run = read_run(state, request.run_id)
        if run.status == "launching":
            run = _resolve(state, client.pods, run.run_id, ports.clock.now())
        if run.status == "cancelled":
            return CancelWorkerResponse(run=run)
        if run.status in FINAL_RUN_STATUSES:
            raise RunNotActiveError(run.run_id, run.status)
        terminated = _stop(state, client.pods, run, ports.clock)
        cancelled = update_run(state, run.run_id, ports.clock.now(), "cancelled")
    return CancelWorkerResponse(run=cancelled, terminated_pod_id=terminated)


def _resolve(state: LocalState, pods: PodsApi, run_id: str, now: datetime) -> RunInfo:
    listed = runpod_call(lambda: Listed.read(pods))
    run = resolve_launching(state, run_id, listed, now)
    if run.status == "launching":
        raise LaunchUnresolvedError(run_id)
    return run


def _stop(state: LocalState, pods: PodsApi, run: RunInfo, clock: Clock) -> str | None:
    """Stop the run's pod (or queued pod); return the id of the pod terminated, if any."""
    run_id = run.run_id
    if run.pod_id is not None:
        terminate_verified(pods, run.pod_id)
        return run.pod_id
    if run.queued_pod_id is None:
        raise LaunchUnresolvedError(run_id)  # not final, yet neither id is known

    follow = partial(_follow_pod, state, run_id, clock)
    return cancel_queued_verified(pods, run.queued_pod_id, follow).pod_id


def _follow_pod(state: LocalState, run_id: str, clock: Clock, pod_id: str) -> None:
    """Record the pod a queued pod deployed during the cancel; the run follows it until it is terminated."""
    update_run(state, run_id, clock.now(), "running", {"pod_id": pod_id})
