"""The run leases the last `refresh_status` read, kept in the database (architecture §3.9 "Run scope").

`refresh_status` reads the live leases from R2 and then replaces the table `known_leases` in one
transaction, so a batch summary built by any request, the GUI's or a fresh CLI process's, can tell
which `ready` batches wait for the current run (`waits_for_current_run`) without reading R2. The
rows are as of the last refresh.
"""

from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from transcribe_ui.state.classes import LiveLease
from transcribe_ui.state.models import LeaseRow


def replace_known_leases(session: Session, leases: Iterable[LiveLease]) -> None:
    """Replace the stored leases by those of a newer reading."""
    session.execute(delete(LeaseRow))
    session.add_all(_row(lease) for lease in leases)


def _row(lease: LiveLease) -> LeaseRow:
    listed = None if lease.batch_ids is None else sorted(lease.batch_ids)
    return LeaseRow(run_id=lease.run_id, batch_ids=listed, until=lease.until)


def known_leases(session: Session) -> list[LiveLease]:
    """Return the leases the last refresh read (none before the first one)."""
    rows = session.scalars(select(LeaseRow).order_by(LeaseRow.run_id))
    return [_lease(row) for row in rows]


def _lease(row: LeaseRow) -> LiveLease:
    listed = None if row.batch_ids is None else frozenset(row.batch_ids)
    return LiveLease(run_id=row.run_id, batch_ids=listed, until=row.until)
