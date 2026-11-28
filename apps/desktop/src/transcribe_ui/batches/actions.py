"""What a batch allows and shows now: `BatchSummary.actions`, `stage` and `labels` (architecture §3.3).

One table (`constants.ACTION_RULES`) names, per action, the phases its handler accepts, the
further guard conditions (item and job states, flags, a retry batch still copying the audio) and
the no-op conditions (leaving out what the handler would accept but that would change nothing,
such as showing a batch that is not hidden). The handlers keep their own guards; the table
restates them for the frontends, which build their buttons from the list alone, and a drift test
sends every action against seeded batches so the two cannot diverge silently. Conditions outside
the batch (a run is active, a lock is busy, the cloud cannot be reached, the request's own
fields) are not in the table: a listed action can still be refused.

The stage (the step of the guided flow, plan m6 §1 "Layout") and the labels (notes such as an
interrupted cleanup) come from the same facts, so no frontend derives them from the phase.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple, cast

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from transcribe_ui.batches import constants as rules
from transcribe_ui.batches.payloads import CleanupScope
from transcribe_ui.batches.responses import BatchAction, BatchLabel, BatchStage
from transcribe_ui.cleanup.constants import CLEANED, FLAGGED_STATUSES, RETRY_SOURCE_PATH
from transcribe_ui.placement.constants import RECORDED_OUTCOMES
from transcribe_ui.state.models import BatchRow, ItemRow, JobRow, PlacementRow
from transcribe_ui.upload.constants import ITEM_REVIEWED, ITEM_UPLOADED, REVIEWED_BATCH, UPLOADING_BATCH


class JobKey(NamedTuple):
    """A job of a batch as the summary reads it."""

    language: str
    kind: str
    status: str
    # A download recorded the job's placement outcome (placed, or skipped and why).
    placed: bool = False

    @classmethod
    def of_row(cls, job: JobRow, placed: frozenset[int]) -> "JobKey":
        """Return a job row's key; `placed` holds the ids of the jobs with a placement outcome."""
        recorded = job.job_id in placed
        return cls(job.language, job.kind, job.status, placed=recorded)


@dataclass(frozen=True)
class ItemCounts:
    """How many items a batch has, how many are not `reviewed`, and how many are in review."""

    total: int = 0
    not_reviewed: int = 0
    in_review: int = 0

    @classmethod
    def of_phases(cls, phases: Sequence[str]) -> "ItemCounts":
        """Return the counts of a batch whose items' phases are read."""
        not_reviewed = sum(1 for phase in phases if phase != ITEM_REVIEWED)
        in_review = sum(1 for phase in phases if phase in rules.ITEM_REVIEW_PHASES)
        return cls(total=len(phases), not_reviewed=not_reviewed, in_review=in_review)


@dataclass(frozen=True)
class JobCounts:
    """How many transcribe jobs wait for a result or are flagged, how many jobs extract, how many have a subtitle."""

    pending_results: int
    flagged: int
    extract: int
    # Jobs whose subtitle can be placed and has no placement outcome yet: a transcription's
    # result, or an extracted subtitle.
    subtitles: int = 0

    @classmethod
    def of_jobs(cls, jobs: Sequence[JobKey]) -> "JobCounts":
        """Return the counts of a batch's jobs."""
        transcribing = [job.status for job in jobs if job.kind == "transcribe"]
        unplaced = [job for job in jobs if not job.placed]
        return cls(
            pending_results=transcribing.count(rules.JOB_PENDING),
            flagged=sum(1 for job in jobs if flagged_job(job.kind, job.status)),
            extract=sum(1 for job in jobs if job.kind == "extract"),
            subtitles=sum(1 for job in unplaced if job.status in rules.SUBTITLE_STATUSES),
        )


@dataclass(frozen=True)
class BatchFacts:
    """What the table's conditions read of a batch."""

    phase: str
    audio_deleted: bool
    hidden: bool
    manifest_held: bool
    cleanup_pending: CleanupScope | None
    item_counts: ItemCounts
    job_counts: JobCounts
    # A retry batch of this batch is `uploading` with an item not copied yet.
    copying_retry: bool


def flagged_job(kind: str, status: str) -> bool:
    """Return whether a job failed or found no speech, so a retry batch can take it (`JobView.flagged`)."""
    return kind == "transcribe" and status in FLAGGED_STATUSES


def placed_job_ids(session: Session, *batches: ColumnElement[bool]) -> frozenset[int]:
    """Return the jobs with a recorded placement outcome, of the batches the conditions on `PlacementRow` pick."""
    recorded = PlacementRow.outcome.in_(RECORDED_OUTCOMES)
    query = select(PlacementRow.job_id).where(recorded, *batches)
    return frozenset(session.scalars(query))


