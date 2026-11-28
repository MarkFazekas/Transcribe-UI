"""The `ready` batches `refresh_status` follows: whose results are still awaited (architecture §3.9 "Results poll").

A `ready` batch is followed until its `_done.json` moves it on, or until another computer removed
it from the cloud (`removed_from_cloud`: neither its manifest nor `_done.json` is left, in a bucket
proven to exist); such a batch keeps its history and is no longer checked by the poll. The mark
can be undone: a refresh with `recheck` (start-up, Refresh now) looks for the marked
batches again, and one whose manifest or `_done.json` is back (the bucket was switched back, or
its uploader withdrew and published it again) is followed again.
"""

from collections.abc import Callable

from sqlalchemy import ColumnElement, select

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.phases import touch_batch
from transcribe_ui.cleanup.pending import finish_pending_cleanups
from transcribe_ui.runs.constants import READY_PHASE
from transcribe_ui.state.models import BatchRow
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.r2 import ObjectStore


def followed_batches() -> tuple[ColumnElement[bool], ...]:
    """Return the conditions of the batches whose results are awaited: `ready`, still in the cloud."""
    return (BatchRow.phase == READY_PHASE, BatchRow.removed_from_cloud.is_(False))


def awaiting_results(state: LocalState) -> bool:
    """Return whether some followed batch still waits for its results (the GUI keeps polling)."""
    query = select(BatchRow.batch_id).where(*followed_batches())
    with state.session() as session:
        return session.scalar(query.limit(1)) is not None


def mark_removed(context: RequestContext, batch_id: str) -> None:
    """Mark a `ready` batch another computer removed from the cloud (the caller holds its lock)."""
    with context.ports.state.session() as session:
        now = context.ports.clock.now()
        touch_batch(session, batch_id, (READY_PHASE,), now=now, also_set={"removed_from_cloud": True})


def removed_batches(state: LocalState) -> list[str]:
    """Return the `ready` batches marked removed from the cloud."""
    marked = (BatchRow.phase == READY_PHASE, BatchRow.removed_from_cloud.is_(True))
    query = select(BatchRow.batch_id).where(*marked)
    with state.session() as session:
        return list(session.scalars(query.order_by(BatchRow.batch_id)))


def follow_again(context: RequestContext, batch_id: str) -> None:
    """Follow a batch marked removed again: it is back in the cloud (the caller holds its lock)."""
    with context.ports.state.session() as session:
        now = context.ports.clock.now()
        touch_batch(session, batch_id, (READY_PHASE,), now=now, also_set={"removed_from_cloud": False})


# Whether R2 holds a batch's manifest or `_done.json`.
type Holds = Callable[[str], bool]


def recheck_cloud(context: RequestContext, store: ObjectStore, holds: Holds) -> tuple[str, ...]:
    """The fuller check (`recheck`): finish the interrupted cleanups, then follow again the marked batches that are back.

    `holds` tells whether R2 holds a batch's manifest or `_done.json` (proving the bucket first).
    A busy batch waits for the next recheck. Returns the batches whose cleanup was finished.

    Raises:
        StorageError: R2 could not be read, or the bucket does not exist.
    """
    finished = finish_pending_cleanups(context, store)
    for batch_id in removed_batches(context.ports.state):
        with context.ports.state.locks.try_batch(batch_id) as taken:
            if taken and holds(batch_id):
                follow_again(context, batch_id)
    return finished
