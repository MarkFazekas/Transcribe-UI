"""The retry batches of a source batch, found by the `retry_of` of their payload (plan m5 §1 "Retry").

A retry batch is unique per source and job set while it is not `cleaned`, and a source's audio
must stay while one of its retry batches still copies it.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from transcribe_ui.batches.payloads import BatchPayload, RetryOrigin
from transcribe_ui.cleanup.classes import CreateRetryBatchRequest
from transcribe_ui.cleanup.constants import CLEANED, RETRY_SOURCE_PATH
from transcribe_ui.cleanup.planning import RetryPlan, plan_retry
from transcribe_ui.state.models import BatchRow, ItemRow
from transcribe_ui.upload.constants import ITEM_UPLOADED, UPLOADING_BATCH


def _retry_rows(session: Session, source_batch_id: str) -> list[BatchRow]:
    source_of = func.json_extract(BatchRow.payload, RETRY_SOURCE_PATH)
    query = select(BatchRow).where(source_of == source_batch_id)
    return list(session.scalars(query.order_by(BatchRow.batch_id)))


@dataclass(frozen=True)
class ExistingRetry:
    """The retry batch of a source and job set that exists already."""

    batch_id: str
    phase: str

    @property
    def uploading(self) -> bool:
        """Return whether its copy or manifest step may still be due (it is `uploading`)."""
        return self.phase == UPLOADING_BATCH


def retry_target(session: Session, origin: RetryOrigin, request: CreateRetryBatchRequest) -> ExistingRetry | RetryPlan:
    """Return the retry batch of the source and job set that is not `cleaned`, or the plan of a new one.

    Raises:
        DomainError: A new retry batch is refused (see `plan_retry`).
    """
    existing = existing_retry(session, origin)
    if existing is None:
        return plan_retry(session, origin, request)
    return ExistingRetry(existing.batch_id, existing.phase)


def existing_retry(session: Session, origin: RetryOrigin) -> BatchRow | None:
    """Return the retry batch of this source and job set that is not `cleaned`, or None."""
    for row in _retry_rows(session, origin.source_batch_id):
        same = BatchPayload.model_validate(row.payload).retry_of == origin
        if same and row.phase != CLEANED:
            return row
    return None


def copying_retry(session: Session, source_batch_id: str) -> str | None:
    """Return the first retry batch of the source that is `uploading` with an item not copied yet, or None."""
    for row in _retry_rows(session, source_batch_id):
        if row.phase != UPLOADING_BATCH:
            continue
        waiting = (ItemRow.batch_id == row.batch_id, ItemRow.phase != ITEM_UPLOADED)
        query = select(ItemRow.item_id).where(*waiting)
        if session.scalar(query.limit(1)) is not None:
            return row.batch_id
    return None
