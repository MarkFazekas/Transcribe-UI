"""The worker's own report of a run: `runs/<run_id>/status.json`, read through `reading.read_run_status`."""

from dataclasses import dataclass

from transcribe_contracts.classes import RunStatus as WorkerStatus
from transcribe_contracts.errors import ContractError
from transcribe_contracts.layout import run_status_key
from transcribe_contracts.reading import read_run_status
from transcribe_ui.runs.constants import UNREADABLE_STATE
from transcribe_ui.runs.payloads import StoredCurrentJob, StoredWorkerReport
from transcribe_ui.state.models import ItemRow
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.r2 import ObjectStore


@dataclass(frozen=True)
class WorkerReport:
    """A `status.json` as found: its content, or None when it cannot be read (invalid, another major)."""

    status: WorkerStatus | None

    @property
    def state(self) -> str:
        """Return the worker state (`unreadable` for a report that cannot be read)."""
        return UNREADABLE_STATE if self.status is None else self.status.state

    def stored(self, name: str | None) -> StoredWorkerReport:
        """Return the report as the run payload keeps it, the current job named `name`."""
        status = self.status
        if status is None:
            return StoredWorkerReport(state=UNREADABLE_STATE)
        current = status.current
        job = None
        if current is not None:
            job = StoredCurrentJob(
                batch_id=current.batch_id, item_id=current.item_id, language=current.language, name=name
            )
        return StoredWorkerReport(
            state=status.state,
            updated_at=status.updated_at,
            current=job,
            done_jobs=status.done_jobs,
            total_jobs=status.total_jobs,
            error=status.error,
        )


def stored_report(state: LocalState, report: WorkerReport) -> StoredWorkerReport:
    """Return the report to store, its current job named by its item's file (own transaction).

    Best effort: an item this database does not know (or of another batch), or a stored item
    payload without a readable name, gives no name; it never fails the refresh.
    """
    status = report.status
    current = None if status is None else status.current
    if current is None:
        return report.stored(None)
    with state.session() as session:
        row = session.get(ItemRow, current.item_id)
        of_batch = row is not None and row.batch_id == current.batch_id
        found = row.payload if of_batch and row is not None else None
    name = found.get("name") if isinstance(found, dict) else None
    return report.stored(name if isinstance(name, str) else None)


def read_worker(store: ObjectStore, run_id: str) -> WorkerReport | None:
    """Return the run's worker report, or None while the worker has written none.

    Raises:
        StorageError: R2 could not be read.
    """
    key = run_status_key(run_id)
    body = store.read(key)
    if body is None:
        return None
    try:
        return WorkerReport(read_run_status(key, body))
    except ContractError:
        return WorkerReport(None)