def stored_cleanup_pending(row: BatchRow) -> CleanupScope | None:
    """Return the batch payload's pending cleanup scope, read without validating the whole payload."""
    payload = row.payload
    found = payload.get("cleanup_pending") if isinstance(payload, dict) else None
    return cast("CleanupScope", found) if found in rules.CLEANUP_SCOPES else None


def batch_facts(row: BatchRow, item_counts: ItemCounts, jobs: Sequence[JobKey], *, copying: bool) -> BatchFacts:
    """Return the facts of a batch from its row, its item counts and its jobs."""
    return BatchFacts(
        phase=row.phase,
        audio_deleted=row.audio_deleted,
        hidden=row.hidden,
        manifest_held=row.manifest_held,
        cleanup_pending=stored_cleanup_pending(row),
        item_counts=item_counts,
        job_counts=JobCounts.of_jobs(jobs),
        copying_retry=copying,
    )


def allowed_actions(facts: BatchFacts) -> tuple[BatchAction, ...]:
    """Return the actions whose phase and conditions hold for the batch, in the table's order."""
    allowed: list[BatchAction] = []
    for action, (phases, conditions) in rules.ACTION_RULES.items():
        if facts.phase not in phases:
            continue
        held = [_holds(condition, facts) for condition in conditions]
        if all(held):
            allowed.append(cast("BatchAction", action))
    return tuple(allowed)


def batch_stage(facts: BatchFacts) -> BatchStage:
    """Return the step of the guided flow the batch is at: by its phase, a held upload already at transcription."""
    if facts.phase == UPLOADING_BATCH and facts.manifest_held:
        return rules.STAGE_WHEN_HELD
    return rules.PHASE_STAGES[facts.phase]


def batch_labels(facts: BatchFacts) -> tuple[BatchLabel, ...]:
    """Return the labels that apply to the batch, in the order to show them."""
    uploading = facts.phase == UPLOADING_BATCH
    held = facts.manifest_held
    jobs = facts.job_counts
    results_in = facts.phase in rules.RESULT_PHASES
    applies: dict[BatchLabel, bool] = {
        rules.LABEL_CLEANUP_PENDING: facts.cleanup_pending is not None,
        rules.LABEL_UPLOAD_UNFINISHED: uploading and not held,
        rules.LABEL_MANIFEST_HELD: uploading and held,
        rules.LABEL_SUBTITLES_READY: results_in and jobs.subtitles > 0,
        rules.LABEL_FLAGGED_JOBS: jobs.flagged > 0,
        rules.LABEL_AUDIO_DELETED: facts.audio_deleted and facts.phase != CLEANED,
        rules.LABEL_HIDDEN: facts.hidden,
    }
    return tuple(label for label, holds in applies.items() if holds)


def _holds(condition: str, facts: BatchFacts) -> bool:
    """Return whether a condition of the table holds (an unknown one is a programming error: `KeyError`)."""
    counted = facts.item_counts
    jobs = facts.job_counts
    pending = facts.cleanup_pending
    cleaned = facts.phase == CLEANED
    checks = {
        rules.CONDITION_SEVERAL_ITEMS: counted.total > 1,
        rules.CONDITION_REVIEW_COMPLETE: facts.phase != REVIEWED_BATCH or counted.not_reviewed == 0,
        rules.CONDITION_RESULTS_IN: jobs.pending_results == 0,
        rules.CONDITION_FLAGGED_JOBS: jobs.flagged > 0,
        rules.CONDITION_AUDIO_KEPT: not facts.audio_deleted,
        rules.CONDITION_NO_COPYING_RETRY: not facts.copying_retry,
        rules.CONDITION_REVIEW_NOT_STARTED: facts.phase == rules.COMPOSING,
        rules.CONDITION_ITEMS_IN_REVIEW: counted.in_review > 0,
        rules.CONDITION_VISIBLE: not facts.hidden,
        rules.CONDITION_HIDDEN: facts.hidden,
        rules.CONDITION_AUDIO_LEFT: not facts.audio_deleted or pending == "audio",
        rules.CONDITION_CLOUD_DATA_LEFT: not cleaned or pending == "everything",
        rules.CONDITION_SUBTITLES_LEFT: not cleaned or jobs.extract > 0,
    }
    return checks[condition]


def copying_sources(session: Session) -> frozenset[str]:
    """Return the batches a retry batch still copies audio from: it is `uploading` with an item not copied yet."""
    source_of = func.json_extract(BatchRow.payload, RETRY_SOURCE_PATH)
    waiting = select(ItemRow.batch_id).where(ItemRow.phase != ITEM_UPLOADED)
    retry_batch = source_of.is_not(None)
    copying = (BatchRow.phase == UPLOADING_BATCH, retry_batch, BatchRow.batch_id.in_(waiting))
    return frozenset(session.scalars(select(source_of).where(*copying)))
