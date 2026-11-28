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

The record is then dropped. Each record names its bucket (the store's identity, a hash of the
endpoint and bucket name the store was opened with): only the records of the configured bucket
are settled, the others wait until it is configured again. A
record with neither object is dropped only once the bucket is proven to exist (once per refresh).
A batch a live run lease lists, or whose lock is busy, waits for a later refresh, and so does one
R2 fails for: an error about the object is logged and the others go on, one about R2 itself stops
the settling until the next refresh. Two computers cleaning the same job at once may delete each
other's marker; each one's own steps then still finish or undo it. Repeating `cleanup_cloud` with the id still works as before.
"""

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from transcribe_contracts.layout import deleting_key, manifest_key
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.cleanup.constants import FINISH_PENDING_ACTION, SETTLE_STOPPING_CODES
from transcribe_ui.cleanup.stages import Purge, finish_unknown
from transcribe_ui.runs.leases import batch_leases
from transcribe_ui.state.models import PendingCleanupRow
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import ObjectStore

logger = logging.getLogger(__name__)


def record_pending(context: RequestContext, store: ObjectStore, batch_id: str) -> None:
    """Record that the cleanup of a batch this database does not know starts (before its marker), in the store's bucket."""
    now = context.ports.clock.now()
    row = PendingCleanupRow(bucket=store.identity, batch_id=batch_id, started_at=now)
    with context.ports.state.session() as session:
        session.merge(row)


def drop_pending(context: RequestContext, store: ObjectStore, batch_id: str) -> None:
    """Drop the record of a cleanup that ended (after its marker was deleted), in the store's bucket."""
    with context.ports.state.session() as session:
        _drop(session, store.identity, batch_id)


def finish_pending_cleanups(context: RequestContext, store: ObjectStore) -> tuple[str, ...]:
    """Finish or undo every recorded cleanup of the store's bucket; return the batches whose deletion was finished now.

    The caller holds the runs lock. Records of another bucket wait until it is configured again.
    An R2 error about one object is logged and its record kept, and the others go on; one about
    R2 itself (unreachable, the credentials, the bucket) stops the loop, logged once: the records
    left wait for the next refresh, and the refresh goes on.

    Raises:
        CancelledError: Cancelled between two deletes (the records left stay).
    """
    state = context.ports.state
    of_bucket = PendingCleanupRow.bucket == store.identity
    query = select(PendingCleanupRow.batch_id).where(of_bucket).order_by(PendingCleanupRow.batch_id)
    with state.session() as session:
        pending = list(session.scalars(query))
    settling = _Settling(context, store)
    for batch_id in pending:
        with state.locks.try_batch(batch_id) as taken:
            if taken and not settling.settle(batch_id):
                break
    return tuple(settling.finished)


class _Settling:
    """The settling of the records of one refresh: the bucket is proven once, the finished ones are kept."""

    def __init__(self, context: RequestContext, store: ObjectStore) -> None:
        self._context = context
        self._store = store
        self._proven = False
        self.finished: list[str] = []

    def settle(self, batch_id: str) -> bool:
        """Settle one record; return False when R2 itself failed (the loop stops)."""
        try:
            self._settle(batch_id)
        except StorageError as error:
            if error.code in SETTLE_STOPPING_CODES:
                logger.warning("Interrupted cleanups are not settled now (%s); they are kept", error.code)
                return False
            logger.warning("The interrupted cleanup of %s could not be settled (%s); it is kept", batch_id, error.code)
        return True

    def _settle(self, batch_id: str) -> None:
        """Finish or undo one recorded cleanup.

        Raises:
            StorageError: R2 failed, or the bucket does not exist (before a record without
                either object is dropped).
        """
        context = self._context
        store = self._store
        if batch_leases(store, batch_id, context.ports.clock.now()):
            return
        manifest = store.head(manifest_key(batch_id)) is not None
        marker = store.head(deleting_key(batch_id)) is not None
        if manifest and marker:
            store.delete(deleting_key(batch_id))
        elif marker:
            finish_unknown(Purge(store, context, FINISH_PENDING_ACTION), batch_id)
            self.finished.append(batch_id)
        elif not manifest:
            self._prove()
        drop_pending(context, store, batch_id)

    def _prove(self) -> None:
        """Prove once per refresh that the bucket exists (a HEAD in a missing bucket also finds nothing)."""
        if not self._proven:
            self._store.check_access()
            self._proven = True


def _drop(session: Session, bucket: str, batch_id: str) -> None:
    of_record = (PendingCleanupRow.bucket == bucket, PendingCleanupRow.batch_id == batch_id)
    session.execute(delete(PendingCleanupRow).where(*of_record))
