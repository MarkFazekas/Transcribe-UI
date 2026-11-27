"""The phase model of batches and items (architecture §3.3, plan m5 §1 "State and concurrency").

Batch phases change only by compare-and-set (`UPDATE ... WHERE phase = :expected`), so a request
that lost a race changes nothing. Requests that change a batch without moving its phase (edit,
resolve, exclude) also write it conditionally: `touch_batch` matches the phases the request
needs, so a batch that moved on in the meantime gives `phase_conflict` and rolls back. Item
phases follow the batch until `reviewing`; from then on an item can go back to review on its own
while the batch stays `uploading` (plan m5 §1 "Per-item re-review").
"""

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from transcribe_ui.batches.errors import WrongPhaseError
from transcribe_ui.batches.payloads import ItemPhase
from transcribe_ui.batches.records import stored_items
from transcribe_ui.state.errors import BatchNotFoundError, PhaseConflictError
from transcribe_ui.state.models import BatchRow, ItemRow


def load_batch(session: Session, batch_id: str) -> BatchRow:
    """Return the batch row.

    Raises:
        BatchNotFoundError: If there is no such batch.
    """
    row = session.get(BatchRow, batch_id)
    if row is None:
        raise BatchNotFoundError(batch_id)
    return row


def require_phase(row: BatchRow, allowed: Collection[str]) -> None:
    """Check that the batch is in one of the allowed phases.

    Raises:
        WrongPhaseError: If it is not.
    """
    require_phase_name(row.batch_id, row.phase, allowed)


def require_phase_name(batch_id: str, phase: str, allowed: Collection[str]) -> None:
    """Check that a batch's phase is one of the allowed phases.

    Raises:
        WrongPhaseError: If it is not.
    """
    if phase not in allowed:
        raise WrongPhaseError(batch_id, phase, allowed)


def touch_batch(
    session: Session,
    batch_id: str,
    allowed: Collection[str],
    *,
    now: datetime,
    also_set: Mapping[str, object] | None = None,
) -> None:
    """Record a change of the batch (its `updated_at`, plus `also_set`) while it is in an allowed phase.

    One conditional `UPDATE`, like a phase change: a batch that left the allowed phases in the
    meantime is not touched, and the error rolls the request's transaction back.

    Raises:
        PhaseConflictError: If the batch is no longer in an allowed phase.
        BatchNotFoundError: If the batch does not exist.
    """
    changes = {"updated_at": now, **(also_set or {})}
    found = BatchRow.batch_id == batch_id
    in_phase = BatchRow.phase.in_(allowed)
    statement = update(BatchRow).where(found, in_phase).values(changes)
    outcome = cast("CursorResult[tuple[()]]", session.execute(statement.execution_options(synchronize_session=False)))
    if outcome.rowcount == 1:
        # A row this session already loaded is stale now: reload it.
        session.get(BatchRow, batch_id, populate_existing=True)
        return
    actual = session.scalar(select(BatchRow.phase).where(found))
    if actual is None:
        raise BatchNotFoundError(batch_id)
    raise PhaseConflictError(batch_id, "|".join(sorted(allowed)), actual)


def set_item_phases(session: Session, batch_id: str, expected: ItemPhase, new: ItemPhase) -> None:
    """Move every item of the batch in phase `expected` to `new` (the batch moved with them)."""
    matches = (ItemRow.batch_id == batch_id, ItemRow.phase == expected)
    statement = update(ItemRow).where(*matches).values(phase=new)
    session.execute(statement.execution_options(synchronize_session=False))
    # Item rows this session already loaded are stale now: reload them on next access.
    session.expire_all()


def set_review_phase(session: Session, batch_id: str, item_id: str) -> None:
    """Set an item's review phase from its jobs: `reviewed` when every choice fits, else `reviewing`.

    An item that is not in review (already uploaded) keeps its phase.
    """
    members = {stored.item_id: stored for stored in stored_items(session, batch_id)}
    member = members[item_id]
    if not member.in_review():
        return
    row = session.get(ItemRow, item_id)
    assert row is not None, "the item was just read"
    row.phase = "reviewed" if member.resolved() else "reviewing"
