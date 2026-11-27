"""`exclude_items`: remove items from a batch in `reviewing` or `uploading` (plan m5 §1 "Per-item re-review").

In an `uploading` batch the object store is opened first, so a credentials error changes
nothing; a leftover manifest of ours (an interrupted manifest step) is then withdrawn under the
runs lock while no run is active (`upload/withdraw.py`), else the request is refused with
nothing changed. Then one transaction clears `manifest_held` (recording the pending withdrawal)
and deletes the items with their
`audio_objects` and `extracted_subtitles` rows: from then on nothing lists their audio, and
the intent to exclude cannot be lost. Only then are the objects under each item's audio prefix
deleted (only an `uploading` batch has any), with the item's open multipart uploads aborted.
Objects an interruption leaves behind belong to no item; the batch-prefix cleanup or the next
`run_upload` (M5.10) removes them. The last item cannot be excluded; when only `extract` jobs
remain, the batch is extract-only (no upload, no manifest; its summary says so) and the upload
takes it to `transcribed` without a manifest.
"""

from sqlalchemy import delete, select

from transcribe_ui.api.classes import NeedsConfirmation, ProgressEvent
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.classes import ExcludeItemsRequest
from transcribe_ui.batches.confirmations import exclude_confirmation
from transcribe_ui.batches.constants import DELETE_OBJECTS_STAGE, REVIEW_PHASES, UPLOADING_PHASES
from transcribe_ui.batches.errors import ItemNotFoundError, LastItemError
from transcribe_ui.batches.objects import delete_item_objects, forget_uploads, object_store
from transcribe_ui.batches.phases import load_batch, require_phase, touch_batch
from transcribe_ui.batches.records import load_detail, stored_items
from transcribe_ui.batches.responses import BatchDetail, ExcludeItemsResponse
from transcribe_ui.state.models import AudioObjectRow, ItemRow
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.steps import finish_after_change
from transcribe_ui.upload.withdraw import recorded_withdrawal, withdrawal_guard


def _checked_phase(request: ExcludeItemsRequest, context: RequestContext) -> str:
    """Check the request (nothing is changed); return the batch's phase.

    Raises:
        WrongPhaseError: If the batch is not in `reviewing` or `uploading`.
        ItemNotFoundError: If an item id is not an item of the batch.
        LastItemError: If no item would be left.
    """
    batch_id = request.batch_id
    with context.ports.state.session() as session:
        row = load_batch(session, batch_id)
        require_phase(row, REVIEW_PHASES)
        present = {member.item_id for member in stored_items(session, batch_id)}
        unknown = set(request.item_ids) - present
        if unknown:
            raise ItemNotFoundError(batch_id, unknown)
        if present <= set(request.item_ids):
            raise LastItemError(batch_id)
        return row.phase


def _remove_items(
    request: ExcludeItemsRequest, context: RequestContext, phase: str, leftover: str | None
) -> BatchDetail:
    """Clear `manifest_held` and delete the items with their upload rows, in one transaction; return the batch.

    With a leftover manifest (its digest), the same transaction records the pending withdrawal.
    """
    batch_id = request.batch_id
    with context.ports.state.session() as session:
        now = context.ports.clock.now()
        changes: dict[str, object] = {"manifest_held": False}
        if leftover is not None:
            changes["pending_withdrawal"] = recorded_withdrawal(session, batch_id, leftover, request.item_ids)
        touch_batch(session, batch_id, (phase,), now=now, also_set=changes)
        forget_uploads(session, batch_id, request.item_ids)
        excluded = ItemRow.item_id.in_(request.item_ids)
        of_batch = ItemRow.batch_id == batch_id
        session.execute(delete(ItemRow).where(of_batch, excluded))
        return load_detail(session, load_batch(session, batch_id))


def _has_uploaded_audio(request: ExcludeItemsRequest, context: RequestContext) -> bool:
    """Return whether an audio object is recorded for one of the items to exclude."""
    excluded = AudioObjectRow.item_id.in_(request.item_ids)
    recorded = select(AudioObjectRow.object_key).where(excluded)
    with context.ports.state.session() as session:
        return session.scalar(recorded.limit(1)) is not None


def _delete_objects(request: ExcludeItemsRequest, context: RequestContext, store: ObjectStore) -> int:
    deleted = 0
    total = len(request.item_ids)
    for done, item_id in enumerate(request.item_ids, start=1):
        deleted += delete_item_objects(store, request.batch_id, item_id, context.check_cancelled)
        event = ProgressEvent(action=request.action, stage=DELETE_OBJECTS_STAGE, completed=done, total=total)
        context.progress.emit(event)
    return deleted


def exclude_items(request: ExcludeItemsRequest, context: RequestContext) -> ExcludeItemsResponse | NeedsConfirmation:
    """Remove the items from the batch, then delete their uploaded audio; refuses to remove the last item.

    Without `confirm` the answer asks first, and nothing is changed.
    """
    with context.ports.state.locks.batch(request.batch_id):
        phase = _checked_phase(request, context)
        if not request.confirm:
            uploaded = _has_uploaded_audio(request, context)
            return exclude_confirmation(request, phase, uploaded_audio=uploaded)
        if phase not in UPLOADING_PHASES:
            return ExcludeItemsResponse(batch=_remove_items(request, context, phase, None), deleted_objects=0)
        store = object_store(context.ports)
        with withdrawal_guard(context, store, request.batch_id) as leftover:
            detail = _remove_items(request, context, phase, leftover)
            pending = leftover is not None and finish_after_change(context, store, request.batch_id)
        deleted = _delete_objects(request, context, store)
    return ExcludeItemsResponse(batch=detail, deleted_objects=deleted, withdrawal_pending=pending)
