"""Reading batches from the database and turning their rows into response models."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_ui.batches.choices import choice_problem
from transcribe_ui.batches.constants import ITEM_REVIEW_PHASES
from transcribe_ui.batches.payloads import BatchPayload, ItemPayload, ItemPhase, JobKind, JobPayload
from transcribe_ui.batches.responses import BatchDetail, BatchItem, BatchSummary, JobView
from transcribe_ui.state.models import AudioObjectRow, BatchPhase, BatchRow, ExtractedSubtitleRow, ItemRow, JobRow

# A job's (language, kind), as the batch summary counts it.
type JobKey = tuple[str, str]


@dataclass(frozen=True)
class StoredJob:
    """A job row with its payload read."""

    row: JobRow
    kind: JobKind
    payload: JobPayload


@dataclass(frozen=True)
class StoredItem:
    """An item row with its payload and jobs read."""

    row: ItemRow
    payload: ItemPayload
    jobs: tuple[StoredJob, ...]

    @property
    def item_id(self) -> str:
        """Return the item's id."""
        return self.row.item_id

    @property
    def phase(self) -> ItemPhase:
        """Return the item's phase."""
        return cast("ItemPhase", self.row.phase)

    def job(self, language: str) -> StoredJob | None:
        """Return the item's job in the language, or None."""
        found = [job for job in self.jobs if job.row.language == language]
        return found[0] if found else None

    def resolved(self) -> bool:
        """Return whether the item is probed without a problem and every job's choice fits it."""
        payload = self.payload
        problems = [choice_problem(payload, job.kind, job.payload) for job in self.jobs]
        return all(problem is None for problem in problems)

    def in_review(self) -> bool:
        """Return whether the item's choices can still change."""
        return self.phase in ITEM_REVIEW_PHASES

    def languages(self) -> frozenset[str]:
        """Return the languages of the item's jobs."""
        return frozenset(job.row.language for job in self.jobs)

    def has_subtitles(self) -> bool:
        """Return whether the probe found embedded subtitles (the first review view)."""
        probe = self.payload.probe
        return probe is not None and bool(probe.subtitle_streams)

    def has_track_choice(self) -> bool:
        """Return whether the item is a video with more than one audio track (the second review view)."""
        probe = self.payload.probe
        tracks = () if probe is None else probe.audio_tracks
        return self.payload.kind == "video" and len(tracks) > 1


def stored_job(row: JobRow) -> StoredJob:
    """Return the job row with its payload read."""
    kind = cast("JobKind", row.kind)
    return StoredJob(row, kind, JobPayload.model_validate(row.payload))


def _stored_item(row: ItemRow, jobs: list[StoredJob]) -> StoredItem:
    return StoredItem(row, ItemPayload.model_validate(row.payload), tuple(jobs))


def stored_item(session: Session, item_id: str) -> StoredItem:
    """Return one item with its jobs (jobs by language); the caller knows that it exists."""
    row = session.get(ItemRow, item_id)
    assert row is not None, "the item is locked by its batch lock"
    of_item = JobRow.item_id == item_id
    jobs_query = select(JobRow).where(of_item).order_by(JobRow.language)
    jobs = [stored_job(job_row) for job_row in session.scalars(jobs_query)]
    return _stored_item(row, jobs)


def items_with_uploads(session: Session, batch_id: str) -> frozenset[str]:
    """Return the ids of the batch's items that have `audio_objects` or `extracted_subtitles` rows."""
    audio = select(AudioObjectRow.item_id).where(AudioObjectRow.batch_id == batch_id)
    extracted = (
        select(JobRow.item_id)
        .join(ExtractedSubtitleRow, ExtractedSubtitleRow.job_id == JobRow.job_id)
        .where(JobRow.batch_id == batch_id)
    )
    return frozenset(session.scalars(audio.union(extracted)))


def stored_items(session: Session, batch_id: str) -> list[StoredItem]:
    """Return the batch's items with their jobs, in selection order (jobs by language)."""
    jobs_by_item: defaultdict[str, list[StoredJob]] = defaultdict(list)
    of_batch = JobRow.batch_id == batch_id
    jobs_query = select(JobRow).where(of_batch).order_by(JobRow.language)
    for job_row in session.scalars(jobs_query):
        jobs_by_item[job_row.item_id].append(stored_job(job_row))
    items_query = select(ItemRow).where(ItemRow.batch_id == batch_id)
    item_rows = session.scalars(items_query)
    found = [_stored_item(item_row, jobs_by_item[item_row.item_id]) for item_row in item_rows]
    return sorted(found, key=lambda stored: stored.payload.position)


def job_view(member: ItemPayload, job: StoredJob) -> JobView:
    """Return a job as responses show it."""
    payload = job.payload
    return JobView(
        language=job.row.language,
        kind=job.kind,
        mode=payload.mode,
        track_index=payload.track_index,
        stream_index=payload.stream_index,
        status=job.row.status,
        resolved=choice_problem(member, job.kind, payload) is None,
        result_error=payload.result_error,
    )


def batch_item(member: StoredItem) -> BatchItem:
    """Return an item as responses show it."""
    payload = member.payload
    return BatchItem(
        item_id=member.item_id,
        path=payload.path,
        name=payload.name,
        kind=payload.kind,
        size_bytes=payload.size_bytes,
        phase=member.phase,
        subtitle_languages=payload.subtitle_languages,
        problem=payload.problem,
        jobs=tuple(job_view(payload, job) for job in member.jobs),
    )


def batch_summary(row: BatchRow, item_count: int, jobs: Sequence[JobKey]) -> BatchSummary:
    """Return a batch as the batch list shows it, from its item count and its jobs' (language, kind)."""
    languages = sorted({language for language, _kind in jobs})
    return BatchSummary(
        batch_id=row.batch_id,
        phase=cast("BatchPhase", row.phase),
        created_at=row.created_at,
        updated_at=row.updated_at,
        items=item_count,
        jobs=len(jobs),
        languages=tuple(languages),
        hidden=row.hidden,
        manifest_held=row.manifest_held,
        audio_deleted=row.audio_deleted,
        cleanup_pending=BatchPayload.model_validate(row.payload).cleanup_pending,
        extract_only=_extract_only(jobs),
    )


def _extract_only(jobs: Sequence[JobKey]) -> bool:
    kinds = {kind for _language, kind in jobs}
    return kinds == {"extract"}


def summary_of(row: BatchRow, members: Sequence[StoredItem]) -> BatchSummary:
    """Return the summary of a batch whose items are read."""
    stored = [job.row for member in members for job in member.jobs]
    jobs = [(row.language, row.kind) for row in stored]
    return batch_summary(row, len(members), jobs)


def batch_detail(row: BatchRow, members: Sequence[StoredItem]) -> BatchDetail:
    """Return a batch with its items and jobs."""
    payload = BatchPayload.model_validate(row.payload)
    return BatchDetail(
        summary=summary_of(row, members),
        scan_root=payload.scan_root,
        path_flavor=payload.path_flavor,
        items=tuple(batch_item(member) for member in members),
    )


def load_detail(session: Session, row: BatchRow) -> BatchDetail:
    """Read the batch's items and return the batch with them."""
    return batch_detail(row, stored_items(session, row.batch_id))
