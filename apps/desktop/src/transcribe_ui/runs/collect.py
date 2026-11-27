"""Run objects kept locally, then deleted from R2 (plan m5 §1 "Run objects", architecture §3.7).

Once a run is final (its pod is gone, or it never had one), `refresh_status` keeps the worker's
last `status.json` in the run row (if the run's end did not already), writes `worker.log` to the
user log dir (`runs/worker_logs.py`: whole, never over a log kept before), then deletes every
object under `runs/<run_id>/` and marks the run collected (last). A failure is logged and leaves
the run uncollected: the next refresh tries again.

Only the run's worker writes these objects, and it is gone by then; but a straggler pod or queued
pod carrying the run's name may still write them. Collection therefore waits while one is live
in the Runpod lists (`runs/lingering.py`). Listing a straggler in `reconcile_cloud`, or stopping
it through `terminate_pod` or `cancel_queued_pod`, marks a collected run uncollected
(`reopen_collection`); the refresh after the straggler is gone collects what it wrote, so a
straggler's log is kept once, whole, not in parts.
"""

import logging
from collections.abc import Callable
from datetime import datetime

from transcribe_contracts.constants import RUNS_PREFIX
from transcribe_contracts.layout import run_log_key
from transcribe_ui.runs.payloads import RunPayload
from transcribe_ui.runs.records import read_payload, uncollected_runs, update_run
from transcribe_ui.runs.worker import read_worker
from transcribe_ui.runs.worker_logs import keep_log
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import ObjectStore

logger = logging.getLogger(__name__)


# Whether a live straggler still carries a final run's name (its pod name and payload given).
type StragglerCheck = Callable[[str, RunPayload], bool]


def collect_run_objects(
    state: LocalState,
    store: ObjectStore,
    now: datetime,
    straggling: StragglerCheck | None = None,
) -> tuple[str, ...]:
    """Keep and delete the objects of every final run not collected yet; return the run ids collected now.

    A run a live straggler carries the name of (`straggling`) waits for the next refresh.

    Raises:
        DomainError: `straggling` could not read Runpod (nothing more is collected).
    """
    collected: list[str] = []
    for uncollected in uncollected_runs(state):
        run_id = uncollected.run_id
        if straggling is not None and straggling(uncollected.pod_name, uncollected.payload):
            logger.info("Run %s: a straggler still carries its name; its objects are collected later", run_id)
        elif _collected(state, store, run_id, now):
            collected.append(run_id)
    return tuple(collected)


def _collected(state: LocalState, store: ObjectStore, run_id: str, now: datetime) -> bool:
    """Collect one run; return whether that worked (a failure is logged, the next refresh retries)."""
    try:
        _collect(state, store, run_id, now)
    except StorageError as error:
        logger.warning("Collecting the objects of run %s failed (%s); retried at the next refresh", run_id, error.code)
        return False
    except OSError as error:
        logger.warning("Keeping the worker log of run %s failed (%s); retried at the next refresh", run_id, error)
        return False
    return True


def _collect(state: LocalState, store: ObjectStore, run_id: str, now: datetime) -> None:
    """Keep the run's status and log, delete `runs/<run_id>/`, mark the run collected.

    Raises:
        StorageError: R2 failed.
        OSError: The log could not be written.
    """
    if read_payload(state, run_id).final_status is None:
        report = read_worker(store, run_id)
        if report is not None and report.status is not None:
            # Kept before anything is deleted.
            update_run(state, run_id, now, changes={"final_status": report.status})
    log = store.read(run_log_key(run_id))
    if log is not None:
        keep_log(state.paths.log_dir, run_id, log)
    for found in store.list_objects(f"{RUNS_PREFIX}{run_id}/"):
        store.delete(found.key)
    update_run(state, run_id, now, changes={"objects_collected": True})
