"""Starting a batch's upload: the review must be complete and its id free in R2 (architecture §3.3 "Upload").

Batch ids are unique only in this database: a batch of another computer created in the same
second, or a batch of an earlier (deleted) database, has the same id and so the same R2 prefix.
Before the first object is uploaded, `batch_metadata/<id>.json`, every object under `<id>/` and
every open multipart upload there (another computer's upload in progress) must therefore be
absent; otherwise the batch keeps `reviewing` and the user creates it again
(a new id). Once the batch is `uploading`, objects under its prefix are taken as its own.
"""

from transcribe_contracts.layout import batch_prefix, manifest_key
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.phases import load_batch, require_phase
from transcribe_ui.batches.records import stored_items
from transcribe_ui.state.database import compare_and_set_phase
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.constants import ITEM_REVIEWED, REVIEWED_BATCH, UPLOAD_PHASES, UPLOADING_BATCH
from transcribe_ui.upload.errors import BatchIdTakenError, ReviewIncompleteError


def checked_phase(context: RequestContext, batch_id: str) -> str:
    """Return the batch's phase; a batch in `reviewing` must have every item resolved.

    Raises:
        BatchNotFoundError: If there is no such batch.
        WrongPhaseError: If the batch is neither `reviewing` nor `uploading`.
        ReviewIncompleteError: If the batch is `reviewing` and an item is not resolved.
    """
    with context.ports.state.session() as session:
        row = load_batch(session, batch_id)
        require_phase(row, UPLOAD_PHASES)
        if row.phase == REVIEWED_BATCH:
            members = stored_items(session, batch_id)
            unresolved = [member.item_id for member in members if member.phase != ITEM_REVIEWED]
            if unresolved:
                raise ReviewIncompleteError(batch_id, unresolved)
        return row.phase


def start_upload(context: RequestContext, store: ObjectStore, batch_id: str) -> None:
    """Move a reviewed batch to `uploading`, once R2 holds nothing under its id.

    The caller holds the batch's lock (so the review cannot change meanwhile).

    Raises:
        BatchIdTakenError: R2 holds the batch's manifest, objects or open multipart uploads under its
            prefix; nothing was changed.
        StorageError: R2 could not be read; nothing was changed.
    """
    prefix = batch_prefix(batch_id)
    manifest = store.head(manifest_key(batch_id))
    if manifest is not None or store.list_objects(prefix) or store.count_uploads(prefix):
        raise BatchIdTakenError(batch_id)
    with context.ports.state.session() as session:
        now = context.ports.clock.now()
        compare_and_set_phase(session, batch_id, REVIEWED_BATCH, UPLOADING_BATCH, now=now)
