"""Following every run that is not final, once per `refresh_status` (architecture §3.7).

Each run is followed in Runpod (`runs/following.py`); whether its pod is gone and whether its
desktop deadline has passed are decided from Runpod data alone. Then its worker report is read
from R2. A run whose pod is gone ends, which needs the report (`finished` or `failed`): when R2
cannot be read, the run stays as it is (fail closed). A run past its deadline gets its pod
stopped, verified, and fails (`runs/ending.py`) even when R2 cannot be read, so an R2 outage or
missing R2 credentials never keep a billing pod alive; the R2 error is then raised after every
run was handled. The Runpod lists are read once per refresh (`runs/monitor.py`).
"""

from dataclasses import dataclass
from datetime import datetime

from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.cloud import LazyStore
from transcribe_ui.runs.constants import FINAL_RUN_STATUSES
from transcribe_ui.runs.ending import desktop_deadline, end_run, stop_at_deadline
from transcribe_ui.runs.errors import runpod_call
from transcribe_ui.runs.following import follow_run
from transcribe_ui.runs.records import open_runs, read_payload, update_run
from transcribe_ui.runs.settling import Listed
from transcribe_ui.runs.worker import WorkerReport, read_worker, worker_progress
from transcribe_ui.state.store import LocalState


@dataclass(frozen=True)
class Refreshed:
    """One run after its refresh: the worker's report, a deadline stop that does not show yet, an R2 failure."""

    run_id: str
    worker: WorkerReport | None
    stop_error: str | None = None
    # The error that kept the worker report from being read (R2 or its credentials).
    failure: DomainError | None = None
    # Whether R2 was read for the worker report (False: not followed that far, or R2 failed).
    read: bool = False


def refresh_runs(context: RequestContext, pods: PodsApi, store: LazyStore, listed: Listed | None) -> list[Refreshed]:
    """Follow every run that is not final, oldest first, by the lists read once; end or stop those whose time came.

    `listed` is None only when no run was open when the caller looked.

    Raises:
        DomainError: The Runpod lists or a lookup could not be read; the runs followed before
            stay recorded. R2 failures are returned in `Refreshed.failure` instead.
    """
    before = open_runs(context.ports.state)
    if not before:
        return []
    if listed is None:
        listed = runpod_call(lambda: Listed.read(pods))
    refreshed = []
    for run in before:
        each = _refresh_run(context, pods, store, run.run_id, listed)
        _record(context.ports.state, each, context.ports.clock.now())
        refreshed.append(each)
    return refreshed


def _record(state: LocalState, refreshed: Refreshed, now: datetime) -> None:
    """Store what the refresh saw of the run: when, the deadline stop's error, the worker report if read.

    `list_runs` shows these after a restart; a report not read this time keeps the last one.
    """
    changes: dict[str, object] = {"refreshed_at": now, "stop_error": refreshed.stop_error}
    if refreshed.read:
        worker = refreshed.worker
        changes["worker"] = None if worker is None else worker_progress(state, worker)
    update_run(state, refreshed.run_id, now, changes=changes)


def refresh_open_runs(
    context: RequestContext, pods: PodsApi, store: LazyStore, listed: Listed | None
) -> list[Refreshed]:
    """Follow the open runs (`refresh_runs`); raise the first R2 failure once every run was handled.

    Raises:
        DomainError: Runpod or R2 (or the R2 credentials) failed.
    """
    refreshed = refresh_runs(context, pods, store, listed)
    failures = [each.failure for each in refreshed if each.failure is not None]
    if failures:
        raise failures[0]
    return refreshed


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
    read = failure is None
    if followed.gone:
        if read:
            end_run(state, run_id, worker, clock.now())
        return Refreshed(run_id, worker, failure=failure, read=read)
    if not past_deadline:
        return Refreshed(run_id, worker, failure=failure, read=read)
    stop_error = stop_at_deadline(state, pods, run_id, worker, clock)
    return Refreshed(run_id, worker, stop_error, failure, read=read)


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
