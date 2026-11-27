"""Handlers of the batch requests that read or hide batches: list, get, hide (§3.3)."""

from collections import defaultdict
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.actions import JobKey, copying_sources
from transcribe_ui.batches.classes import GetBatchRequest, ListBatchesRequest, SetBatchHiddenRequest
from transcribe_ui.batches.constants import ALL_PHASES, FINISHED_PHASES
from transcribe_ui.batches.phases import load_batch, require_phase, touch_batch
from transcribe_ui.batches.records import batch_summary, load_detail, stored_items, summary_of
from transcribe_ui.batches.responses import BatchSummary, GetBatchResponse, ListBatchesResponse, SetBatchHiddenResponse
from transcribe_ui.state.models import BatchRow, ItemRow, JobRow


def _item_phases(session: Session) -> defaultdict[str, list[str]]:
    phases: defaultdict[str, list[str]] = defaultdict(list)
    for batch_id, phase in session.execute(select(ItemRow.batch_id, ItemRow.phase)):
        phases[batch_id].append(phase)
    return phases


def _job_keys(session: Session) -> defaultdict[str, list[JobKey]]:
    jobs: defaultdict[str, list[JobKey]] = defaultdict(list)
    columns = select(JobRow.batch_id, JobRow.language, JobRow.kind, JobRow.status)
    for batch_id, language, kind, status in session.execute(columns):
        jobs[batch_id].append((language, kind, status))
    return jobs


def list_batches(request: ListBatchesRequest, context: RequestContext) -> ListBatchesResponse:
    """Return the batches, newest first; hidden ones only when asked."""
    newest_first = (BatchRow.created_at.desc(), BatchRow.batch_id.desc())
    query = select(BatchRow).order_by(*newest_first)
    if not request.include_hidden:
        query = query.where(BatchRow.hidden.is_(False))
    with context.ports.state.session() as session:
        rows = list(session.scalars(query))
        phases = _item_phases(session)
        jobs = _job_keys(session)
        copying = copying_sources(session)
    summaries = [_summary(row, phases, jobs, copying) for row in rows]
    return ListBatchesResponse(batches=tuple(summaries))


def _summary(
    row: BatchRow,
    phases: Mapping[str, list[str]],
    jobs: Mapping[str, list[JobKey]],
    copying: frozenset[str],
) -> BatchSummary:
    batch_id = row.batch_id
    retry_copying = batch_id in copying
    item_phases = phases.get(batch_id, [])
    return batch_summary(row, item_phases, jobs.get(batch_id, []), copying=retry_copying)


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
