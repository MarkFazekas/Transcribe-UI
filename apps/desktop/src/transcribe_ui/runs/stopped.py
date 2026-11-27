"""How a run the user stopped ends (`cancel_worker`, `terminate_pod`, `cancel_queued_pod`).

Once the stop is verified the run is final. It is `cancelled`, unless its worker had already
reported `finished` or `failed` (its report read best effort from R2 before the stop, so a cancel
during that read leaves nothing stopped): then it ends as the worker reported, so a run whose
work was done is not recorded as cancelled.
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runs.cloud import LazyStore
from transcribe_ui.runs.constants import WORKER_FAILED, WORKER_FINISHED
from transcribe_ui.runs.ending import end_changes
from transcribe_ui.runs.records import PayloadChanges, cancel_local_run, read_run, reopen_collection, run_statuses
from transcribe_ui.runs.worker import WorkerReport, read_worker, stored_report
from transcribe_ui.state.models import RunStatus
from transcribe_ui.state.store import LocalState

if TYPE_CHECKING:
    from transcribe_ui.runs.classes import EndReason

logger = logging.getLogger(__name__)

# How a run ends: its final status and the payload fields that record the end.
type RunEnd = tuple[RunStatus, dict[str, object]]


def stopped_end(state: LocalState, worker: WorkerReport | None, now: datetime) -> RunEnd:
    """Return how a run the user stopped ends: as its worker reported if it had finished or failed, else `cancelled`.

    A worker that wrote `finished` (or `failed`) before the stop did its work: the run is not
    recorded as cancelled. The report read is kept in the payload.
    """
    reported = None if worker is None else worker.state
    status: RunStatus = "cancelled"
    reason: EndReason = "cancelled"
    if reported == WORKER_FINISHED:
        status, reason = "finished", "worker_finished"
    elif reported == WORKER_FAILED:
        status, reason = "failed", "worker_failed"
    changes = end_changes(reason, worker, now)
    if worker is not None:
        changes["worker"] = stored_report(state, worker)
    return status, changes


def last_report(context: RequestContext, run_id: str) -> WorkerReport | None:
    """Return the run's worker report, best effort: None when there is none or R2 cannot be read (logged).

    Raises:
        CancelledError: The request was cancelled.
    """
    try:
        return read_worker(LazyStore(context).get(), run_id)
    except CancelledError:
        raise
    except DomainError as error:
        logger.info("Run %s: its worker report could not be read (%s); a stop records it cancelled", run_id, error.code)
        return None


def end_stopped_run(
    context: RequestContext,
    run_id: str | None,
    stop: tuple[PayloadChanges, frozenset[str]],
    worker: WorkerReport | None,
    *,
    stopped_now: bool,
) -> RunStatus | None:
    """End the local run the stopped pod belonged to, or mark its objects for collection again; return its status.

    `stop` is the payload fields of the stop and the ids it stopped (verified); `worker` is the
    run's report as read before the stop (`last_report`). A pod or queued pod that had ended
    before this request stopped nothing: its run is left to `refresh_status`, which reads the
    worker's report (a finished run is not recorded as cancelled). Otherwise the run ends as
    `cancel_worker` ends it (`stopped_end`).
    """
    ports = context.ports
    state = ports.state
    reopen_collection(state, run_id, ports.clock.now())
    if run_id is None:
        return None
    if not stopped_now:
        known = run_id in run_statuses(state)
        return read_run(state, run_id).status if known else None
    now = ports.clock.now()
    return cancel_local_run(state, run_id, now, stop, stopped_end(state, worker, now))
