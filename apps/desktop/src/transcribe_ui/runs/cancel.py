"""`cancel_worker`: stop a run's queued pod or pod, verified (plan m5 §1 "Cancel follows a deploy").

A queued pod is cancelled, then read again: if it deployed meanwhile, its pod is terminated and
the termination verified (a queued pod gone after the cancel has its pod looked up by the run's
name, and counts as cancelled only once a pod it deployed just before would be listed). The run
ends only once that is verified (`runs/stopped.py`: `cancelled`, or as its worker reported if it
had finished or failed, the report read before the stop); until then it stays as it was (a pod
learned on the way is recorded and the run follows it as `running`), so `refresh_status` keeps
following it. Under the runs lock.
"""

from datetime import datetime

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.classes import CancelWorkerRequest
from transcribe_ui.runs.cloud import open_runpod
from transcribe_ui.runs.constants import FINAL_RUN_STATUSES
from transcribe_ui.runs.errors import LaunchUnresolvedError, RunNotActiveError, runpod_call
from transcribe_ui.runs.halting import stop_run
from transcribe_ui.runs.records import read_run, update_run
from transcribe_ui.runs.responses import CancelWorkerResponse, RunInfo
from transcribe_ui.runs.settling import Listed, resolve_launching
from transcribe_ui.runs.stopped import last_report, stopped_end
from transcribe_ui.state.store import LocalState


def cancel_worker(request: CancelWorkerRequest, context: RequestContext) -> CancelWorkerResponse:
    """Stop the run's queued pod or pod and end the run once that is verified.

    The run becomes `cancelled`, unless its worker had reported `finished` or `failed` (its report
    read best effort from R2 before the stop): then it ends as the worker reported.

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
        # Read before the stop, so a cancel during the read leaves the run as it was.
        worker = last_report(context, run.run_id)
        terminated = stop_run(state, client.pods, run.run_id, ports.clock)
        now = ports.clock.now()
        status, ended = stopped_end(state, worker, now)
        stopped = update_run(state, run.run_id, now, status, ended)
    return CancelWorkerResponse(run=stopped, terminated_pod_id=terminated)


def _resolve(state: LocalState, pods: PodsApi, run_id: str, now: datetime) -> RunInfo:
    listed = runpod_call(lambda: Listed.read(pods))
    run = resolve_launching(state, run_id, listed, now)
    if run.status == "launching":
        raise LaunchUnresolvedError(run_id)
    return run
