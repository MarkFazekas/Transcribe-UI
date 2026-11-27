"""Ending a run: its pod is gone, or the desktop deadline passed (plan m5 §1 "Run deadline and wait").

A run ends only once its pod is gone for sure (`runs/following.py`): `finished` when the worker
reported `finished`, `failed` otherwise. Past the desktop deadline (the first `RUNNING` sighting
+ `MAX_RUNTIME_S` + `DESKTOP_DEADLINE_MARGIN_S`, capped by the pod name deadline, which is also
the deadline of a pod never seen running) the pod is terminated, or the queued pod cancelled,
with the same verification as `cancel_worker` (`runs/halting.py`); only a verified stop makes
the run `failed`. A stop that does not show yet leaves the run followed; the next refresh tries
again.
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from transcribe_ui.api.ports import Clock
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.classes import EndReason
from transcribe_ui.runs.constants import DESKTOP_DEADLINE_MARGIN_S, WORKER_FAILED, WORKER_FINISHED
from transcribe_ui.runs.errors import LaunchUnresolvedError, StopUnverifiedError
from transcribe_ui.runs.halting import stop_run
from transcribe_ui.runs.payloads import RunPayload
from transcribe_ui.runs.records import update_run
from transcribe_ui.runs.worker import WorkerReport
from transcribe_ui.state.store import LocalState

if TYPE_CHECKING:
    from transcribe_ui.state.models import RunStatus


def desktop_deadline(payload: RunPayload) -> datetime:
    """Return the moment after which the desktop stops the run's pod."""
    first = payload.first_running_at
    if first is None:
        return payload.name_deadline
    limit = first + timedelta(seconds=payload.max_runtime_s + DESKTOP_DEADLINE_MARGIN_S)
    return min(limit, payload.name_deadline)


def end_run(state: LocalState, run_id: str, worker: WorkerReport | None, now: datetime) -> None:
    """End a run whose pod is gone: `finished` if the worker reported so, else `failed`."""
    state_reported = None if worker is None else worker.state
    status: RunStatus = "finished" if state_reported == WORKER_FINISHED else "failed"
    reason: EndReason = "pod_gone"
    if state_reported == WORKER_FINISHED:
        reason = "worker_finished"
    elif state_reported == WORKER_FAILED:
        reason = "worker_failed"
    update_run(state, run_id, now, status, end_changes(reason, worker, now))


def stop_at_deadline(
    state: LocalState,
    pods: PodsApi,
    run_id: str,
    worker: WorkerReport | None,
    clock: Clock,
) -> str | None:
    """Stop the run's pod (or queued pod), verified, and fail the run; return the error code if the stop does not show.

    Raises:
        DomainError: A Runpod call failed (`cloud_unreachable` or a key error); the run stays.
    """
    try:
        stop_run(state, pods, run_id, clock)
    except (StopUnverifiedError, LaunchUnresolvedError) as error:
        return error.code
    now = clock.now()
    update_run(state, run_id, now, "failed", end_changes("deadline", worker, now))
    return None


def end_changes(reason: EndReason, worker: WorkerReport | None, now: datetime) -> dict[str, object]:
    """Return the payload fields that record a run's end (with the worker's report when readable)."""
    changes: dict[str, object] = {"ended_at": now, "end_reason": reason}
    if worker is not None and worker.status is not None:
        changes["final_status"] = worker.status
    return changes
