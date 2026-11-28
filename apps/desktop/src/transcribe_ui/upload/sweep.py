"""The orphan sweep at the start of `run_upload` (architecture §3.3 "Upload").

Leaving the upload forgets an item's rows before its objects are deleted, so an interrupted
exclusion or re-review leaves audio objects that no item records; an interrupted upload leaves
objects whose row was never written (or another extension of the same track). The sweep lists
the batch's audio folder and deletes every object that is not a verified `audio_objects` row of
the batch. Such objects are never in a manifest this database builds now, but a leftover manifest
whose withdrawal is pending (`upload/withdraw.py`) may list them, and a running worker may read
it: a pending withdrawal is therefore finished first (whether or not there are orphans, so no
leftover outlives the upload's start), under the runs lock while no live lease lists the batch.
While one does (or a launch holds the runs lock past the bounded wait) the withdrawal and the sweep are put off to the next `run_upload`, or to the manifest step,
which finishes the withdrawal before it writes; the objects are harmless meanwhile.
"""

import logging

from sqlalchemy import select

from transcribe_contracts.constants import AUDIO_FOLDER
from transcribe_contracts.layout import batch_prefix, deleting_key
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.phases import load_batch
from transcribe_ui.runs.errors import RunActiveError
from transcribe_ui.runs.leases import refuse_if_leased
from transcribe_ui.state.errors import RunsBusyError
from transcribe_ui.state.models import AudioObjectRow
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.constants import AUDIO_VERIFIED
from transcribe_ui.upload.steps import finish_withdrawal

logger = logging.getLogger(__name__)


def clear_leftovers(context: RequestContext, store: ObjectStore, batch_id: str) -> int | None:
    """Abort the batch's open multipart uploads, then sweep its orphans (`sweep_orphans`).

    Raises:
        CancelledError: Cancelled between two deletes or while waiting for the runs lock.
        StorageError: R2 failed (the next `run_upload` resumes).
    """
    store.abort_uploads(batch_prefix(batch_id))
    # Another computer's interrupted cleanup marker must not let it purge the prefix this upload reclaims.
    store.delete(deleting_key(batch_id))
    return sweep_orphans(context, store, batch_id)


def sweep_orphans(context: RequestContext, store: ObjectStore, batch_id: str) -> int | None:
    """Finish a pending withdrawal, then delete the batch's audio objects that no item records.

    Returns how many objects were deleted, or None when a pending withdrawal could not be
    finished (a live lease lists the batch, or the runs lock stays busy).

    The caller holds the batch's lock and not the runs lock.

    Raises:
        CancelledError: Cancelled between two deletes or while waiting for the runs lock.
        StorageError: R2 failed, the leases included (what was deleted stays deleted; the next sweep resumes).
    """
    with context.ports.state.session() as session:
        pending = load_batch(session, batch_id).pending_withdrawal is not None
    if pending and not _withdrawn(context, store, batch_id):
        return None
    verified = (AudioObjectRow.batch_id == batch_id, AudioObjectRow.status == AUDIO_VERIFIED)
    query = select(AudioObjectRow.object_key).where(*verified)
    with context.ports.state.session() as session:
        recorded = frozenset(session.scalars(query))
    listed = store.list_objects(f"{batch_prefix(batch_id)}{AUDIO_FOLDER}/")
    orphans = [found.key for found in listed if found.key not in recorded]
    for key in orphans:
        context.check_cancelled()
        store.delete(key)
    return len(orphans)


def _withdrawn(context: RequestContext, store: ObjectStore, batch_id: str) -> bool:
    """Finish the batch's pending withdrawal under the runs lock unless a live lease lists it; return whether it is done."""
    state = context.ports.state
    try:
        with state.locks.runs(context.cancel.is_cancelled):
            refuse_if_leased(store, batch_id, context.ports.clock.now())
            finish_withdrawal(context, store, batch_id)
    except (RunsBusyError, RunActiveError) as error:
        logger.info("Sweep of %s put off: %s", batch_id, error.code)
        return False
    return True
