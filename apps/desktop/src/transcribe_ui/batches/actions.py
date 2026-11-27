"""The batch requests a batch allows now (`BatchSummary.actions`, architecture §3.3 "Allowed actions").

One table (`constants.ACTION_RULES`) names, per action, the phases its handler accepts and the
further local conditions: item and job states, flags, a retry batch still copying the audio. The
handlers keep their own guards; the table restates them for the frontends, which build their
buttons from it instead of re-deriving the rules, and a drift test sends every action against
seeded batches so the two cannot diverge silently. Conditions outside the batch (a run is active,
a lock is busy, the cloud cannot be reached, the request's own fields) are not in the table: a
listed action can still be refused.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from transcribe_ui.batches.constants import (
    ACTION_RULES,
    CONDITION_AUDIO_KEPT,
    CONDITION_FLAGGED_JOBS,
    CONDITION_NO_COPYING_RETRY,
    CONDITION_RESULTS_IN,
    CONDITION_REVIEW_COMPLETE,
    CONDITION_SEVERAL_ITEMS,
    JOB_PENDING,
)
from transcribe_ui.batches.responses import BatchAction
from transcribe_ui.cleanup.constants import FLAGGED_STATUSES, RETRY_SOURCE_PATH
from transcribe_ui.state.models import BatchRow, ItemRow
from transcribe_ui.upload.constants import ITEM_REVIEWED, ITEM_UPLOADED, REVIEWED_BATCH, UPLOADING_BATCH

# A job of a batch as the summary reads it: (language, kind, status).
type JobKey = tuple[str, str, str]


@dataclass(frozen=True)
class BatchFacts:
    """What the table's conditions read of a batch."""

    phase: str
    audio_deleted: bool
    items: int  # noqa: WPS110 -- the count of items, as in `BatchSummary`
    # Items not `reviewed` (in a `reviewing` batch: not probed, unresolved or with a problem).
    items_not_reviewed: int
    # Transcribe jobs without a result.
    pending_results: int
    # Transcribe jobs that failed or found no speech.
    flagged_jobs: int
    # A retry batch of this batch is `uploading` with an item not copied yet.
    copying_retry: bool


def batch_facts(row: BatchRow, item_phases: Sequence[str], jobs: Sequence[JobKey], *, copying: bool) -> BatchFacts:
    """Return the facts of a batch from its row, its items' phases and its jobs."""
    transcribing = [status for _language, kind, status in jobs if kind == "transcribe"]
    return BatchFacts(
        phase=row.phase,
        audio_deleted=row.audio_deleted,
        items=len(item_phases),
        items_not_reviewed=sum(1 for phase in item_phases if phase != ITEM_REVIEWED),
        pending_results=transcribing.count(JOB_PENDING),
        flagged_jobs=sum(1 for status in transcribing if status in FLAGGED_STATUSES),
        copying_retry=copying,
    )


def allowed_actions(facts: BatchFacts) -> tuple[BatchAction, ...]:
    """Return the actions whose phase and conditions hold for the batch, in the table's order."""
    allowed: list[BatchAction] = []
    for action, (phases, conditions) in ACTION_RULES.items():
        if facts.phase not in phases:
            continue
        held = [_holds(condition, facts) for condition in conditions]
        if all(held):
            allowed.append(cast("BatchAction", action))
    return tuple(allowed)


def _holds(condition: str, facts: BatchFacts) -> bool:
    """Return whether a condition of the table holds (an unknown one is a programming error: `KeyError`)."""
    checks = {
        CONDITION_SEVERAL_ITEMS: facts.items > 1,
        CONDITION_REVIEW_COMPLETE: facts.phase != REVIEWED_BATCH or facts.items_not_reviewed == 0,
        CONDITION_RESULTS_IN: facts.pending_results == 0,
        CONDITION_FLAGGED_JOBS: facts.flagged_jobs > 0,
        CONDITION_AUDIO_KEPT: not facts.audio_deleted,
        CONDITION_NO_COPYING_RETRY: not facts.copying_retry,
    }
    return checks[condition]


def copying_sources(session: Session) -> frozenset[str]:
    """Return the batches a retry batch still copies audio from: it is `uploading` with an item not copied yet."""
    source_of = func.json_extract(BatchRow.payload, RETRY_SOURCE_PATH)
    waiting = select(ItemRow.batch_id).where(ItemRow.phase != ITEM_UPLOADED)
    retry_batch = source_of.is_not(None)
    copying = (BatchRow.phase == UPLOADING_BATCH, retry_batch, BatchRow.batch_id.in_(waiting))
    return frozenset(session.scalars(select(source_of).where(*copying)))
