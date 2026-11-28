"""Which batches the live runs process, as this backend knows them (the label `waits_for_current_run`).

A run processes only the batches its lease lists (A21, architecture §3.9 "Run scope"). A `ready`
batch waits for the current run while a live run is known and none lists it. What is known: the
local runs that are not final (their covered batch ids), and the live leases the last
`refresh_status` read (`state/leases.py`, stored in the database, so every request and process
sees the same; leases of runs that are final here since are left out). No list request reads R2
for it.
"""

from dataclasses import dataclass

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_ui.batches.constants import COVERED_BATCHES_KEY
from transcribe_ui.runs.constants import FINAL_RUN_STATUSES
from transcribe_ui.state.classes import LiveLease
from transcribe_ui.state.leases import known_leases
from transcribe_ui.state.models import RunRow

# The batch ids one run lists.
type Listing = list[str]


@dataclass(frozen=True)
class RunScope:
    """Which batches the live runs process, as far as this backend knows (`waits_for_current_run`).

    From the local open runs' covered batch ids, and the live leases the last `refresh_status`
    read (`state/leases.py`) whose run is not a final local run; no request reads R2 for it.
    """

    # Whether a live run is known at all.
    active: bool = False
    # The batches the live runs list; None when a lease that cannot be read lists every batch.
    listed: frozenset[str] | None = frozenset()

    def waits(self, batch_id: str) -> bool:
        """Return whether a `ready` batch waits for the current run: a run is live and none lists it."""
        return self.active and self.listed is not None and batch_id not in self.listed


def run_scope(session: Session) -> RunScope:
    """Return the run scope known in this session (the local runs, and the leases of the last refresh)."""
    final, listings = _local_runs(session)
    for lease in _leases(session, final):
        if lease.batch_ids is None:
            return RunScope(active=True, listed=None)
        listings.append(list(lease.batch_ids))
    listed = frozenset(batch_id for listing in listings for batch_id in listing)
    return RunScope(active=bool(listings), listed=listed)


def _local_runs(session: Session) -> tuple[frozenset[str], list[Listing]]:
    """Return the final local runs' ids, and the covered batch ids of each local run that is not final."""
    final: set[str] = set()
    listings: list[Listing] = []
    for row in session.execute(select(RunRow.run_id, RunRow.status, RunRow.payload)):
        if row.status in FINAL_RUN_STATUSES:
            final.add(row.run_id)
        else:
            listings.append(_covered(row.payload))
    return frozenset(final), listings


def _leases(session: Session, final: frozenset[str]) -> list[LiveLease]:
    """Return the leases the last refresh read, less those of runs that are final here since."""
    return [lease for lease in known_leases(session) if lease.run_id not in final]


def _covered(payload: JsonValue) -> list[str]:
    """Return the covered batch ids a run payload lists (read without validating the whole payload)."""
    found = payload.get(COVERED_BATCHES_KEY) if isinstance(payload, dict) else None
    return [str(batch_id) for batch_id in found] if isinstance(found, list) else []
