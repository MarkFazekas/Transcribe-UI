"""The worker's own report of a run: `runs/<run_id>/status.json`, read through `reading.read_run_status`."""

from dataclasses import dataclass

from transcribe_contracts.classes import RunStatus as WorkerStatus
from transcribe_contracts.errors import ContractError
from transcribe_contracts.layout import run_status_key
from transcribe_contracts.reading import read_run_status
from transcribe_ui.batches.payloads import ItemPayload
from transcribe_ui.runs.constants import UNREADABLE_STATE
from transcribe_ui.runs.responses import CurrentJob, WorkerProgress
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

    def progress(self) -> WorkerProgress:
        """Return the report as the frontends show it."""
        status = self.status
        if status is None:
            return WorkerProgress(state=UNREADABLE_STATE)
        current = status.current
        job = None if current is None else CurrentJob(**current.model_dump())
        return WorkerProgress(
            state=status.state,
            updated_at=status.updated_at,
            current=job,
            done_jobs=status.done_jobs,
            total_jobs=status.total_jobs,
            error=status.error,
        )


def worker_progress(state: LocalState, report: WorkerReport) -> WorkerProgress:
    """Return the report as the frontends show it, the current job named by its file (own transaction)."""
    progress = report.progress()
    current = progress.current
    if current is None:
        return progress
    with state.session() as session:
        row = session.get(ItemRow, current.item_id)
        name = None
        if row is not None and row.batch_id == current.batch_id:
            name = ItemPayload.model_validate(row.payload).name
    named = current.model_copy(update={"name": name})
    return progress.model_copy(update={"current": named})


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
