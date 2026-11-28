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

The record is then dropped. A batch a live run lease lists, or whose lock is busy, waits for a
later refresh. Repeating `cleanup_cloud` with the id still works as before.
"""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from transcribe_contracts.layout import deleting_key, manifest_key
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.cleanup.constants import FINISH_PENDING_ACTION
from transcribe_ui.cleanup.stages import Purge, finish_unknown
from transcribe_ui.runs.leases import batch_leases
from transcribe_ui.state.models import PendingCleanupRow
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.r2 import ObjectStore


def record_pending(state: LocalState, batch_id: str, now: datetime) -> None:
    """Record that the cleanup of a batch this database does not know starts (before its marker)."""
    with state.session() as session:
        session.merge(PendingCleanupRow(batch_id=batch_id, started_at=now))


def drop_pending(state: LocalState, batch_id: str) -> None:
    """Drop the record of a cleanup that ended (after its marker was deleted)."""
    with state.session() as session:
        _drop(session, batch_id)


def finish_pending_cleanups(context: RequestContext, store: ObjectStore) -> tuple[str, ...]:
    """Finish or undo every recorded cleanup; return the batches whose deletion was finished now.

    The caller holds the runs lock.

    Raises:
        CancelledError: Cancelled between two deletes (the records left stay).
        StorageError: R2 failed (the records left stay; the next refresh resumes).
    """
    state = context.ports.state
    query = select(PendingCleanupRow.batch_id).order_by(PendingCleanupRow.batch_id)
    with state.session() as session:
        pending = list(session.scalars(query))
    finished: list[str] = []
    for batch_id in pending:
        with state.locks.try_batch(batch_id) as taken:
            if taken and _settle(context, store, batch_id):
                finished.append(batch_id)
    return tuple(finished)


def _settle(context: RequestContext, store: ObjectStore, batch_id: str) -> bool:
    """Finish or undo one recorded cleanup; return whether its deletion was finished now."""
    if batch_leases(store, batch_id, context.ports.clock.now()):
        return False
    manifest = store.head(manifest_key(batch_id)) is not None
    marker = store.head(deleting_key(batch_id)) is not None
    if manifest and marker:
        store.delete(deleting_key(batch_id))
    elif marker:
        finish_unknown(Purge(store, context, FINISH_PENDING_ACTION), batch_id)
    drop_pending(context.ports.state, batch_id)
    return marker and not manifest


def _drop(session: Session, batch_id: str) -> None:
    session.execute(delete(PendingCleanupRow).where(PendingCleanupRow.batch_id == batch_id))
