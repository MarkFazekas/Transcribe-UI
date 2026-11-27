"""The worker's own report of a run: `runs/<run_id>/status.json`, read through `reading.read_run_status`."""

from dataclasses import dataclass

from transcribe_contracts.classes import RunStatus as WorkerStatus
from transcribe_contracts.errors import ContractError
from transcribe_contracts.layout import run_status_key
from transcribe_contracts.reading import read_run_status
from transcribe_ui.runs.constants import UNREADABLE_STATE
from transcribe_ui.runs.responses import CurrentJob, WorkerProgress
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
