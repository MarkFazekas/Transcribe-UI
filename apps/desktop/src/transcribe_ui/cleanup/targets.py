"""What a cleanup of a known batch checks before anything changes (architecture §3.3 "Cloud cleanup").

- `everything` works from `uploading` on (again in `cleaned`, to finish an interrupted one);
  `audio` from `ready` on, refused while a job has no result (the worker would fail it).
- Both are refused while a retry batch of the batch still copies its audio (`retry_copying`,
  naming it), so the retry can resume.
- `audio` is refused for another computer's job (`adopted_batch`).

The confirmations it then needs are `cleanup/confirmations.py`'s.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from transcribe_ui.batches.constants import JOB_PENDING
from transcribe_ui.batches.payloads import BatchPayload
from transcribe_ui.batches.phases import require_not_adopted, require_phase_name
from transcribe_ui.cleanup.classes import CleanupCloudRequest
from transcribe_ui.cleanup.constants import AUDIO_PHASES, EVERYTHING_PHASES
from transcribe_ui.cleanup.errors import ResultsPendingError, RetryCopyingError
from transcribe_ui.cleanup.origins import copying_retry
from transcribe_ui.placement.constants import RECORDED_OUTCOMES
from transcribe_ui.runs.constants import JOB_FAILED
from transcribe_ui.state.models import BatchRow, JobRow, PlacementRow


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
    # Another computer's batch (`adopt_cloud_batch`): its audio is not this computer's to delete.
    adopted: bool = False
    # The installation that uploaded an adopted batch (its manifest's `uploaded_by`); None for a
    # batch uploaded here, or an adopted one whose manifest did not say.
    uploaded_by: str | None = None

    def uploaded_by_installation(self, installation_id: str) -> bool:
        """Return whether this installation uploaded the batch: not adopted, or adopted with its id (Q35)."""
        return not self.adopted or self.uploaded_by == installation_id


def load_target(session: Session, batch_id: str) -> CleanupTarget | None:
    """Return the batch as the cleanup checks it, or None if this database does not know it."""
    row = session.get(BatchRow, batch_id)
    if row is None:
        return None
    adopted = BatchPayload.model_validate(row.payload).adopted
    transcribing = (JobRow.batch_id == batch_id, JobRow.kind == "transcribe")
    query = select(JobRow.status).where(*transcribing)
    statuses = list(session.scalars(query))
    return CleanupTarget(
        batch_id=batch_id,
        phase=row.phase,
        pending_jobs=statuses.count(JOB_PENDING),
        jobs_not_downloaded=_not_downloaded_jobs(session, batch_id),
        copying_retry=copying_retry(session, batch_id),
        adopted=adopted is not None,
        uploaded_by=None if adopted is None else adopted.uploaded_by,
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
        AdoptedBatchError: `audio` on another computer's job.
        RetryCopyingError: A retry batch of the batch still copies its audio.
        ResultsPendingError: `audio` while a job has no result.
    """
    allowed = AUDIO_PHASES if request.scope == "audio" else EVERYTHING_PHASES
    require_phase_name(target.batch_id, target.phase, allowed)
    if request.scope == "audio":
        require_not_adopted(target.batch_id, adopted=target.adopted)
    if target.copying_retry is not None:
        raise RetryCopyingError(target.batch_id, target.copying_retry)
    if request.scope == "audio" and target.pending_jobs:
        raise ResultsPendingError(target.batch_id, target.pending_jobs)
