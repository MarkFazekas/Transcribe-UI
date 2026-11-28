"""Handlers of the batch requests that read or hide batches: list, get, hide (§3.3)."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import cast

from sqlalchemy import ColumnElement, Select, case, func, select
from sqlalchemy.orm import Session

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.actions import ItemCounts, JobKey, copying_sources, placed_job_ids
from transcribe_ui.batches.classes import GetBatchRequest, ListBatchesRequest, SetBatchHiddenRequest
from transcribe_ui.batches.constants import ALL_PHASES, FINISHED_PHASES, ITEM_REVIEW_PHASES, RESULT_PHASES
from transcribe_ui.batches.phases import load_batch, require_phase, touch_batch
from transcribe_ui.batches.records import batch_summary, load_detail, stored_items, summary_of
from transcribe_ui.batches.responses import BatchSummary, GetBatchResponse, ListBatchesResponse, SetBatchHiddenResponse
from transcribe_ui.state.models import BatchRow, ItemRow, JobRow, PlacementRow
from transcribe_ui.upload.constants import ITEM_REVIEWED

# The conditions that pick the listed batches (none: every batch).
type Listing = Sequence[ColumnElement[bool]]
# Per listed batch: its id, item count, items not `reviewed` and items in review.
type CountsQuery = Select[str, int, int, int]


def _counts_query(listed: Listing) -> CountsQuery:
    """Return the grouped query of the listed batches' item counts: all, not `reviewed`, in review."""
    phase = ItemRow.phase
    waiting = case((phase != ITEM_REVIEWED, 1), else_=0)
    reviewing = case((phase.in_(ITEM_REVIEW_PHASES), 1), else_=0)
    sums = (func.sum(waiting), func.sum(reviewing))
    columns = select(ItemRow.batch_id, func.count(), *sums)
    joined = columns.join(BatchRow).where(*listed)
    return cast("CountsQuery", joined.group_by(ItemRow.batch_id))


def _item_counts(session: Session, listed: Listing) -> dict[str, ItemCounts]:
    """Return the item counts of the listed batches, one grouped query."""
    counts = {}
    query = _counts_query(listed)
    for batch_id, total, not_reviewed, in_review in session.execute(query):
        counts[batch_id] = ItemCounts(total, not_reviewed, in_review)
    return counts


def _placed(session: Session, listed: Listing) -> frozenset[int]:
    """Return the listed jobs with a placement outcome; only batches with results can have placed subtitles."""
    phases = BatchRow.phase.in_(RESULT_PHASES)
    with_results = select(BatchRow.batch_id).where(*listed, phases)
    return placed_job_ids(session, PlacementRow.batch_id.in_(with_results))


def _job_keys(session: Session, listed: Listing) -> defaultdict[str, list[JobKey]]:
    jobs: defaultdict[str, list[JobKey]] = defaultdict(list)
    placed = _placed(session, listed)
    keyed = (JobRow.language, JobRow.kind, JobRow.status)
    columns = select(JobRow.batch_id, JobRow.job_id, *keyed)
    for job in session.execute(columns.join(BatchRow).where(*listed)):
        jobs[job.batch_id].append(JobKey.of_row(job, placed))
    return jobs


def list_batches(request: ListBatchesRequest, context: RequestContext) -> ListBatchesResponse:
    """Return the batches, newest first; hidden ones only when asked."""
    newest_first = (BatchRow.created_at.desc(), BatchRow.batch_id.desc())
    listed = [] if request.include_hidden else [BatchRow.hidden.is_(False)]
    query = select(BatchRow).where(*listed).order_by(*newest_first)
    with context.ports.state.session() as session:
        rows = list(session.scalars(query))
        counts = _item_counts(session, listed)
        jobs = _job_keys(session, listed)
        copying = copying_sources(session)
    summaries = [_summary(row, counts, jobs, copying) for row in rows]
    return ListBatchesResponse(batches=tuple(summaries))


def _summary(
    row: BatchRow,
    counts: Mapping[str, ItemCounts],
    jobs: Mapping[str, list[JobKey]],
    copying: frozenset[str],
) -> BatchSummary:
    batch_id = row.batch_id
    retry_copying = batch_id in copying
    item_counts = counts.get(batch_id, ItemCounts())
    return batch_summary(row, item_counts, jobs.get(batch_id, []), copying=retry_copying)


def get_batch(request: GetBatchRequest, context: RequestContext) -> GetBatchResponse:
    """Return one batch with its items and jobs."""
    with context.ports.state.session() as session:
        detail = load_detail(session, load_batch(session, request.batch_id))
    return GetBatchResponse(batch=detail)


def set_batch_hidden(request: SetBatchHiddenRequest, context: RequestContext) -> SetBatchHiddenResponse:
    """Hide a finished (`downloaded` or `cleaned`) batch from the default list, or show any batch again."""
    state = context.ports.state
    batch_id = request.batch_id
    allowed = FINISHED_PHASES if request.hidden else ALL_PHASES
    now = context.ports.clock.now()
    with state.locks.batch(batch_id), state.session() as session:
        require_phase(load_batch(session, batch_id), allowed)
        touch_batch(session, batch_id, allowed, now=now, also_set={"hidden": request.hidden})
        row = load_batch(session, batch_id)
        summary = summary_of(session, row, stored_items(session, batch_id))
    return SetBatchHiddenResponse(batch=summary)
