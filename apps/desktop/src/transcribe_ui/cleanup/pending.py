"""This computer's interrupted cleanups of other computers' jobs, finished by the refresh (architecture §3.3).

The cleanup of a batch this database does not know records it in `pending_cleanups` before it
writes the marker `<id>/_deleting.json`, and drops the record after it deleted the marker. After
an interruption (a crash, a kill, an R2 failure) the batch is gone from every listing once its
manifest is gone, so `refresh_status` with `recheck` (start-up, Refresh now) handles every record,
under the runs lock and the batch's lock (only tried), with no transaction open across R2:

- manifest gone, marker there: the deletion passed its point of no return, and the user
  confirmed it; the rest is deleted without another question (`stages.finish_unknown`);
- manifest still there: the deletion never got past the marker; the marker is deleted and the
  batch stays as it was;
- neither: nothing is left to do.

The record is then dropped. Each record names its bucket (endpoint and bucket name): only the
records of the configured bucket are settled, the others wait until it is configured again. A
record with neither object is dropped only once the bucket is proven to exist. A batch a live run
lease lists, or whose lock is busy, waits for a later refresh, and so does one R2 fails for
(logged; the other records go on). Two computers cleaning the same job at once may delete each
other's marker; each one's own steps then still finish or undo it. Repeating `cleanup_cloud` with the id still works as before.
"""

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from transcribe_contracts.layout import deleting_key, manifest_key
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.cleanup.constants import FINISH_PENDING_ACTION
from transcribe_ui.cleanup.stages import Purge, finish_unknown
from transcribe_ui.credentials.constants import R2_BUCKET, R2_ENDPOINT_URL
from transcribe_ui.runs.leases import batch_leases
from transcribe_ui.state.models import PendingCleanupRow
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import ObjectStore

logger = logging.getLogger(__name__)


def bucket_identity(context: RequestContext) -> str:
    """Return the bucket the stored R2 credentials name: endpoint and bucket name (what a record belongs to)."""
    lookup = context.ports.credentials.lookup((R2_ENDPOINT_URL, R2_BUCKET))
    endpoint = lookup.text(R2_ENDPOINT_URL).rstrip("/")
    return f"{endpoint}/{lookup.text(R2_BUCKET)}"


def record_pending(context: RequestContext, batch_id: str) -> None:
    """Record that the cleanup of a batch this database does not know starts (before its marker), in this bucket."""
    now = context.ports.clock.now()
    row = PendingCleanupRow(bucket=bucket_identity(context), batch_id=batch_id, started_at=now)
    with context.ports.state.session() as session:
        session.merge(row)


def drop_pending(context: RequestContext, batch_id: str) -> None:
    """Drop the record of a cleanup that ended (after its marker was deleted), in this bucket."""
    with context.ports.state.session() as session:
        _drop(session, bucket_identity(context), batch_id)


def finish_pending_cleanups(context: RequestContext, store: ObjectStore) -> tuple[str, ...]:
    """Finish or undo every recorded cleanup of this bucket; return the batches whose deletion was finished now.

    The caller holds the runs lock. Records of another bucket wait until it is configured again;
    one that R2 fails for is logged and kept for the next refresh, and the others go on.

    Raises:
        CancelledError: Cancelled between two deletes (the records left stay).
    """
    state = context.ports.state
    bucket = bucket_identity(context)
    of_bucket = PendingCleanupRow.bucket == bucket
    query = select(PendingCleanupRow.batch_id).where(of_bucket).order_by(PendingCleanupRow.batch_id)
    with state.session() as session:
        pending = list(session.scalars(query))
    finished: list[str] = []
    for batch_id in pending:
        with state.locks.try_batch(batch_id) as taken:
            if taken and _settled(context, store, batch_id):
                finished.append(batch_id)
    return tuple(finished)


def _settled(context: RequestContext, store: ObjectStore, batch_id: str) -> bool:
    """Settle one record; R2 failing for it is logged and the record kept (the next refresh resumes)."""
    try:
        return _settle(context, store, batch_id)
    except StorageError as error:
        logger.warning("The interrupted cleanup of %s could not be settled (%s); it is kept", batch_id, error.code)
        return False


def _settle(context: RequestContext, store: ObjectStore, batch_id: str) -> bool:
    """Finish or undo one recorded cleanup; return whether its deletion was finished now.

    Raises:
        StorageError: R2 failed, or the bucket does not exist (before a record without either
            object is dropped).
    """
    if batch_leases(store, batch_id, context.ports.clock.now()):
        return False
    manifest = store.head(manifest_key(batch_id)) is not None
    marker = store.head(deleting_key(batch_id)) is not None
    if manifest and marker:
        store.delete(deleting_key(batch_id))
    elif marker:
        finish_unknown(Purge(store, context, FINISH_PENDING_ACTION), batch_id)
    elif not manifest:
        # A HEAD in a missing bucket also finds nothing: prove the bucket before dropping the record.
        store.check_access()
    drop_pending(context, batch_id)
    return marker and not manifest


def _drop(session: Session, bucket: str, batch_id: str) -> None:
    of_record = (PendingCleanupRow.bucket == bucket, PendingCleanupRow.batch_id == batch_id)
    session.execute(delete(PendingCleanupRow).where(*of_record))
