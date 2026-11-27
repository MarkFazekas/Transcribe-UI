"""What a cleanup of a known batch checks before anything changes (architecture §3.3 "Cloud cleanup").

- `everything` works from `uploading` on (again in `cleaned`, to finish an interrupted one);
  `audio` from `ready` on, refused while a job has no result (the worker would fail it).
- Both are refused while a retry batch of the batch still copies its audio (`retry_copying`,
  naming it), so the retry can resume.
- `confirm` is needed, and for `everything` on a batch whose results may not be downloaded yet
  (`ready`, `transcribed`, with transcribe jobs) also `confirm_not_downloaded`.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_ui.api.classes import Confirmation
from transcribe_ui.batches.constants import JOB_PENDING
from transcribe_ui.batches.phases import require_phase_name
from transcribe_ui.cleanup.classes import CleanupCloudRequest
from transcribe_ui.cleanup.constants import (
    AUDIO_PHASES,
    CONFIRM_FIELD,
    CONFIRM_NOT_DOWNLOADED_FIELD,
    EVERYTHING_PHASES,
    NOT_DOWNLOADED_PHASES,
)
from transcribe_ui.cleanup.errors import ResultsPendingError, RetryCopyingError
from transcribe_ui.cleanup.origins import copying_retry
from transcribe_ui.state.models import BatchRow, JobRow

if TYPE_CHECKING:
    from pydantic import JsonValue


@dataclass(frozen=True)
class CleanupTarget:
    """A known batch as the cleanup checks it."""

    batch_id: str
    phase: str
    # Transcribe jobs of the batch, and how many of them have no result yet.
    transcribe_jobs: int
    pending_jobs: int
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
        transcribe_jobs=len(statuses),
        pending_jobs=statuses.count(JOB_PENDING),
        copying_retry=copying_retry(session, batch_id),
    )


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
        message = _message(request)
        needed.append(Confirmation(field=CONFIRM_FIELD, message=message, details=details))
    if request.scope == "everything" and _not_downloaded(target) and not request.confirm_not_downloaded:
        message = "The batch's subtitles may not be downloaded yet; after this, only the backup on the cloud volume keeps them."
        needed.append(Confirmation(field=CONFIRM_NOT_DOWNLOADED_FIELD, message=message, details=details))
    return tuple(needed)


def _not_downloaded(target: CleanupTarget) -> bool:
    return target.phase in NOT_DOWNLOADED_PHASES and target.transcribe_jobs > 0


def _message(request: CleanupCloudRequest) -> str:
    if request.scope == "audio":
        return "The batch's audio will be deleted from the cloud; its subtitles stay, but it can no longer be retried."
    return "All of the batch's data will be deleted from the cloud: audio, subtitles and its description."
