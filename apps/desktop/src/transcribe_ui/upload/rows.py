"""What the upload persists, one step per transaction (architecture §3.3 "Upload").

Each verified audio object, each extracted subtitle and each finished item is recorded in its
own transaction, conditional on the batch still being `uploading` (`touch_batch`), so an
interrupted upload resumes after the last recorded step. Nothing here talks to R2 or ffmpeg.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.constants import UPLOADING_PHASES
from transcribe_ui.batches.phases import touch_batch
from transcribe_ui.batches.records import StoredItem, stored_items
from transcribe_ui.state.models import AudioObjectRow, ExtractedSubtitleRow, ItemRow, JobRow
from transcribe_ui.upload.constants import AUDIO_VERIFIED, ITEM_IN_REVIEW, ITEM_REVIEWED, ITEM_UPLOADED, JOB_EXTRACTED
from transcribe_ui.upload.payloads import AudioObjectPayload, ExtractedSubtitlePayload


@dataclass(frozen=True)
class ItemProgress:
    """What of one item is already recorded: its verified audio tracks and its extracted jobs."""

    # Audio tracks with a verified object (None: an audio file uploaded as it is).
    tracks: frozenset[int | None]
    # Ids of the `extract` jobs whose SRT is stored.
    extracted: frozenset[int]


@dataclass(frozen=True)
class Extracted:
    """The SRT text of an embedded subtitle stream."""

    stream_index: int
    srt_text: str


def _items_in_phase(context: RequestContext, batch_id: str, phase: str) -> list[StoredItem]:
    with context.ports.state.session() as session:
        members = stored_items(session, batch_id)
    return [member for member in members if member.phase == phase]


def items_to_upload(context: RequestContext, batch_id: str) -> list[StoredItem]:
    """Return the batch's resolved items that are not uploaded yet, in selection order."""
    return _items_in_phase(context, batch_id, ITEM_REVIEWED)


def items_in_review(context: RequestContext, batch_id: str) -> tuple[str, ...]:
    """Return the ids of the batch's items in review, in selection order."""
    return tuple(member.item_id for member in _items_in_phase(context, batch_id, ITEM_IN_REVIEW))


def has_transcribe_jobs(context: RequestContext, batch_id: str) -> bool:
    """Return whether the batch has a `transcribe` job (else it takes the extract-only path)."""
    transcribing = (JobRow.batch_id == batch_id, JobRow.kind == "transcribe")
    query = select(JobRow.job_id).where(*transcribing).limit(1)
    with context.ports.state.session() as session:
        return session.scalar(query) is not None


def item_progress(context: RequestContext, item_id: str) -> ItemProgress:
    """Return what of the item is already recorded."""
    with context.ports.state.session() as session:
        verified = (AudioObjectRow.item_id == item_id, AudioObjectRow.status == AUDIO_VERIFIED)
        rows = session.scalars(select(AudioObjectRow).where(*verified))
        payloads = [AudioObjectPayload.model_validate(row.payload) for row in rows]
        tracks = frozenset(payload.track_index for payload in payloads)
        extracted_query = (
            select(ExtractedSubtitleRow.job_id)
            .join(JobRow, JobRow.job_id == ExtractedSubtitleRow.job_id)
            .where(JobRow.item_id == item_id)
        )
        return ItemProgress(tracks, frozenset(session.scalars(extracted_query)))


def record_audio(context: RequestContext, member: StoredItem, key: str, audio: AudioObjectPayload) -> None:
    """Record a verified audio object of the item.

    Raises:
        PhaseConflictError: If the batch is no longer `uploading`.
    """
    batch_id = member.row.batch_id
    with context.ports.state.session() as session:
        _touch(context, session, member)
        stored = audio.model_dump(mode="json")
        row = AudioObjectRow(
            object_key=key, batch_id=batch_id, item_id=member.item_id, status=AUDIO_VERIFIED, payload=stored
        )
        session.merge(row)


def record_extraction(context: RequestContext, member: StoredItem, job_id: int, extracted: Extracted) -> None:
    """Store the SRT of an `extract` job and mark the job `extracted`.

    Raises:
        PhaseConflictError: If the batch is no longer `uploading`.
    """
    stored = ExtractedSubtitlePayload(stream_index=extracted.stream_index).model_dump(mode="json")
    row = ExtractedSubtitleRow(job_id=job_id, srt_text=extracted.srt_text, payload=stored)
    with context.ports.state.session() as session:
        _touch(context, session, member)
        session.merge(row)
        job = session.get(JobRow, job_id)
        assert job is not None, "the job is locked by its batch lock"
        job.status = JOB_EXTRACTED


def mark_uploaded(context: RequestContext, member: StoredItem) -> None:
    """Move the item to `uploaded`: its audio is verified and its extractions are stored.

    Raises:
        PhaseConflictError: If the batch is no longer `uploading`.
    """
    with context.ports.state.session() as session:
        _touch(context, session, member)
        row = session.get(ItemRow, member.item_id)
        assert row is not None, "the item is locked by its batch lock"
        row.phase = ITEM_UPLOADED


def _touch(context: RequestContext, session: Session, member: StoredItem) -> None:
    """Record a change of the item's batch, which must still be `uploading`."""
    now = context.ports.clock.now()
    touch_batch(session, member.row.batch_id, UPLOADING_PHASES, now=now)
