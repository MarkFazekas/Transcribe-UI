"""The progress of a followed run: done share, remaining time and finish (architecture §3.7 "Progress fields").

`refresh_status` computes it once the `ready` batches' results are recorded and stores it in the
run payload with the refresh, so `list_runs` shows the same values (`runs/progress.py`). The
followed audio is that of the transcribe jobs the launch found pending in the covered batches this
database knows (`weighted_known_duration_s`), `careful` jobs counted twice as in the estimate; a
job no longer pending here (ok, no speech or failed) counts as done.

The remaining time is the pending work less the time the worker has spent on the file in
progress, never below 0, so it counts down between refreshes:

- Pending work: once the run has written an `ok` or `no_speech` result, the worker's pace (the
  summed `finished_at - started_at` of those results per weighted second of their audio) applied
  to the pending weighted audio, that of the covered batches of another computer included (their
  pending jobs read from R2: the run's lease lists them, so the worker does them too). Before
  that, the launch estimate's runtime factors without the margin (audio x factor + overhead per
  job and pass), plus the covered batches of another computer (`unknown_runtime_s`, kept at the
  launch).
- Time on the file in progress, on the worker's clock: its report's `updated_at` minus the latest
  `finished_at` of the run's results, else minus the `updated_at` of the first report that showed
  `transcribing` with a current job (`transcribing_since`, kept in the run payload; the worker
  reports `transcribing` before its model loads and names its first job only once loaded).
- Until then (no result and no such report), what is left of the start-up allowance since the
  first `RUNNING` sighting is added instead.
- 0 once the worker reported its end (the pod is being stopped).

It is unknown (None) while no machine runs the run (launching or waiting), for an unreadable
worker report, for a run launched before the followed audio was kept, for a run without followed
audio, and once the run is final (`runs/records.py` also clears it when a run ends otherwise).
The finish is `refreshed_at` plus the remaining time.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from fractions import Fraction
from typing import NamedTuple

from transcribe_contracts.classes import RunStatus as WorkerStatus
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runs.constants import (
    FINAL_WORKER_STATES,
    PROGRESS_FRACTION_DECIMALS,
    STARTUP_ALLOWANCE_S,
    WORKER_TRANSCRIBING,
)
from transcribe_ui.runs.estimate import job_runtime_s, weighted_duration
from transcribe_ui.runs.followed_jobs import FollowedJobs, followed_jobs
from transcribe_ui.runs.manifests import total_duration
from transcribe_ui.runs.payloads import RunPayload
from transcribe_ui.runs.records import load_run, payload_of, update_run
from transcribe_ui.runs.refreshing import Refreshed
from transcribe_ui.runs.worker import WorkerReport
from transcribe_ui.storage.r2 import ObjectStore


class RunEstimate(NamedTuple):
    """A run's progress as of its refresh; None where unknown."""

    progress_fraction: float | None = None
    remaining_estimate_s: int | None = None
    estimated_finish_at: datetime | None = None


class RunWork(NamedTuple):
    """What a run's progress is computed from: its status, its stored payload and its followed jobs."""

    status: str
    payload: RunPayload
    jobs: FollowedJobs


def record_progress(context: RequestContext, refreshed: Sequence[Refreshed], store: ObjectStore) -> None:
    """Compute and store the progress of every run the refresh followed (after the results are recorded).

    Raises:
        StorageError: R2 could not be read (another computer's covered batches).
    """
    state = context.ports.state
    for each in refreshed:
        work = _noted_transcribing(_run_work(context, each.run_id, store), each.worker)
        changes = {**estimate_progress(work, each.worker)._asdict()}
        changes["transcribing_since"] = work.payload.transcribing_since
        update_run(state, each.run_id, context.ports.clock.now(), changes=changes)


