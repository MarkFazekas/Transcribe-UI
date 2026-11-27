"""Placement outcomes per job, and the `downloaded` rule (plan m5 §1 "Download").

Every job a download handled gets a `placements` row with its outcome (placed, overwritten, or
skipped as an existing file, a missing folder, an empty or a failed result), committed as soon as
the job is done, so an interrupted download keeps what it did. A batch reaches `downloaded` when it
is `transcribed` (it has its `_done.json`, or it is extract-only) and every job has such a row,
whether from one download or several (Q24). A job whose result failed counts as having its
outcome even without a row (it can fail at `_done.json`, after the last download): failed jobs
never block `downloaded`, retry batches take them (proposed in Q24). `run_download` and
`import_custom_plan` apply the rule after each download; `refresh_status` applies it when it
moves a `ready` batch to `transcribed`, and to every `transcribed` batch whose lock is free (a
download killed after its last outcome). Downloading again (Q25) only adds rows; the phase never
moves back.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.placement.classes import PlacementOutcome
from transcribe_ui.placement.constants import DOWNLOADED_PHASE, RECORDED_OUTCOMES
from transcribe_ui.placement.payloads import PlacementPayload
from transcribe_ui.runs.constants import JOB_FAILED, READY_PHASE, TRANSCRIBED_PHASE
from transcribe_ui.state.database import compare_and_set_phase
from transcribe_ui.state.models import BatchPhase, BatchRow, JobRow, PlacementRow


@dataclass(frozen=True)
class Placed:
    """What a download did with one job."""

    job_id: int
    outcome: PlacementOutcome
    # Where the subtitle went (or would have gone).
    target_path: str | None


def record_placement(session: Session, batch_id: str, placed: Placed, now: datetime) -> None:
    """Add the job's placement outcome (`not_finished` is no outcome and is not recorded)."""
    if placed.outcome not in RECORDED_OUTCOMES:
        return
    payload = PlacementPayload(target_path=placed.target_path).model_dump(mode="json")
    row = PlacementRow(job_id=placed.job_id, batch_id=batch_id, payload=payload)
    row.outcome = placed.outcome
    row.created_at = now
    session.add(row)


def mark_downloaded(session: Session, batch_id: str, now: datetime) -> bool:
    """Move a `transcribed` batch whose every job has an outcome (or failed) to `downloaded`; return whether it moved.

    The caller holds the batch's lock, so the phase read here is the one the compare-and-set expects.
    """
    phase = select(BatchRow.phase).where(BatchRow.batch_id == batch_id)
    if session.scalar(phase) != TRANSCRIBED_PHASE:
        return False
    recorded = PlacementRow.outcome.in_(RECORDED_OUTCOMES)
    with_outcome = select(PlacementRow.job_id).where(recorded)
    of_batch = (JobRow.batch_id == batch_id, JobRow.status != JOB_FAILED)
    lacking = JobRow.job_id.not_in(with_outcome)
    without_outcome = select(JobRow.job_id).where(*of_batch, lacking)
    if session.scalar(without_outcome.limit(1)) is not None:
        return False
    compare_and_set_phase(session, batch_id, TRANSCRIBED_PHASE, DOWNLOADED_PHASE, now=now)
    return True


def mark_transcribed(session: Session, batch_id: str, now: datetime) -> BatchPhase:
    """Move a `ready` batch whose `_done.json` exists to `transcribed`, then apply the `downloaded` rule.

    Returns the phase the batch is in now.

    Raises:
        PhaseConflictError: The batch is no longer `ready`.
    """
    compare_and_set_phase(session, batch_id, READY_PHASE, TRANSCRIBED_PHASE, now=now)
    return DOWNLOADED_PHASE if mark_downloaded(session, batch_id, now) else TRANSCRIBED_PHASE


def settle_transcribed(context: RequestContext) -> None:
    """Apply the `downloaded` rule to every `transcribed` batch whose lock is free (busy ones wait for the next call).

    A download killed after its last recorded outcome leaves its batch `transcribed`; this moves it on.
    The caller may hold the runs lock (order runs → batch); batch locks are only tried.
    """
    state = context.ports.state
    with state.session() as session:
        query = select(BatchRow.batch_id).where(BatchRow.phase == TRANSCRIBED_PHASE)
        transcribed = list(session.scalars(query.order_by(BatchRow.batch_id)))
    for batch_id in transcribed:
        with state.locks.try_batch(batch_id) as taken:
            if not taken:
                continue
            with state.session() as session:
                mark_downloaded(session, batch_id, context.ports.clock.now())
