"""`exclude_items`: remove items from a batch in `reviewing` or `uploading` (plan m5 §1 "Per-item re-review").

Three steps, so no interruption leaves a held manifest listing audio that is gone. First one
transaction checks the request, clears `manifest_held` and deletes the items' `audio_objects`
and `extracted_subtitles` rows; then their objects under each item's audio prefix are deleted
(only an `uploading` batch has any); then the item rows go. An interruption after the first
step leaves the items in the batch without upload rows, and sending the request again finishes
the work (deleting a missing object is not an error). The last item cannot be excluded; when
only `extract` jobs remain, the batch is extract-only (no upload, no manifest; its summary says
so) and the upload takes it to `transcribed` without a manifest.
"""

from sqlalchemy import delete

from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.classes import ExcludeItemsRequest
from transcribe_ui.batches.constants import DELETE_OBJECTS_STAGE, REVIEW_PHASES, UPLOADING_PHASES
from transcribe_ui.batches.errors import ItemNotFoundError, LastItemError
from transcribe_ui.batches.objects import delete_item_objects, forget_uploads, object_store
from transcribe_ui.batches.phases import load_batch, require_phase, touch_batch
from transcribe_ui.batches.records import load_detail, stored_items
from transcribe_ui.batches.responses import ExcludeItemsResponse
from transcribe_ui.state.models import ItemRow


def _forget(request: ExcludeItemsRequest, context: RequestContext) -> str:
    """Check the request, clear `manifest_held` and forget the items' upload rows; return the batch's phase.

    Raises:
        WrongPhaseError: If the batch is not in `reviewing` or `uploading`.
        ItemNotFoundError: If an item id is not an item of the batch.
        LastItemError: If no item would be left.
    """
    batch_id = request.batch_id
    with context.ports.state.session() as session:
        row = load_batch(session, batch_id)
        require_phase(row, REVIEW_PHASES)
        phase = row.phase
        present = {member.item_id for member in stored_items(session, batch_id)}
        unknown = set(request.item_ids) - present
        if unknown:
            raise ItemNotFoundError(batch_id, unknown)
        if present <= set(request.item_ids):
            raise LastItemError(batch_id)
        forget_uploads(session, batch_id, request.item_ids)
        now = context.ports.clock.now()
        touch_batch(session, batch_id, (phase,), now=now, also_set={"manifest_held": False})
        return phase


def _delete_objects(request: ExcludeItemsRequest, context: RequestContext) -> int:
    store = object_store(context.ports)
    deleted = 0
    total = len(request.item_ids)
    for done, item_id in enumerate(request.item_ids, start=1):
        deleted += delete_item_objects(store, request.batch_id, item_id, context.check_cancelled)
        event = ProgressEvent(action=request.action, stage=DELETE_OBJECTS_STAGE, completed=done, total=total)
        context.progress.emit(event)
    return deleted


def exclude_items(request: ExcludeItemsRequest, context: RequestContext) -> ExcludeItemsResponse:
    """Remove the items from the batch, deleting their uploaded audio; refuses to remove the last item."""
    state = context.ports.state
    batch_id = request.batch_id
    with state.locks.batch(batch_id):
        phase = _forget(request, context)
        deleted = _delete_objects(request, context) if phase in UPLOADING_PHASES else 0
        with state.session() as session:
            now = context.ports.clock.now()
            touch_batch(session, batch_id, (phase,), now=now)
            excluded = ItemRow.item_id.in_(request.item_ids)
            of_batch = ItemRow.batch_id == batch_id
            session.execute(delete(ItemRow).where(of_batch, excluded))
            detail = load_detail(session, load_batch(session, batch_id))
    return ExcludeItemsResponse(batch=detail, deleted_objects=deleted)
