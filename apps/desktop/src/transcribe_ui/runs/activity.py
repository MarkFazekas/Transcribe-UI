"""The run-activity check (plan m5 §1 "Run activity"): is a worker run active right now?

Used by `launch_worker`, `cleanup_cloud`, the manifest step and `publish_held_manifests`,
always under the runs lock. A run is active when any of these holds:

- a local run is not final (`launching`, `queued`, `running`, or a status this version does not
  know);
- the Runpod lists hold a live `transcribe-ui-*` entry: a pod in any status but `TERMINATED`
  (`EXITED` and `ERROR` pods still exist), or a queued pod in any status but `FAILED`,
  `EXPIRED` and `CANCELLED`, where a `DEPLOYED` one counts until its pod is listed (the pod's
  own entry decides then), reads `TERMINATED`, or has stayed out of the pod list for
  `DEPLOY_SETTLE_S` after the deploy (or past its name deadline): a pod not listed (or 404)
  sooner may be list lag;
- a `runs/*/status.json` in R2 is unreadable, or not final with `updated_at` within
  `HEARTBEAT_STALE_S` of the clock (or ahead of it).

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
    return RunActivity(sources=(*local, *listed, *heartbeats))


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
