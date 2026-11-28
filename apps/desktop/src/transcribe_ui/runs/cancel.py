"""`cancel_worker`: stop a run's queued pod or pod, verified (plan m5 §1 "Cancel follows a deploy").

A queued pod is cancelled, then read again: if it deployed meanwhile, its pod is terminated and
the termination verified (a queued pod gone after the cancel has its pod looked up by the run's
name, and counts as cancelled only once a pod it deployed just before would be listed). The run
ends only once that is verified (`runs/stopped.py`: `cancelled`, or as its worker reported if it
had finished or failed, the report read before the stop); until then it stays as it was (a pod
learned on the way is recorded and the run follows it as `running`), so `refresh_status` keeps
following it. Under the runs lock.

Without `confirm` the request only reads the database (no lock, no Runpod call): a run that is
not final answers `needs_confirmation` with its stage (Q34: finished subtitles are kept, the
files not done yet wait for the next start), an ended one is refused as below, and nothing is
changed.
"""

from transcribe_ui.api.classes import NeedsConfirmation
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runs.cancel_asking import cancel_asked
from transcribe_ui.runs.classes import CancelWorkerRequest
from transcribe_ui.runs.cloud import open_runpod
from transcribe_ui.runs.constants import FINAL_RUN_STATUSES
from transcribe_ui.runs.errors import RunNotActiveError
from transcribe_ui.runs.halting import stop_run
from transcribe_ui.runs.lease_release import releasing_leases
from transcribe_ui.runs.records import read_run, update_run
from transcribe_ui.runs.responses import CancelWorkerResponse
from transcribe_ui.runs.settling import resolve_for_stop
from transcribe_ui.runs.stopped import last_report, stopped_end


def cancel_worker(
    request: CancelWorkerRequest,
    context: RequestContext,
) -> CancelWorkerResponse | NeedsConfirmation:
    """Stop the run's queued pod or pod and end the run once that is verified (asked first without `confirm`).

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
    if not request.confirm:
        return cancel_asked(state, request)
    with state.locks.runs(context.cancel.is_cancelled), releasing_leases(context):
        return _confirmed_cancel(context, request.run_id)


def _confirmed_cancel(context: RequestContext, run_id: str) -> CancelWorkerResponse:
    """Stop the run's pod (verified) and end the run; the caller holds the runs lock."""
    ports = context.ports
    state = ports.state
    with open_runpod(context) as client:
        run = read_run(state, run_id)
        if run.status == "launching":
            run = resolve_for_stop(state, client.pods, run.run_id, ports.clock.now())
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
