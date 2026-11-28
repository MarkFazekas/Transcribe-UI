"""The `ready` batches `refresh_status` follows: whose results are still awaited (architecture §3.9 "Results poll").

A `ready` batch is followed until its `_done.json` moves it on, or until another computer removed
it from the cloud (`removed_from_cloud`: neither its manifest nor `_done.json` is left); such a
batch keeps its history and is no longer checked.
"""

from sqlalchemy import ColumnElement, select

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.phases import touch_batch
from transcribe_ui.runs.constants import READY_PHASE
from transcribe_ui.state.models import BatchRow
from transcribe_ui.state.store import LocalState


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
