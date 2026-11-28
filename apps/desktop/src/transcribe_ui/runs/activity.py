"""The run-activity check (plan m5 §1 "Run activity"): is a worker run active right now?

Used only by `launch_worker` (one run at a time), under the runs lock; cleanup and withdrawals
use the per-batch lease check instead (`runs/leases.py`), which needs no Runpod key. A run is active when any of these holds:

- a local run is not final (`launching`, `queued`, `running`, or a status this version does not
  know);
- the Runpod lists hold a live `transcribe-ui-*` entry (`runs/pods.py`): a pod in any status
  but `TERMINATED`, or a queued pod in any status but `FAILED`, `EXPIRED` and `CANCELLED`. A
  `DEPLOYED` one is over past its name deadline; before that its pod, found in the full list or
  looked up by id, decides (a 404 counts until `DEPLOY_SETTLE_S` after the deploy), and one
  without `podId` counts;
- a `runs/*/status.json` written recently (`runs/heartbeats.py`, R2 `LastModified`) is
  unreadable, or not final with a recent `updated_at` (capped at the run's deadline);
- a run lease counts (`runs/leases.py`): a `runs/*/launch.json` before its deadline whose run
  has not reported its end, another computer's included. Since the Runpod lists were read, a
  lease written more than `LAUNCH_SETTLE_S` ago whose run has no live entry in them is left
  out: its pod request never created a pod, or the pod is gone (another computer's run that
  ended while nobody followed it), so it does not block a launch for hours.

The check fails closed: if the Runpod lists or R2 cannot be read, it raises
`ActivityUnknownError` (`cloud_unreachable`) instead of answering "inactive".
"""

from datetime import datetime

from sqlalchemy import select

from transcribe_ui.runpod.access import api_failure
from transcribe_ui.runpod.errors import RunpodApiError
from transcribe_ui.runs.classes import ActivitySource, RunActivity
from transcribe_ui.runs.cloud import CloudAccess
from transcribe_ui.runs.constants import FINAL_RUN_STATUSES
from transcribe_ui.runs.errors import ActivityUnknownError
from transcribe_ui.runs.heartbeats import heartbeat_sources
from transcribe_ui.runs.leases import lease_sources, live_leases, may_still_run
from transcribe_ui.runs.pods import pod_sources
from transcribe_ui.state.errors import LockOrderError
from transcribe_ui.state.models import RunRow
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.errors import StorageError


def check_activity(state: LocalState, cloud: CloudAccess, now: datetime) -> RunActivity:
    """Return every sign of a live worker run; the caller holds the runs lock.

    Raises:
        ActivityUnknownError: The Runpod lists or R2 could not be read (fail closed).
        LockOrderError: The calling thread does not hold the runs lock (a programming error).
    """
    if not state.locks.holds_runs():
        msg = "The run-activity check needs the runs lock; take it before checking."
        raise LockOrderError(msg)
    local = _local_runs(state)
    listed = _read_runpod(cloud, now)
    heartbeats = _read_heartbeats(cloud, now)
    leases = _read_leases(cloud, now, listed)
    return RunActivity(sources=(*local, *listed, *heartbeats, *leases))


def _local_runs(state: LocalState) -> list[ActivitySource]:
    query = select(RunRow.run_id, RunRow.status).order_by(RunRow.run_id)
    with state.session() as session:
        rows = session.execute(query).all()
    sources = [_local_source(run_id, status) for run_id, status in rows]
    return [source for source in sources if source.status not in FINAL_RUN_STATUSES]


def _local_source(run_id: str, status: str) -> ActivitySource:
    return ActivitySource(kind="local_run", name=run_id, status=status, run_id=run_id)


def _read_runpod(cloud: CloudAccess, now: datetime) -> list[ActivitySource]:
    try:
        return pod_sources(cloud.pods, now)
    except RunpodApiError as error:
        failure = ActivityUnknownError("runpod", api_failure(error).code)
    # Raised outside the `except` block, so the Runpod error is not chained.
    raise failure


def _read_heartbeats(cloud: CloudAccess, now: datetime) -> list[ActivitySource]:
    try:
        return heartbeat_sources(cloud.store, now)
    except StorageError as error:
        failure = ActivityUnknownError("r2", error.code)
    raise failure


def _read_leases(cloud: CloudAccess, now: datetime, listed: list[ActivitySource]) -> list[ActivitySource]:
    try:
        leases = live_leases(cloud.store, now)
    except StorageError as error:
        failure = ActivityUnknownError("r2", error.code)
        raise failure from None
    in_lists = {source.run_id for source in listed}
    counted = tuple(lease for lease in leases if may_still_run(lease, in_lists, now))
    return lease_sources(counted)
