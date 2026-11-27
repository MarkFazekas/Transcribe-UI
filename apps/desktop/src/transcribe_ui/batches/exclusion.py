"""`exclude_items`: remove items from a batch in `reviewing` or `uploading` (plan m5 §1 "Per-item re-review").

In an `uploading` batch the object store is opened first, so a credentials error changes
nothing. Then one transaction clears `manifest_held` and deletes the items with their
`audio_objects` and `extracted_subtitles` rows: from then on nothing lists their audio, and
the intent to exclude cannot be lost. Only then are the objects under each item's audio prefix
deleted (only an `uploading` batch has any), with the item's open multipart uploads aborted.
Objects an interruption leaves behind belong to no item; the batch-prefix cleanup or the next
`run_upload` (M5.10) removes them. The last item cannot be excluded; when only `extract` jobs
remain, the batch is extract-only (no upload, no manifest; its summary says so) and the upload
takes it to `transcribed` without a manifest.
"""

from sqlalchemy import delete

from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.classes import ExcludeItemsRequest
from transcribe_ui.batches.constants import DELETE_OBJECTS_STAGE, REVIEW_PHASES, UPLOADING_PHASES
from transcribe_ui.batches.errors import ItemNotFoundError, LastItemError
from transcribe_ui.batches.objects import delete_item_objects, forget_uploads, object_store, withdraw_manifest
from transcribe_ui.batches.phases import load_batch, require_phase, touch_batch
from transcribe_ui.batches.records import load_detail, stored_items
from transcribe_ui.batches.responses import BatchDetail, ExcludeItemsResponse
from transcribe_ui.state.models import ItemRow
from transcribe_ui.storage.r2 import ObjectStore


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


def _remove_items(request: ExcludeItemsRequest, context: RequestContext, phase: str) -> BatchDetail:
    """Clear `manifest_held` and delete the items with their upload rows, in one transaction; return the batch."""
    batch_id = request.batch_id
    with context.ports.state.session() as session:
        now = context.ports.clock.now()
        touch_batch(session, batch_id, (phase,), now=now, also_set={"manifest_held": False})
        forget_uploads(session, batch_id, request.item_ids)
        excluded = ItemRow.item_id.in_(request.item_ids)
        of_batch = ItemRow.batch_id == batch_id
        session.execute(delete(ItemRow).where(of_batch, excluded))
        return load_detail(session, load_batch(session, batch_id))


def _delete_objects(request: ExcludeItemsRequest, context: RequestContext, store: ObjectStore) -> int:
    withdraw_manifest(store, request.batch_id)
    deleted = 0
    total = len(request.item_ids)
    for done, item_id in enumerate(request.item_ids, start=1):
        deleted += delete_item_objects(store, request.batch_id, item_id, context.check_cancelled)
        event = ProgressEvent(action=request.action, stage=DELETE_OBJECTS_STAGE, completed=done, total=total)
        context.progress.emit(event)
    return deleted


def exclude_items(request: ExcludeItemsRequest, context: RequestContext) -> ExcludeItemsResponse:
    """Remove the items from the batch, then delete their uploaded audio; refuses to remove the last item."""
    with context.ports.state.locks.batch(request.batch_id):
        phase = _checked_phase(request, context)
        store = object_store(context.ports) if phase in UPLOADING_PHASES else None
        detail = _remove_items(request, context, phase)
        deleted = 0 if store is None else _delete_objects(request, context, store)
    return ExcludeItemsResponse(batch=detail, deleted_objects=deleted)
