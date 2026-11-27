"""A run as `refresh_status` shows it: stage, worker report, deadline, elapsed time and cost so far.

Elapsed time counts from the pod's creation (`createdAt`, else its first sighting) until the run
ended (or now); the cost so far is that time at the pod's price per hour (`cost`). Both are
estimates for the display; Runpod's own bill is what counts.
"""

from datetime import datetime

from transcribe_ui.runs.constants import (
    COST_SO_FAR_DECIMALS,
    SECONDS_PER_HOUR,
    WORKER_STARTING,
    WORKER_TRANSCRIBING,
)
from transcribe_ui.runs.ending import desktop_deadline
from transcribe_ui.runs.payloads import RunPayload
from transcribe_ui.runs.records import load_run, payload_of, run_info
from transcribe_ui.runs.refreshing import Refreshed
from transcribe_ui.runs.responses import RunInfo, RunProgress, RunStage
from transcribe_ui.runs.worker import WorkerReport
from transcribe_ui.runs.worker_logs import worker_log_path
from transcribe_ui.state.store import LocalState


def runs_progress(state: LocalState, refreshed: list[Refreshed], now: datetime) -> tuple[RunProgress, ...]:
    """Return each refreshed run as it is now, with the worker report its refresh read."""
    return tuple(_refreshed_progress(state, each, now) for each in refreshed)


def _refreshed_progress(state: LocalState, refreshed: Refreshed, now: datetime) -> RunProgress:
    return run_progress(state, refreshed.run_id, refreshed.worker, refreshed.stop_error, now)


def run_progress(
    state: LocalState,
    run_id: str,
    worker: WorkerReport | None,
    stop_error: str | None,
    now: datetime,
) -> RunProgress:
    """Return the run as it is now, with the worker report read by this refresh."""
    with state.session() as session:
        row = load_run(session, run_id)
        run = run_info(row)
        payload = payload_of(row)
    elapsed = _elapsed_s(payload, now)
    price = payload.price_per_hour_usd
    log = worker_log_path(state.paths.log_dir, run_id)
    kept_log = payload.objects_collected and log.is_file()
    return RunProgress(
        run=run,
        stage=_stage(run, worker),
        pod_status=payload.pod_status,
        worker=None if worker is None else worker.progress(),
        first_running_at=payload.first_running_at,
        deadline=desktop_deadline(payload),
        elapsed_s=elapsed,
        price_per_hour_usd=price,
        cost_so_far_usd=_cost(elapsed, price),
        ended_at=payload.ended_at,
        end_reason=payload.end_reason,
        stop_error=stop_error,
        worker_log=str(log) if kept_log else None,
    )


def _elapsed_s(payload: RunPayload, now: datetime) -> float | None:
    started = payload.pod_created_at
    if started is None:
        return None
    until = payload.ended_at or now
    return max((until - started).total_seconds(), 0)


def _cost(elapsed_s: float | None, price_per_hour: float | None) -> float | None:
    if elapsed_s is None or price_per_hour is None:
        return None
    return round(price_per_hour * elapsed_s / SECONDS_PER_HOUR, COST_SO_FAR_DECIMALS)


def _stage(run: RunInfo, worker: WorkerReport | None) -> RunStage:
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