def estimate_progress(work: RunWork, worker: WorkerReport | None) -> RunEstimate:
    """Return the run's progress from its followed jobs and the worker report this refresh read (None: none)."""
    total = work.payload.weighted_known_duration_s
    if total is None or total <= 0:
        return RunEstimate()
    pending_s = min(weighted_duration(work.jobs.pending), total)
    remaining = _remaining_s(work, worker, pending_s)
    refreshed_at = work.payload.refreshed_at
    finish = None
    if remaining is not None and refreshed_at is not None:
        finish = refreshed_at + timedelta(seconds=remaining)
    return RunEstimate(done_share(pending_s, total), remaining, finish)


def done_share(pending_s: float, total_s: float) -> float:
    """Return the done share of `total_s`, rounded down exactly to its decimals, so 1 means nothing is pending."""
    scale = 10**PROGRESS_FRACTION_DECIMALS
    done = 1 - Fraction(pending_s) / Fraction(total_s)
    return int(done * scale) / scale  # not negative, so truncating rounds down


def _remaining_s(work: RunWork, worker: WorkerReport | None, pending_s: float) -> int | None:
    """Return the remaining time; None while no machine runs the run, once it is final, or for an unreadable report."""
    if work.status != "running":
        return None
    if worker is None:
        return round(_startup_left_s(work.payload) + _pending_work_s(work, pending_s))
    report = worker.status
    if report is None:
        return None
    return _reported_s(work, report, pending_s)


def _reported_s(work: RunWork, report: WorkerStatus, pending_s: float) -> int:
    """Return the remaining time by the worker's readable report: 0 once it ended, else the work left."""
    if report.state in FINAL_WORKER_STATES:
        return 0
    work_s = _pending_work_s(work, pending_s)
    since = _in_progress_since(work)
    if since is None:  # no job started yet: the start-up (model load included) is still ahead
        return round(_startup_left_s(work.payload) + work_s)
    work_s -= max((report.updated_at - since).total_seconds(), 0)
    return round(max(work_s, 0))


def _pending_work_s(work: RunWork, pending_s: float) -> float:
    """Return the pending work: at the worker's measured pace, else by the launch estimate's factors."""
    paced = [done for done in work.jobs.worked if done.paced]
    audio_s = total_duration(done.weighted_s for done in paced)
    if audio_s > 0:
        spent = total_duration(done.spent_s for done in paced)
        return spent / audio_s * (pending_s + weighted_duration(work.jobs.others))
    launch_s = total_duration(job_runtime_s(job) for job in work.jobs.pending)
    return launch_s + (work.payload.unknown_runtime_s or 0)


def _in_progress_since(work: RunWork) -> datetime | None:
    """Return when the worker started the file in progress (worker clock): after its latest result, else its start."""
    finished = [done.finished_at for done in work.jobs.worked]
    return max(finished) if finished else work.payload.transcribing_since


def _startup_left_s(payload: RunPayload) -> float:
    """Return what is left of the start-up allowance at the refresh, counted from the first `RUNNING` sighting."""
    since = payload.first_running_at
    until = payload.refreshed_at
    if since is None or until is None:
        return STARTUP_ALLOWANCE_S
    return max(STARTUP_ALLOWANCE_S - (until - since).total_seconds(), 0)


def _noted_transcribing(work: RunWork, worker: WorkerReport | None) -> RunWork:
    """Return the work with `transcribing_since` set from the first report that shows a job being transcribed.

    The worker reports `transcribing` before it loads the model; it names its current job once loaded.
    """
    report = None if worker is None else worker.status
    if work.payload.transcribing_since is not None or report is None:
        return work
    if report.state != WORKER_TRANSCRIBING or report.current is None:
        return work
    payload = work.payload.model_copy(update={"transcribing_since": report.updated_at})
    return work._replace(payload=payload)


def _run_work(context: RequestContext, run_id: str, store: ObjectStore) -> RunWork:
    """Return what the run's progress is computed from; another computer's covered batches only while it runs.

    Raises:
        StorageError: R2 could not be read.
    """
    with context.ports.state.session() as session:
        row = load_run(session, run_id)
        payload = payload_of(row)
        jobs = followed_jobs(session, payload.covered_batch_ids, run_id)
        status = row.status
    if status == "running":
        jobs = jobs.with_others(store)
    return RunWork(status, payload, jobs)
