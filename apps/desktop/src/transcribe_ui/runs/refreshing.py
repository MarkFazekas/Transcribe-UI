"""Following every run that is not final, once per `refresh_status` (architecture §3.7).

Each run is followed in Runpod (`runs/following.py`); whether its pod is gone and whether its
desktop deadline has passed are decided from Runpod data alone. Then its worker report is read
from R2. A run whose pod is gone ends, which needs the report (`finished` or `failed`): when R2
cannot be read, the run stays as it is (fail closed). A run past its deadline gets its pod
stopped, verified, and fails (`runs/ending.py`) even when R2 cannot be read, so an R2 outage or
missing R2 credentials never keep a billing pod alive; the R2 error is then raised after every
run was handled. The Runpod lists are read once, and only when such a run exists.
"""

from dataclasses import dataclass

from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.cloud import LazyStore
from transcribe_ui.runs.constants import FINAL_RUN_STATUSES
from transcribe_ui.runs.ending import desktop_deadline, end_run, stop_at_deadline
from transcribe_ui.runs.errors import runpod_call
from transcribe_ui.runs.following import follow_run
from transcribe_ui.runs.records import open_runs, read_payload
from transcribe_ui.runs.settling import Listed
from transcribe_ui.runs.worker import WorkerReport, read_worker


@dataclass(frozen=True)
class Refreshed:
    """One run after its refresh: the worker's report, a deadline stop that does not show yet, an R2 failure."""

    run_id: str
    worker: WorkerReport | None
    stop_error: str | None = None
    # The error that kept the worker report from being read (R2 or its credentials).
    failure: DomainError | None = None


def refresh_runs(context: RequestContext, pods: PodsApi, store: LazyStore) -> list[Refreshed]:
    """Follow every run that is not final, oldest first; end or stop those whose time came.

    Raises:
        DomainError: The Runpod lists or a lookup could not be read; the runs followed before
            stay recorded. R2 failures are returned in `Refreshed.failure` instead.
    """
    before = open_runs(context.ports.state)
    if not before:
        return []
    listed = runpod_call(lambda: Listed.read(pods))
    return [_refresh_run(context, pods, store, run.run_id, listed) for run in before]


def first_failure(refreshed: list[Refreshed]) -> DomainError | None:
    """Return the first R2 failure of the refreshed runs, if any."""
    failures = [each.failure for each in refreshed if each.failure is not None]
    return failures[0] if failures else None


def _refresh_run(context: RequestContext, pods: PodsApi, store: LazyStore, run_id: str, listed: Listed) -> Refreshed:
    ports = context.ports
    state = ports.state
    clock = ports.clock
    followed = follow_run(state, pods, run_id, listed, clock.now())
    status = followed.run.status
    if status in FINAL_RUN_STATUSES or status == "launching":
        return Refreshed(run_id, None)
    past_deadline = clock.now() >= desktop_deadline(read_payload(state, run_id))
    worker, failure = _read_report(store, run_id)
    if followed.gone:
        if failure is None:
            end_run(state, run_id, worker, clock.now())
        return Refreshed(run_id, worker, failure=failure)
    if not past_deadline:
        return Refreshed(run_id, worker, failure=failure)
    stop_error = stop_at_deadline(state, pods, run_id, worker, clock)
    return Refreshed(run_id, worker, stop_error, failure)


def _read_report(store: LazyStore, run_id: str) -> tuple[WorkerReport | None, DomainError | None]:
    """Return the run's worker report, or the R2 (or R2 credentials) error that kept it from being read.

    Raises:
        CancelledError: The request was cancelled.
    """
    try:
        return read_worker(store.get(), run_id), None
    except CancelledError:
        raise
    except DomainError as error:
        return None, error
