"""Per-item re-review: the upload sends an item whose source changed back to review (plan m5 §1)."""

from sqlalchemy import update
from sqlalchemy.orm import Session

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.constants import JOB_PENDING, UPLOADING_PHASES
from transcribe_ui.batches.errors import ItemNotFoundError
from transcribe_ui.batches.objects import delete_item_objects, forget_uploads, object_store
from transcribe_ui.batches.phases import load_batch, require_phase, touch_batch
from transcribe_ui.state.models import ItemRow, JobRow
from transcribe_ui.upload.steps import finish_after_change
from transcribe_ui.upload.withdraw import recorded_withdrawal, withdrawal_guard


def return_item_to_review(context: RequestContext, batch_id: str, item_id: str) -> bool:
    """Send an item of an `uploading` batch back to review (its source changed, plan m5 §1).

    The caller holds the batch lock and not the runs lock. A leftover manifest of ours (an
    interrupted manifest step) is withdrawn under the runs lock while no live run lease lists the
    batch (`upload/withdraw.py`); while one does the request is refused and nothing changes. One
    transaction sets the item to `reviewing`, deletes its `audio_objects` and
    `extracted_subtitles` rows, sets its jobs back to pending and records the pending withdrawal; then the withdrawal is carried out and the item's objects are deleted. Objects
    an interruption leaves behind stay under the item's audio prefix, unrecorded, where the
    orphan sweep of the next `run_upload` (or the batch's cleanup) removes them. Returns whether a withdrawal
    stays pending (R2 failed after the change was saved; logged, not an error).

    Raises:
        WrongPhaseError: If the batch is not `uploading`.
        ItemNotFoundError: If the item is not an item of the batch.
        RunActiveError: A leftover manifest is ours and a live lease lists the batch (nothing changed).
        StorageError: R2 could not be read (nothing changed).
        CancelledError: If cancelled between two object deletes.
    """
    with context.ports.state.session() as session:
        _require_item(session, batch_id, item_id)
    store = object_store(context.ports)
    with withdrawal_guard(context, store, batch_id) as leftover:
        _send_back(context, batch_id, item_id, leftover)
        pending = leftover is not None and finish_after_change(context, store, batch_id)
    delete_item_objects(store, batch_id, item_id, context.check_cancelled)
    return pending


def _send_back(context: RequestContext, batch_id: str, item_id: str, leftover: str | None) -> None:
    with context.ports.state.session() as session:
        row = _require_item(session, batch_id, item_id)
        row.phase = "reviewing"
        forget_uploads(session, batch_id, (item_id,))
        of_item = (JobRow.batch_id == batch_id, JobRow.item_id == item_id)
        pending = update(JobRow).where(*of_item).values(status=JOB_PENDING)
        session.execute(pending.execution_options(synchronize_session=False))
        changes: dict[str, object] = {}
        if leftover is not None:
            changes["pending_withdrawal"] = recorded_withdrawal(session, batch_id, leftover, (item_id,))
        now = context.ports.clock.now()
        touch_batch(session, batch_id, UPLOADING_PHASES, now=now, also_set=changes)


def _require_item(session: Session, batch_id: str, item_id: str) -> ItemRow:
    """Return the item's row; the batch must be `uploading` and the item one of its items."""
    require_phase(load_batch(session, batch_id), UPLOADING_PHASES)
    row = session.get(ItemRow, item_id)
    if row is None or row.batch_id != batch_id:
        raise ItemNotFoundError(batch_id, (item_id,))
    return row
