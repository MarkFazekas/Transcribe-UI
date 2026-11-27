"""Storing a new retry batch: the batch in `uploading`, its items `reviewed` with their copy sources (§3.3)."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from transcribe_ui.api.ports import IdSource
from transcribe_ui.batches.payloads import ItemPayload, JobPayload
from transcribe_ui.cleanup.constants import RETRY_MODE
from transcribe_ui.cleanup.planning import RetryItem, RetryPlan
from transcribe_ui.state.models import BatchRow, ItemRow, JobRow
from transcribe_ui.upload.constants import ITEM_REVIEWED, UPLOADING_BATCH


@dataclass(frozen=True)
class NewBatch:
    """Where and when a retry batch is created."""

    batch_id: str
    now: datetime


def insert_retry(session: Session, new: NewBatch, plan: RetryPlan, ids: IdSource) -> None:
    """Insert the retry batch in `uploading`, its items `reviewed` (copy sources set) and its jobs pending."""
    session.add(
        BatchRow(
            batch_id=new.batch_id,
            phase=UPLOADING_BATCH,
            manifest_held=False,
            audio_deleted=False,
            hidden=False,
            created_at=new.now,
            updated_at=new.now,
            payload=plan.payload.model_dump(mode="json"),
        ),
    )
    session.flush()
    for position, member in enumerate(plan.members):
        _insert_item(session, new.batch_id, position, member, ids.new_id())


def _insert_item(session: Session, batch_id: str, position: int, member: RetryItem, item_id: str) -> None:
    changes = {"position": position, "problem": None, "copy_sources": member.copies}
    payload: ItemPayload = member.source.payload.model_copy(update=changes)
    stored = payload.model_dump(mode="json")
    row = ItemRow(item_id=item_id, batch_id=batch_id, phase=ITEM_REVIEWED)
    row.payload = stored
    session.add(row)
    session.flush()
    for job in member.jobs:
        retried = JobPayload(mode=RETRY_MODE, track_index=job.payload.track_index)
        job_row = JobRow(item_id=item_id, batch_id=batch_id, language=job.row.language)
        job_row.payload = retried.model_dump(mode="json")
        job_row.kind = "transcribe"
        job_row.status = "pending"
        session.add(job_row)
    session.flush()
