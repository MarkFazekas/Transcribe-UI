"""What a cleanup of a known batch checks before anything changes (architecture §3.3 "Cloud cleanup").

- `everything` works from `uploading` on (again in `cleaned`, to finish an interrupted one);
  `audio` from `ready` on, refused while a job has no result (the worker would fail it).
- Both are refused while a retry batch of the batch still copies its audio (`retry_copying`,
  naming it), so the retry can resume.
- `confirm` is needed, and for `everything` on a batch whose results may not be downloaded yet
  also `confirm_not_downloaded` when a transcribe job's subtitle may not be downloaded yet: the
  batch is `ready` or `transcribed` and a job that did not fail has no placement outcome
  (`placement/recording.py`, the rule that moves a batch to `downloaded`).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from transcribe_ui.api.classes import Confirmation
from transcribe_ui.batches.constants import JOB_PENDING
from transcribe_ui.batches.phases import require_phase_name
from transcribe_ui.cleanup.classes import CleanupCloudRequest
from transcribe_ui.cleanup.constants import (
    AUDIO_PHASES,
    CONFIRM_FIELD,
    CONFIRM_NOT_DOWNLOADED_FIELD,
    CONFIRMATION_CLEANUP_AUDIO,
    CONFIRMATION_CLEANUP_EVERYTHING,
    CONFIRMATION_NOT_DOWNLOADED,
    EVERYTHING_PHASES,
    NOT_DOWNLOADED_PHASES,
)
from transcribe_ui.cleanup.errors import ResultsPendingError, RetryCopyingError
from transcribe_ui.cleanup.origins import copying_retry
from transcribe_ui.placement.constants import RECORDED_OUTCOMES
from transcribe_ui.runs.constants import JOB_FAILED
from transcribe_ui.state.models import BatchRow, JobRow, PlacementRow

if TYPE_CHECKING:
    from pydantic import JsonValue


@dataclass(frozen=True)
class CleanupTarget:
    """A known batch as the cleanup checks it."""

    batch_id: str
    phase: str
    # Transcribe jobs of the batch without a result yet.
    pending_jobs: int
    # Transcribe jobs that did not fail and have no recorded placement outcome (not downloaded).
    jobs_not_downloaded: int
    # A retry batch of this batch that still copies its audio, or None.
    copying_retry: str | None


def load_target(session: Session, batch_id: str) -> CleanupTarget | None:
    """Return the batch as the cleanup checks it, or None if this database does not know it."""
    row = session.get(BatchRow, batch_id)
    if row is None:
        return None
    transcribing = (JobRow.batch_id == batch_id, JobRow.kind == "transcribe")
    query = select(JobRow.status).where(*transcribing)
    statuses = list(session.scalars(query))
    return CleanupTarget(
        batch_id=batch_id,
        phase=row.phase,
        pending_jobs=statuses.count(JOB_PENDING),
        jobs_not_downloaded=_not_downloaded_jobs(session, batch_id),
        copying_retry=copying_retry(session, batch_id),
    )


def _not_downloaded_jobs(session: Session, batch_id: str) -> int:
    """Return how many transcribe jobs that did not fail have no recorded placement outcome."""
    with_outcome = PlacementRow.outcome.in_(RECORDED_OUTCOMES)
    recorded = select(PlacementRow.job_id).where(with_outcome)
    transcribing = (JobRow.batch_id == batch_id, JobRow.kind == "transcribe")
    waiting = (JobRow.status != JOB_FAILED, JobRow.job_id.not_in(recorded))
    counted = select(func.count(JobRow.job_id))
    return session.scalar(counted.where(*transcribing, *waiting)) or 0


def check_target(request: CleanupCloudRequest, target: CleanupTarget) -> None:
    """Refuse a cleanup the batch's state does not allow (nothing is changed).

    Raises:
        WrongPhaseError: The scope is not possible in the batch's phase.
        RetryCopyingError: A retry batch of the batch still copies its audio.
        ResultsPendingError: `audio` while a job has no result.
    """
    allowed = AUDIO_PHASES if request.scope == "audio" else EVERYTHING_PHASES
    require_phase_name(target.batch_id, target.phase, allowed)
    if target.copying_retry is not None:
        raise RetryCopyingError(target.batch_id, target.copying_retry)
    if request.scope == "audio" and target.pending_jobs:
        raise ResultsPendingError(target.batch_id, target.pending_jobs)


def missing_confirmations(request: CleanupCloudRequest, target: CleanupTarget) -> tuple[Confirmation, ...]:
    """Return the confirmations the request still lacks (empty: it may go ahead)."""
    details: dict[str, JsonValue] = {"batch_id": target.batch_id, "scope": request.scope, "phase": target.phase}
    needed: list[Confirmation] = []
    if not request.confirm:
        code = CONFIRMATION_CLEANUP_AUDIO if request.scope == "audio" else CONFIRMATION_CLEANUP_EVERYTHING
        message = _message(request)
        confirm = Confirmation(
            code=code,
            field=CONFIRM_FIELD,
            set_to=True,
            message=message,
            details=details,
        )
        needed.append(confirm)
    if request.scope == "everything" and _not_downloaded(target) and not request.confirm_not_downloaded:
        message = "The batch's subtitles may not be downloaded yet; after this, only the backup on the cloud volume keeps them."
        not_downloaded = Confirmation(
            code=CONFIRMATION_NOT_DOWNLOADED,
            field=CONFIRM_NOT_DOWNLOADED_FIELD,
            set_to=True,
            message=message,
            details=details,
        )
        needed.append(not_downloaded)
    return tuple(needed)


def _not_downloaded(target: CleanupTarget) -> bool:
    return target.phase in NOT_DOWNLOADED_PHASES and target.jobs_not_downloaded > 0


def _message(request: CleanupCloudRequest) -> str:
    if request.scope == "audio":
        return "The batch's audio will be deleted from the cloud; its subtitles stay, but it can no longer be retried."
    return "All of the batch's data will be deleted from the cloud: audio, subtitles and its description."
