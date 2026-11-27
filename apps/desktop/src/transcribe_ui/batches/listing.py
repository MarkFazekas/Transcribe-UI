"""Handlers of the batch requests that read or hide batches: list, get, hide (§3.3)."""

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.classes import GetBatchRequest, ListBatchesRequest, SetBatchHiddenRequest
from transcribe_ui.batches.constants import ALL_PHASES, FINISHED_PHASES
from transcribe_ui.batches.phases import load_batch, require_phase, touch_batch
from transcribe_ui.batches.records import JobKey, batch_summary, load_detail, stored_items, summary_of
from transcribe_ui.batches.responses import GetBatchResponse, ListBatchesResponse, SetBatchHiddenResponse
from transcribe_ui.state.models import BatchRow, ItemRow, JobRow


def _item_counts(session: Session) -> dict[str, int]:
    counts = select(ItemRow.batch_id, func.count()).group_by(ItemRow.batch_id)
    return dict(session.execute(counts).all())


def _job_keys(session: Session) -> defaultdict[str, list[JobKey]]:
    jobs: defaultdict[str, list[JobKey]] = defaultdict(list)
    columns = select(JobRow.batch_id, JobRow.language, JobRow.kind)
    for batch_id, language, kind in session.execute(columns):
        jobs[batch_id].append((language, kind))
    return jobs


def list_batches(request: ListBatchesRequest, context: RequestContext) -> ListBatchesResponse:
    """Return the batches, newest first; hidden ones only when asked."""
    newest_first = (BatchRow.created_at.desc(), BatchRow.batch_id.desc())
    query = select(BatchRow).order_by(*newest_first)
    if not request.include_hidden:
        query = query.where(BatchRow.hidden.is_(False))
    with context.ports.state.session() as session:
        rows = list(session.scalars(query))
        counts = _item_counts(session)
        jobs = _job_keys(session)
    summaries = []
    for row in rows:
        batch_id = row.batch_id
        count = counts.get(batch_id, 0)
        summaries.append(batch_summary(row, count, jobs[batch_id]))
    return ListBatchesResponse(batches=tuple(summaries))


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
        summary = summary_of(row, stored_items(session, batch_id))
    return SetBatchHiddenResponse(batch=summary)
