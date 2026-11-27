"""Per-item re-review: the upload sends an item whose source changed back to review (plan m5 §1)."""

from sqlalchemy import delete, select

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.constants import JOB_PENDING, UPLOADING_PHASES
from transcribe_ui.batches.objects import delete_item_objects, known_object_keys, object_store
from transcribe_ui.batches.phases import load_batch, require_phase, touch_batch
from transcribe_ui.state.models import AudioObjectRow, ExtractedSubtitleRow, ItemRow, JobRow


def return_item_to_review(context: RequestContext, batch_id: str, item_id: str) -> None:
    """Send an item of an `uploading` batch back to review (its source changed, plan m5 §1).

    Its uploaded audio objects and extracted subtitles are deleted, its jobs are pending again,
    and the batch's `manifest_held` flag is cleared (the held manifest would list the old audio).
    The caller holds the batch lock. The objects go first, so an interruption leaves the item as
    it was and calling again finishes the work.

    Raises:
        WrongPhaseError: If the batch is not `uploading`.
    """
    state = context.ports.state
    with state.session() as session:
        require_phase(load_batch(session, batch_id), UPLOADING_PHASES)
        known = known_object_keys(session, (item_id,))[item_id]
    delete_item_objects(object_store(context.ports), batch_id, item_id, known)
    with state.session() as session:
        job_ids = select(JobRow.job_id).where(JobRow.item_id == item_id)
        extracted = ExtractedSubtitleRow.job_id.in_(job_ids)
        session.execute(delete(ExtractedSubtitleRow).where(extracted))
        session.execute(delete(AudioObjectRow).where(AudioObjectRow.item_id == item_id))
        jobs = select(JobRow).where(JobRow.item_id == item_id)
        for job_row in session.scalars(jobs):
            job_row.status = JOB_PENDING
        row = session.get(ItemRow, item_id)
        assert row is not None, "the item is locked by its batch lock"
        row.phase = "reviewing"
        now = context.ports.clock.now()
        touch_batch(session, batch_id, UPLOADING_PHASES, now=now, also_set={"manifest_held": False})
