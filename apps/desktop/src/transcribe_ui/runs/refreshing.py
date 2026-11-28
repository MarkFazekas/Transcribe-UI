"""Following every run that is not final, once per `refresh_status` (architecture §3.7).

Each run is followed in Runpod (`runs/following.py`); whether its pod is gone and whether its
desktop deadline has passed are decided from Runpod data alone. Then its worker report is read
from R2. A run whose pod is gone ends, which needs the report (`finished` or `failed`): when R2
cannot be read, the run stays as it is (fail closed). A run past its deadline gets its pod
stopped, verified, and fails (`runs/ending.py`) even when R2 cannot be read, so an R2 outage or
missing R2 credentials never keep a billing pod alive; the R2 error is then raised after every
run was handled, and so is a deadline stop whose Runpod call failed (recorded as the run's
`stop_error`; the next refresh tries again). The Runpod lists are read once per refresh
(`runs/monitor.py`).
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
from transcribe_ui.runs.following import Followed, follow_run
from transcribe_ui.runs.records import open_runs, read_payload, update_run
from transcribe_ui.runs.settling import Listed, deployed_pod_changes, live_pod_named, queue_pod, queue_waiting
from transcribe_ui.runs.worker import WorkerReport, read_worker, stored_report
from transcribe_ui.state.store import LocalState


@dataclass(frozen=True)
class Refreshed:
    """One run after its refresh: the worker's report, a deadline stop that does not show yet, an R2 failure."""

    run_id: str
    worker: WorkerReport | None
    stop_error: str | None = None
    # The error to raise once every run was handled: R2 (or its credentials) kept the worker report
    # from being read, or the deadline stop's Runpod call failed.
    failure: DomainError | None = None
    # Whether R2 was read for the worker report (False: not followed that far, or R2 failed).
    read: bool = False


def refresh_runs(context: RequestContext, pods: PodsApi, store: LazyStore, listed: Listed | None) -> list[Refreshed]:
    """Follow every run that is not final, oldest first, by the lists read once; end or stop those whose time came.

    `listed` is None only when no run was open when the caller looked.

    Raises:
        DomainError: The Runpod lists or a lookup could not be read; the runs followed before
            stay recorded. R2 failures and a failed deadline stop are returned in
            `Refreshed.failure` instead (the stop's error code also in `stop_error`).
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

    `list_runs` shows these after a restart; a report not read this time keeps the last one. The
    remaining time and finish of the last refresh are cleared: the refresh computes them again
    once the results are recorded (`runs/remaining.py`), and a refresh that fails before leaves
    them unknown rather than stale next to the new refresh time.
    """
    changes: dict[str, object] = {"refreshed_at": now, "stop_error": refreshed.stop_error}
    changes.update(remaining_estimate_s=None, estimated_finish_at=None)
    if refreshed.read:
        worker = refreshed.worker
        changes["worker"] = None if worker is None else stored_report(state, worker)
    update_run(state, refreshed.run_id, now, changes=changes)


def refresh_open_runs(
    context: RequestContext, pods: PodsApi, store: LazyStore, listed: Listed | None
) -> list[Refreshed]:
    """Follow the open runs (`refresh_runs`); raise the first failure kept per run once every run was handled.

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
    if followed.gone and not _follow_other_pod(state, followed, listed, clock.now()):
        if read:
            end_run(state, run_id, worker, clock.now())
        return Refreshed(run_id, worker, failure=failure, read=read)
    # Kept open (another pod under its name, or its queued pod) or still running: stopped at the
    # deadline all the same, which also cancels its queued pod again.
    if not past_deadline:
        return Refreshed(run_id, worker, failure=failure, read=read)
    try:
        stop_error = stop_at_deadline(state, pods, run_id, worker, clock)
    except CancelledError:
        raise
    except DomainError as error:
        # The stop could not be sent or checked: recorded as the run's stop error, raised once
        # every run was handled (the run stays followed; the next refresh tries again).
        return Refreshed(run_id, worker, error.code, error, read=read)
    return Refreshed(run_id, worker, stop_error, failure, read=read)


def _follow_other_pod(state: LocalState, followed: Followed, listed: Listed, now: datetime) -> bool:
    """Keep a run whose pod is gone open while something of it may still bill; return whether it stays open.

    A run never becomes final while a pod carrying its name may still bill (a queued pod that
    deployed more than one, a stop cut short between pods): it follows such a pod until it too
    is gone, or stopped at the deadline. Nor while its queued pod names a pod that may still
    bill (one the pod list does not show yet: followed as not seen, so `KnownPod` settles it), or
    still waits (`QUEUED`, it may still deploy): the run then stays as it is, and a pod it
    deploys is adopted by its name.
    """
    run = followed.run
    gone = frozenset(() if run.pod_id is None else (run.pod_id,))
    other = live_pod_named(listed, run.pod_name, gone)
    if other is not None:
        changes = {"pod_id": other.pod_id, "pod_seen": True, "pod_status": other.status}
        update_run(state, run.run_id, now, "running", changes)
        return True
    deployed = queue_pod(listed, run.queued_pod_id, gone)
    if deployed is not None:
        update_run(state, run.run_id, now, "running", deployed_pod_changes(deployed))
        return True
    return queue_waiting(listed, run.queued_pod_id)


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
