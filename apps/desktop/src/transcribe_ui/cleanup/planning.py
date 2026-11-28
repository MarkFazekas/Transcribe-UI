"""What a retry batch holds, read from its source batch (plan m5 §1 "Retry").

A retry batch gets the source's flagged jobs that the request names, as `careful` transcribe
jobs in the same language on the same audio track. Each of its items is a new item (its own id,
so its own audio key) whose audio is copied server side from the source's verified object of
that track (`CopySource`); the file itself is never read again.
"""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_ui.batches.actions import flagged_job
from transcribe_ui.batches.payloads import BatchPayload, RetryJob, RetryOrigin
from transcribe_ui.batches.phases import load_batch, require_phase
from transcribe_ui.batches.records import StoredItem, StoredJob, stored_items
from transcribe_ui.cleanup.classes import CreateRetryBatchRequest
from transcribe_ui.cleanup.constants import (
    REASON_NO_SUCH_JOB,
    REASON_NOT_FLAGGED,
    RETRY_SOURCE_PHASES,
)
from transcribe_ui.cleanup.errors import AudioDeletedError, RetryJobInvalidError
from transcribe_ui.state.models import AudioObjectRow
from transcribe_ui.upload.constants import AUDIO_VERIFIED
from transcribe_ui.upload.errors import CopySourceMissingError
from transcribe_ui.upload.payloads import AudioObjectPayload, CopySource

# The verified audio of one source item: object key and payload by track (None: an audio file).
type ItemAudio = dict[int | None, tuple[str, AudioObjectPayload]]


@dataclass(frozen=True)
class RetryItem:
    """A source item with the jobs to retry and the copies of the audio they use."""

    source: StoredItem
    jobs: tuple[StoredJob, ...]
    copies: tuple[CopySource, ...]


@dataclass(frozen=True)
class RetryPlan:
    """The retry batch to create: its payload (with its origin) and its items in selection order."""

    payload: BatchPayload
    members: tuple[RetryItem, ...]


def origin_of(request: CreateRetryBatchRequest) -> RetryOrigin:
    """Return the request's source and job set (jobs sorted), the retry batch's identity."""
    jobs = sorted(request.jobs, key=_job_order)
    return RetryOrigin(source_batch_id=request.batch_id, jobs=tuple(jobs))


def _job_order(job: RetryJob) -> tuple[str, str]:
    return job.item_id, job.language


def plan_retry(session: Session, origin: RetryOrigin, request: CreateRetryBatchRequest) -> RetryPlan:
    """Check the source and the jobs; return the retry batch to create.

    Raises:
        BatchNotFoundError: There is no source batch.
        AudioDeletedError: The source's audio was cleaned up.
        WrongPhaseError: The source has no results yet (not `transcribed` or `downloaded`).
        RetryJobInvalidError: A job is not a failed or no-speech transcribe job of the source.
        CopySourceMissingError: The source has no verified audio object for a job's track.
    """
    row = load_batch(session, origin.source_batch_id)
    if row.audio_deleted:
        raise AudioDeletedError(row.batch_id)
    require_phase(row, RETRY_SOURCE_PHASES)
    members = stored_items(session, row.batch_id)
    chosen = _chosen_jobs(members, request.jobs)
    audio = _source_audio(session, row.batch_id)
    taken = [member for member in members if member.item_id in chosen]
    retried = [_retry_item(member, chosen, audio) for member in taken]
    source = BatchPayload.model_validate(row.payload)
    languages = sorted({job.language for job in origin.jobs})
    # A retry batch is not composed from a selection, so it keeps none of the source's.
    changes = {"languages": tuple(languages), "retry_of": origin, "composition": None}
    payload = source.model_copy(update=changes)
    return RetryPlan(payload, tuple(retried))


# The jobs a retry takes, by source item id.
type ChosenJobs = dict[str, list[StoredJob]]


def _chosen_jobs(members: list[StoredItem], wanted: tuple[RetryJob, ...]) -> ChosenJobs:
    """Return the source's jobs the request names, by item id (jobs in the item's language order)."""
    every_job = [(member.item_id, job) for member in members for job in member.jobs]
    by_key = {(item_id, job.row.language): job for item_id, job in every_job}
    chosen: defaultdict[str, list[StoredJob]] = defaultdict(list)
    for index, job_ref in enumerate(wanted):
        found = by_key.get((job_ref.item_id, job_ref.language))
        if found is None or found.kind != "transcribe":
            raise RetryJobInvalidError(index, REASON_NO_SUCH_JOB)
        if not flagged_job(found.kind, found.row.status):
            raise RetryJobInvalidError(index, REASON_NOT_FLAGGED)
        chosen[job_ref.item_id].append(found)
    for jobs in chosen.values():
        jobs.sort(key=_language)
    return dict(chosen)


def _language(job: StoredJob) -> str:
    return job.row.language


def _source_audio(session: Session, batch_id: str) -> dict[str, ItemAudio]:
    """Return the source's verified audio objects by item id and track."""
    verified = (AudioObjectRow.batch_id == batch_id, AudioObjectRow.status == AUDIO_VERIFIED)
    found: defaultdict[str, ItemAudio] = defaultdict(dict)
    for audio_row in session.scalars(select(AudioObjectRow).where(*verified)):
        audio = AudioObjectPayload.model_validate(audio_row.payload)
        found[audio_row.item_id][audio.track_index] = (audio_row.object_key, audio)
    return found


def _retry_item(member: StoredItem, chosen: ChosenJobs, audio: dict[str, ItemAudio]) -> RetryItem:
    jobs = chosen[member.item_id]
    tracks = sorted({job.payload.track_index for job in jobs}, key=_track_order)
    item_audio = audio.get(member.item_id, {})
    copies: list[CopySource] = []
    for track in tracks:
        if track not in item_audio:
            raise CopySourceMissingError(member.item_id, track)
        key, payload = item_audio[track]
        copies.append(CopySource(source_key=key, audio=payload))
    return RetryItem(member, tuple(jobs), tuple(copies))


def _track_order(track: int | None) -> int:
    return -1 if track is None else track
