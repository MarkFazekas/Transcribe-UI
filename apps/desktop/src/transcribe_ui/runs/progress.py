"""A run as the frontends show it: stage, worker report, deadline, elapsed time and cost so far.

Built from the stored run only (`refresh_status` stores the worker report, the deadline stop's
error and the refresh time; `runs/refreshing.py`), so `refresh_status` and `list_runs` show a run
the same way. Elapsed time counts from the pod's creation (`createdAt`, else its first sighting)
until the run ended, else until its last refresh, never until the moment of the request; the cost
so far is that time at the pod's price per hour (`cost`). Both are estimates for the display;
Runpod's own bill is what counts.
"""

from collections.abc import Sequence
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select

from transcribe_ui.runs.constants import (
    COST_SO_FAR_DECIMALS,
    SECONDS_PER_HOUR,
    UNREADABLE_STATE,
    WORKER_STARTING,
    WORKER_TRANSCRIBING,
)
from transcribe_ui.runs.ending import desktop_deadline
from transcribe_ui.runs.payloads import RunPayload, StoredWorkerReport
from transcribe_ui.runs.records import load_run, payload_of, run_info
from transcribe_ui.runs.responses import RunInfo, RunProgress, RunStage, WorkerProgress
from transcribe_ui.runs.worker_logs import worker_log_path
from transcribe_ui.state.models import RunRow
from transcribe_ui.state.store import LocalState

# A run as stored: what the frontends see of it, and its payload.
type StoredRun = tuple[RunInfo, RunPayload]


def runs_progress(state: LocalState, run_ids: Sequence[str]) -> tuple[RunProgress, ...]:
    """Return each run as stored now, in the given order."""
    with state.session() as session:
        stored = [_stored(load_run(session, run_id)) for run_id in run_ids]
    return tuple(_progress(state, run, payload) for run, payload in stored)


def latest_runs(state: LocalState, limit: int) -> tuple[RunProgress, ...]:
    """Return the latest runs as stored, newest first (at most `limit`)."""
    newest_first = (RunRow.created_at.desc(), RunRow.run_id.desc())
    query = select(RunRow).order_by(*newest_first).limit(limit)
    with state.session() as session:
        stored = [_stored(row) for row in session.scalars(query)]
    return tuple(_progress(state, run, payload) for run, payload in stored)


def _stored(row: RunRow) -> StoredRun:
    return run_info(row), payload_of(row)


def _progress(state: LocalState, run: RunInfo, payload: RunPayload) -> RunProgress:
    """Return the run as the frontends show it (the log is looked for on disk, outside any transaction)."""
    until = payload.ended_at or payload.refreshed_at
    elapsed = _elapsed_s(payload, until)
    price = payload.price_per_hour_usd
    log = worker_log_path(state.paths.log_dir, run.run_id)
    kept_log = payload.objects_collected and log.is_file()
    return RunProgress(
        run=run,
        stage=_stage(run, payload.worker),
        pod_status=payload.pod_status,
        worker=_shown(payload.worker),
        first_running_at=payload.first_running_at,
        deadline=desktop_deadline(payload),
        elapsed_s=elapsed,
        price_per_hour_usd=price,
        cost_so_far_usd=_cost(elapsed, price),
        refreshed_at=payload.refreshed_at,
        elapsed_until=None if elapsed is None else until,
        ended_at=payload.ended_at,
        end_reason=payload.end_reason,
        stop_error=payload.stop_error,
        worker_log=str(log) if kept_log else None,
    )


def _shown(stored: StoredWorkerReport | None) -> WorkerProgress | None:
    """Return the stored report as the frontends show it; one that no longer fits shows as unreadable."""
    if stored is None:
        return None
    try:
        return WorkerProgress.model_validate(stored.model_dump())
    except ValidationError:
        return WorkerProgress(state=UNREADABLE_STATE)


def _elapsed_s(payload: RunPayload, until: datetime | None) -> float | None:
    started = payload.pod_created_at
    if started is None or until is None:
        return None
    return max((until - started).total_seconds(), 0)


def _cost(elapsed_s: float | None, price_per_hour: float | None) -> float | None:
    if elapsed_s is None or price_per_hour is None:
        return None
    return round(price_per_hour * elapsed_s / SECONDS_PER_HOUR, COST_SO_FAR_DECIMALS)


def _stage(run: RunInfo, worker: StoredWorkerReport | None) -> RunStage:
    status = run.status
    if status == "queued":
        return "waiting"
    if status != "running":
        return status
    reported = None if worker is None else worker.state
    if reported in {None, WORKER_STARTING}:
        return "starting"
    if reported == WORKER_TRANSCRIBING:
        return "transcribing"
    return "stopping"  # the worker is done (or its report unreadable) while its pod still exists
