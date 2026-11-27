"""A batch as the downloads see it: its jobs as the planner's input (plan m5 §1 "Download").

Each job's subtitle state comes from the database: a transcribe job's status as `refresh_status`
recorded it from its result (`ok`, `no_speech`, `failed`; `pending` until then), an extract job's
stored SRT. A `ready` batch therefore offers the jobs whose results the last refresh saw (requirements "Subtitle download options"). A
`cleaned` batch has no results in R2 any more: only its extract jobs are planned.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.payloads import BatchPayload, JobKind
from transcribe_ui.batches.phases import load_batch, require_phase
from transcribe_ui.batches.records import StoredItem, StoredJob, stored_items, summary_of
from transcribe_ui.batches.responses import BatchSummary
from transcribe_ui.placement.classes import PlacementJob, SubtitleState
from transcribe_ui.placement.constants import CLEANED_PHASE, DOWNLOAD_PHASES, EXTRACT_KIND, TRANSCRIBE_SUBTITLES
from transcribe_ui.state.models import ExtractedSubtitleRow, JobRow

# A job's identity in a plan: its item and language.
type JobKey = tuple[str, str]


@dataclass(frozen=True)
class DownloadJob:
    """A job of the batch with what the planner needs, and its row id and kind for the download."""

    job_id: int
    kind: JobKind
    job: PlacementJob

    @property
    def key(self) -> JobKey:
        """Return the job's item and language."""
        return self.job.item_id, self.job.language


@dataclass(frozen=True)
class DownloadSource:
    """A batch whose subtitles can be downloaded, read in one transaction."""

    batch_id: str
    summary: BatchSummary
    scan_root: str
    jobs: tuple[DownloadJob, ...]

    def placement_jobs(self) -> list[PlacementJob]:
        """Return the planner's input: every job, items in selection order, jobs by language."""
        return [each.job for each in self.jobs]

    def by_key(self) -> dict[JobKey, DownloadJob]:
        """Return the jobs by item and language."""
        return {each.key: each for each in self.jobs}


def load_source(context: RequestContext, batch_id: str) -> DownloadSource:
    """Read the batch and its jobs.

    Raises:
        BatchNotFoundError: There is no such batch.
        WrongPhaseError: The batch is not in a phase whose subtitles can be downloaded.
    """
    with context.ports.state.session() as session:
        row = load_batch(session, batch_id)
        require_phase(row, DOWNLOAD_PHASES)
        members = stored_items(session, batch_id)
        stored = select(ExtractedSubtitleRow.job_id).join(JobRow)
        of_batch = JobRow.batch_id == batch_id
        extracted = frozenset(session.scalars(stored.where(of_batch)))
        summary = summary_of(session, row, members)
        scan_root = BatchPayload.model_validate(row.payload).scan_root
        cleaned = row.phase == CLEANED_PHASE
    jobs = _download_jobs(members, extracted, cleaned=cleaned)
    return DownloadSource(batch_id=batch_id, summary=summary, scan_root=scan_root, jobs=tuple(jobs))


def _download_jobs(members: Sequence[StoredItem], extracted: frozenset[int], *, cleaned: bool) -> list[DownloadJob]:
    found: list[DownloadJob] = []
    for member in members:
        for stored in member.jobs:
            if cleaned and stored.kind != EXTRACT_KIND:
                continue
            job = PlacementJob(
                item_id=member.item_id,
                language=stored.row.language,
                source_path=member.payload.path,
                subtitle=_subtitle(stored, extracted),
            )
            job_id = stored.row.job_id
            found.append(DownloadJob(job_id=job_id, kind=stored.kind, job=job))
    return found


def _subtitle(stored: StoredJob, extracted: frozenset[int]) -> SubtitleState:
    if stored.kind == EXTRACT_KIND:
        return "available" if stored.row.job_id in extracted else "pending"
    return TRANSCRIBE_SUBTITLES.get(stored.row.status, "pending")
