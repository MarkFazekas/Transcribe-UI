"""Per-item re-review: the upload sends an item whose source changed back to review (plan m5 §1)."""

from sqlalchemy import update

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.constants import JOB_PENDING, UPLOADING_PHASES
from transcribe_ui.batches.errors import ItemNotFoundError
from transcribe_ui.batches.objects import delete_item_objects, forget_uploads, object_store, withdraw_manifest
from transcribe_ui.batches.phases import load_batch, require_phase, touch_batch
from transcribe_ui.state.models import ItemRow, JobRow


def return_item_to_review(context: RequestContext, batch_id: str, item_id: str) -> None:
    """Send an item of an `uploading` batch back to review (its source changed, plan m5 §1).

    The caller holds the batch lock. One transaction first sets the item to `reviewing`, deletes
    its `audio_objects` and `extracted_subtitles` rows, sets its jobs back to pending and clears
    the batch's `manifest_held` (the held manifest would list the old audio); only then are a
    manifest left by an interrupted manifest step and the item's objects deleted. Objects an interruption leaves behind stay under the item's audio prefix,
    where a later exclusion or upload of the item (or the batch's cleanup) removes them.

    Raises:
        WrongPhaseError: If the batch is not `uploading`.
        ItemNotFoundError: If the item is not an item of the batch.
        CancelledError: If cancelled between two object deletes.
    """
    state = context.ports.state
    with state.session() as session:
        require_phase(load_batch(session, batch_id), UPLOADING_PHASES)
        row = session.get(ItemRow, item_id)
        if row is None or row.batch_id != batch_id:
            raise ItemNotFoundError(batch_id, (item_id,))
        row.phase = "reviewing"
        forget_uploads(session, batch_id, (item_id,))
        of_item = (JobRow.batch_id == batch_id, JobRow.item_id == item_id)
        pending = update(JobRow).where(*of_item).values(status=JOB_PENDING)
        session.execute(pending.execution_options(synchronize_session=False))
        now = context.ports.clock.now()
        touch_batch(session, batch_id, UPLOADING_PHASES, now=now, also_set={"manifest_held": False})
    store = object_store(context.ports)
    withdraw_manifest(store, batch_id)
    delete_item_objects(store, batch_id, item_id, context.check_cancelled)
