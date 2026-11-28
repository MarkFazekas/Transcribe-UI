"""Handlers of the batch requests that compose a batch: create, edit, delete, start the review (§3.3).

Every request that changes a batch holds its batch lock (never waited for: `batch_busy`) and
changes it by compare-and-set (`batches/phases.py`). `create_batch` needs no lock: nobody can
know the new id before its transaction commits, and the id is picked inside that transaction.
"""

from typing import cast

from sqlalchemy import CursorResult, delete

from transcribe_ui.api.classes import NeedsConfirmation
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.classes import (
    BatchComposition,
    CreateBatchRequest,
    DeleteBatchRequest,
    EditBatchRequest,
    StartReviewRequest,
)
from transcribe_ui.batches.composition import Composition, compose
from transcribe_ui.batches.confirmations import delete_confirmation
from transcribe_ui.batches.constants import (
    COMPOSING,
    DELETABLE_PHASES,
    EDITABLE_PHASES,
    REVIEWING,
    START_REVIEW_PHASES,
)
from transcribe_ui.batches.ids import base_batch_id, free_batch_id
from transcribe_ui.batches.item_rows import add_items, batch_payload
from transcribe_ui.batches.phases import load_batch, require_phase, set_item_phases, touch_batch
from transcribe_ui.batches.records import load_detail, stored_items, summary_of
from transcribe_ui.batches.responses import (
    CreateBatchResponse,
    DeleteBatchResponse,
    EditBatchResponse,
    StartReviewResponse,
)
from transcribe_ui.state.database import compare_and_set_phase
from transcribe_ui.state.models import BatchRow, ItemRow


def _confirmation_needed(composition: Composition, request: BatchComposition, action: str) -> NeedsConfirmation | None:
    confirmation = composition.confirmation()
    if confirmation is None or request.confirm_existing_languages:
        return None
    return NeedsConfirmation(action=action, confirmations=(confirmation,))


def create_batch(request: CreateBatchRequest, context: RequestContext) -> CreateBatchResponse | NeedsConfirmation:
    """Store a new batch in `composing` with the selected files; jobs for existing languages need a confirmation."""
    composition = compose(request, context, request.action)
    needed = _confirmation_needed(composition, request, request.action)
    if needed is not None:
        return needed
    ports = context.ports
    now = ports.clock.now()
    base = base_batch_id(now, ports.local_zone)
    with ports.state.session() as session:
        batch_id = free_batch_id(session, base)
        row = BatchRow(
            batch_id=batch_id,
            phase=COMPOSING,
            audio_deleted=False,
            hidden=False,
            created_at=now,
            updated_at=now,
            payload=batch_payload(composition, request),
        )
        session.add(row)
        session.flush()
        add_items(session, batch_id, composition, ports.ids)
        detail = load_detail(session, row, now=now)
    return CreateBatchResponse(batch=detail, warnings=composition.warnings)


def edit_batch(request: EditBatchRequest, context: RequestContext) -> EditBatchResponse | NeedsConfirmation:
    """Replace the items of a batch in `composing` with a new selection."""
    state = context.ports.state
    batch_id = request.batch_id
    with state.locks.batch(batch_id):
        with state.session() as session:
            require_phase(load_batch(session, batch_id), EDITABLE_PHASES)
        composition = compose(request, context, request.action)
        needed = _confirmation_needed(composition, request, request.action)
        if needed is not None:
            return needed
        with state.session() as session:
            payload = batch_payload(composition, request)
            touch_batch(
                session, batch_id, EDITABLE_PHASES, now=context.ports.clock.now(), also_set={"payload": payload}
            )
            session.execute(delete(ItemRow).where(ItemRow.batch_id == batch_id))
            add_items(session, batch_id, composition, context.ports.ids)
            now = context.ports.clock.now()
            detail = load_detail(session, load_batch(session, batch_id), now=now)
    return EditBatchResponse(batch=detail, warnings=composition.warnings)


def delete_batch(request: DeleteBatchRequest, context: RequestContext) -> DeleteBatchResponse | NeedsConfirmation:
    """Delete a batch in `composing` or `reviewing` with its items and jobs (nothing is in the cloud yet).

    Without `confirm` the answer asks first, and nothing is changed.
    """
    state = context.ports.state
    batch_id = request.batch_id
    with state.locks.batch(batch_id), state.session() as session:
        row = load_batch(session, batch_id)
        require_phase(row, DELETABLE_PHASES)
        observed = row.phase
        if not request.confirm:
            return delete_confirmation(request, observed, len(stored_items(session, batch_id)))
        session.expunge(row)
        matches = (BatchRow.batch_id == batch_id, BatchRow.phase == observed)
        statement = delete(BatchRow).where(*matches)
        outcome = cast("CursorResult[tuple[()]]", session.execute(statement))
        # The transaction holds the database's write lock since it began, so the row cannot have changed.
        assert outcome.rowcount == 1, "the batch changed inside its own write transaction"
    return DeleteBatchResponse(batch_id=batch_id)


def start_review(request: StartReviewRequest, context: RequestContext) -> StartReviewResponse:
    """Move a batch from `composing` to `reviewing` (a batch already in `reviewing` stays there)."""
    state = context.ports.state
    batch_id = request.batch_id
    with state.locks.batch(batch_id), state.session() as session:
        row = load_batch(session, batch_id)
        require_phase(row, START_REVIEW_PHASES)
        if row.phase == COMPOSING:
            compare_and_set_phase(session, batch_id, COMPOSING, REVIEWING, now=context.ports.clock.now())
            set_item_phases(session, batch_id, COMPOSING, REVIEWING)
        row = load_batch(session, batch_id)
        now = context.ports.clock.now()
        summary = summary_of(session, row, stored_items(session, batch_id), now=now)
    return StartReviewResponse(batch=summary)
