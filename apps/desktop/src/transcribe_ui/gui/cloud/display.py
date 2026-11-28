"""Display data of the runs on the Cloud page and in the status bar (architecture §3.1 "GUI").

Qt-free. Everything shown comes from the backend's responses: a run's stage, its pod and worker
state, the current job, the counts, the progress, the remaining time, elapsed time and cost (as
of the last refresh, never extrapolated), the deadline and the end. Only presentation is decided here: texts, number, money, date and duration formats,
and the remaining-time countdown to the backend's `estimated_finish_at` (display only, M6.12).
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from transcribe_ui.gui.cloud.constants import COST_DECIMALS, PROGRESS_SCALE
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.i18n.formats import format_datetime, format_duration, format_number
from transcribe_ui.i18n.texts import code_text
from transcribe_ui.runs.constants import FINAL_RUN_STATUSES, WAITING_RUN_STAGES
from transcribe_ui.runs.responses import RunProgress, RunStage, WorkerProgress


@dataclass(frozen=True)
class RunView:
    """A run as the Cloud page shows it (the current run, or the last one with its outcome)."""

    run_id: str
    stage: RunStage
    stage_text: str
    # The run is final: no stop, no poll, no countdown.
    final: bool
    created: str
    test_mode: bool
    # The status Runpod reported for the pod at the last check, or "".
    pod_status: str
    # The state the worker reports, or "".
    worker_state: str
    # The file the worker transcribes now (and its language), or "".
    current_file: str
    # The job (batch) of that file, or None.
    current_job: str | None
    # Transcriptions done of the worker's total, or "".
    done: str
    # Done share on the progress bar's scale (`PROGRESS_SCALE`), or None when unknown.
    progress: int | None
    progress_text: str
    # When the backend expects the work to be done (UTC), or None.
    finish_at: datetime | None
    # Elapsed time and cost so far, as of `updated` ("" while unknown).
    elapsed: str
    cost: str
    deadline: str
    # When the last check was (with seconds), or that there was none yet.
    updated: str
    # Why the run ended, the worker's own error, and a deadline stop that did not show yet ("" each when none).
    end_reason: str
    worker_error: str
    stop_error: str
    # "Stop waiting" while waiting for a machine, "Stop" while starting or transcribing; None once final.
    stop_label: str | None
    worker_log: Path | None


@dataclass(frozen=True)
class HistoryRow:
    """One run of the history."""

    run_id: str
    created: str
    stage_text: str
    end_reason: str
    elapsed: str
    cost: str
    test_mode: str
    worker_log: Path | None


def local_time(moment: datetime, *, seconds: bool = False) -> str:
    """Return a moment as a date and time of this computer's zone."""
    return format_datetime(moment.astimezone(), seconds=seconds)


def money(usd: float) -> str:
    """Return an amount in USD."""
    return tr("{amount} USD").format(amount=format_number(usd, COST_DECIMALS))


def is_final(progress: RunProgress) -> bool:
    """Return whether the run has ended (its status is final; the backend's rule)."""
    return progress.run.status in FINAL_RUN_STATUSES


def stop_label(stage: RunStage) -> str:
    """Return the stop button's label: "Stop waiting" while waiting for a machine, "Stop" afterwards.

    Q34's "Cancel"; named apart from the confirmation dialog's own Cancel ("do not stop").
    """
    return tr("Stop waiting") if stage in WAITING_RUN_STAGES else tr("Stop")


def countdown_text(finish_at: datetime | None, now: datetime) -> str:
    """Return the remaining time to `finish_at`, counted down; "finishing" at 0, never negative; "" when unknown."""
    if finish_at is None:
        return ""
    remaining = (finish_at - now).total_seconds()
    if remaining <= 0:
        return tr("Finishing")
    return tr("{duration} left").format(duration=format_duration(remaining))


def _current_file(worker: WorkerProgress | None) -> tuple[str, str | None]:
    current = None if worker is None else worker.current
    if current is None:
        return "", None
    name = current.name or current.item_id
    return f"{name} ({current.language})", current.batch_id


def _done(worker: WorkerProgress | None) -> str:
    if worker is None or not worker.total_jobs:
        return ""
    return f"{format_number(worker.done_jobs)}/{format_number(worker.total_jobs)}"


def _progress(fraction: float | None) -> tuple[int | None, str]:
    if fraction is None:
        return None, ""
    percent = format_number(fraction * 100)
    return round(fraction * PROGRESS_SCALE), f"{percent}%"


def _updated(progress: RunProgress) -> str:
    if progress.refreshed_at is None:
        return tr("Not checked yet")
    shown = local_time(progress.refreshed_at, seconds=True)
    return tr("Updated {time}").format(time=shown)


def _stop_error(code: str | None) -> str:
    if code is None:
        return ""
    return tr(
        "The cloud machine could not be stopped at its time limit yet ({code}); the next check tries again."
    ).format(
        code=code,
    )


def _elapsed(progress: RunProgress) -> str:
    return "" if progress.elapsed_s is None else format_duration(progress.elapsed_s)


def _cost(progress: RunProgress) -> str:
    return "" if progress.cost_so_far_usd is None else money(progress.cost_so_far_usd)


def _end_reason(progress: RunProgress) -> str:
    reason = progress.end_reason
    return "" if reason is None else code_text("run_end_reason", reason)


def _log(progress: RunProgress) -> Path | None:
    return None if progress.worker_log is None else Path(progress.worker_log)


def run_view(progress: RunProgress) -> RunView:
    """Return a run as the Cloud page shows it."""
    run = progress.run
    worker = progress.worker
    final = is_final(progress)
    current_file, current_job = _current_file(worker)
    shown_progress, progress_text = _progress(progress.progress_fraction)
    return RunView(
        run_id=run.run_id,
        stage=progress.stage,
        stage_text=code_text("run_stage", progress.stage),
        final=final,
        created=local_time(run.created_at),
        test_mode=run.test_mode,
        pod_status=progress.pod_status or "",
        worker_state="" if worker is None else code_text("worker_state", worker.state),
        current_file=current_file,
        current_job=current_job,
        done=_done(worker),
        progress=shown_progress,
        progress_text=progress_text,
        finish_at=None if final else progress.estimated_finish_at,
        elapsed=_elapsed(progress),
        cost=_cost(progress),
        deadline=local_time(progress.deadline),
        updated=_updated(progress),
        end_reason=_end_reason(progress),
        worker_error="" if worker is None else worker.error or "",
        stop_error=_stop_error(progress.stop_error),
        stop_label=None if final else stop_label(progress.stage),
        worker_log=_log(progress),
    )


def history_row(progress: RunProgress) -> HistoryRow:
    """Return a run as the history lists it (elapsed time and cost to its end, else its last refresh)."""
    run = progress.run
    return HistoryRow(
        run_id=run.run_id,
        created=local_time(run.created_at),
        stage_text=code_text("run_stage", progress.stage),
        end_reason=_end_reason(progress),
        elapsed=_elapsed(progress),
        cost=_cost(progress),
        test_mode=tr("yes") if run.test_mode else "",
        worker_log=_log(progress),
    )
