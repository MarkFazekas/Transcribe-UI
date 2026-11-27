"""The orphan sweep at the start of `run_upload` (architecture §3.3 "Upload").

Leaving the upload forgets an item's rows before its objects are deleted, so an interrupted
exclusion or re-review leaves audio objects that no item records; an interrupted upload leaves
objects whose row was never written (or another extension of the same track). The sweep lists
the batch's audio folder and deletes every object that is not a verified `audio_objects` row of
the batch. Such objects are never in a manifest this database builds now, but a leftover manifest
whose withdrawal is pending (`upload/withdraw.py`) may list them, and a running worker may read
it: the withdrawal is therefore finished first, under the runs lock with no run active. While a
run is active (or the cloud cannot be checked, or a launch holds the runs lock past the bounded
wait) the sweep is put off to the next `run_upload`; the objects are harmless meanwhile.
"""

import logging

from sqlalchemy import select

from transcribe_contracts.constants import AUDIO_FOLDER
from transcribe_contracts.layout import batch_prefix
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.phases import load_batch
from transcribe_ui.runs.activity import check_activity
from transcribe_ui.runs.cloud import CloudAccess
from transcribe_ui.runs.errors import ActivityUnknownError
from transcribe_ui.state.errors import RunsBusyError
from transcribe_ui.state.models import AudioObjectRow
from transcribe_ui.upload.constants import AUDIO_VERIFIED
from transcribe_ui.upload.steps import finish_withdrawal

logger = logging.getLogger(__name__)


def sweep_orphans(context: RequestContext, cloud: CloudAccess, batch_id: str) -> int | None:
    """Delete the batch's audio objects that no item records; return how many, or None when put off.

    The caller holds the batch's lock and not the runs lock.

    Raises:
        CancelledError: Cancelled between two deletes or while waiting for the runs lock.
        StorageError: R2 failed (what was deleted stays deleted; the next sweep resumes).
    """
    verified = (AudioObjectRow.batch_id == batch_id, AudioObjectRow.status == AUDIO_VERIFIED)
    query = select(AudioObjectRow.object_key).where(*verified)
    with context.ports.state.session() as session:
        pending = load_batch(session, batch_id).pending_withdrawal is not None
        recorded = frozenset(session.scalars(query))
    listed = cloud.store.list_objects(f"{batch_prefix(batch_id)}{AUDIO_FOLDER}/")
    orphans = [found.key for found in listed if found.key not in recorded]
    if not orphans:
        return 0
    if pending and not _withdrawn(context, cloud, batch_id):
        return None
    for key in orphans:
        context.check_cancelled()
        cloud.store.delete(key)
    return len(orphans)


def _withdrawn(context: RequestContext, cloud: CloudAccess, batch_id: str) -> bool:
    """Finish the batch's pending withdrawal under the runs lock if no run is active; return whether it is done."""
    state = context.ports.state
    try:
        with state.locks.runs(context.cancel.is_cancelled):
            return _withdraw_if_idle(context, cloud, batch_id)
    except (RunsBusyError, ActivityUnknownError) as error:
        logger.info("Sweep of %s put off: %s", batch_id, error.code)
        return False


def _withdraw_if_idle(context: RequestContext, cloud: CloudAccess, batch_id: str) -> bool:
    activity = check_activity(context.ports.state, cloud, context.ports.clock.now())
    if activity.active:
        logger.info("Sweep of %s put off: a run is active", batch_id)
        return False
    finish_withdrawal(context, cloud.store, batch_id)
    return True
