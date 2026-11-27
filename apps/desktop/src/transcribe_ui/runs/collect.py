"""Run objects kept locally, then deleted from R2 (plan m5 §1 "Run objects", architecture §3.7).

Once a run is final (its pod is gone, or it never had one), `refresh_status` keeps the worker's
last `status.json` in the run row (if the run's end did not already), writes `worker.log` to the
user log dir (`worker-<run_id>.log`), then deletes every object under `runs/<run_id>/` and marks
the run collected. The worker is the only writer of these objects and is gone by then. A failure
is logged and leaves the run uncollected: the next refresh tries again.
"""

import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from transcribe_contracts.constants import RUNS_PREFIX
from transcribe_contracts.layout import run_log_key
from transcribe_ui.runs.constants import FINAL_RUN_STATUSES, WORKER_LOG_NAME
from transcribe_ui.runs.records import payload_of, read_payload, update_run
from transcribe_ui.runs.worker import read_worker
from transcribe_ui.state.models import RunRow
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import ObjectStore

logger = logging.getLogger(__name__)


def worker_log_path(log_dir: Path, run_id: str) -> Path:
    """Return where a run's worker log is kept."""
    return log_dir / WORKER_LOG_NAME.format(run_id=run_id)


def collect_run_objects(state: LocalState, store: ObjectStore, now: datetime) -> tuple[str, ...]:
    """Keep and delete the objects of every final run not collected yet; return the run ids collected now."""
    collected: list[str] = []
    for run_id in _uncollected(state):
        try:
            _collect(state, store, run_id, now)
        except StorageError as error:
            logger.warning(
                "Collecting the objects of run %s failed (%s); retried at the next refresh", run_id, error.code
            )
            continue
        except OSError as error:
            logger.warning("Keeping the worker log of run %s failed (%s); retried at the next refresh", run_id, error)
            continue
        collected.append(run_id)
    return tuple(collected)


def _uncollected(state: LocalState) -> list[str]:
    final = RunRow.status.in_(FINAL_RUN_STATUSES)
    with state.session() as session:
        query = select(RunRow).where(final).order_by(RunRow.run_id)
        rows = list(session.scalars(query))
        return [row.run_id for row in rows if _uncollected_row(row)]


def _uncollected_row(row: RunRow) -> bool:
    return not payload_of(row).objects_collected


def _collect(state: LocalState, store: ObjectStore, run_id: str, now: datetime) -> None:
    """Keep the run's status and log, delete `runs/<run_id>/`, mark the run collected.

    Raises:
        StorageError: R2 failed.
        OSError: The log could not be written.
    """
    changes: dict[str, object] = {"objects_collected": True}
    if read_payload(state, run_id).final_status is None:
        report = read_worker(store, run_id)
        if report is not None and report.status is not None:
            changes["final_status"] = report.status
    log = store.read(run_log_key(run_id))
    if log is not None:
        target = worker_log_path(state.paths.log_dir, run_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(log)
    for found in store.list_objects(f"{RUNS_PREFIX}{run_id}/"):
        store.delete(found.key)
    update_run(state, run_id, now, changes=changes)
