"""The run leases the last `refresh_status` read, kept in the backend's memory (architecture §3.9 "Run scope").

Nothing is stored: the start-up refresh reads them again. Every session carries the snapshot in
its `info` (`state/store.py`), so a batch summary can tell which `ready` batches wait for the
current run (`waits_for_current_run`) without reading R2.
"""

import threading

from sqlalchemy.orm import Session

from transcribe_ui.state.classes import LeaseSnapshot
from transcribe_ui.state.constants import KNOWN_LEASES_INFO


class LeaseMemory:
    """The last snapshot of the live leases; safe to use from several threads."""

    def __init__(self) -> None:
        """Know no lease yet."""
        self._lock = threading.Lock()
        self._snapshot: LeaseSnapshot | None = None

    def remember(self, snapshot: LeaseSnapshot) -> None:
        """Replace the snapshot by a newer reading."""
        with self._lock:
            self._snapshot = snapshot

    def snapshot(self) -> LeaseSnapshot | None:
        """Return the last snapshot (None before the first refresh read the leases)."""
        with self._lock:
            return self._snapshot


def known_leases(session: Session) -> LeaseSnapshot | None:
    """Return the lease snapshot the session was opened with."""
    found = session.info.get(KNOWN_LEASES_INFO)
    return found if isinstance(found, LeaseSnapshot) else None
