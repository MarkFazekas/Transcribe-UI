"""Following every run that is not final, once per `refresh_status` (architecture §3.7).

Each run is followed in Runpod (`runs/following.py`) and its worker report read. A run whose
pod is gone ends; one past its desktop deadline gets its pod stopped, verified, and fails
(`runs/ending.py`). The Runpod lists are read once, and only when such a run exists.
"""

from dataclasses import dataclass

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runs.cloud import CloudAccess
from transcribe_ui.runs.constants import FINAL_RUN_STATUSES
from transcribe_ui.runs.ending import desktop_deadline, end_run, stop_at_deadline
from transcribe_ui.runs.errors import runpod_call
from transcribe_ui.runs.following import follow_run
from transcribe_ui.runs.records import open_runs, read_payload
from transcribe_ui.runs.settling import Listed
from transcribe_ui.runs.worker import WorkerReport, read_worker


@dataclass(frozen=True)
class Refreshed:
    """One run after its refresh: the worker's report, and the error of a deadline stop that does not show yet."""

    run_id: str
    worker: WorkerReport | None
    stop_error: str | None = None


def refresh_runs(context: RequestContext, cloud: CloudAccess) -> list[Refreshed]:
    """Follow every run that is not final, oldest first; end or stop those whose time came.

    Raises:
        DomainError: The Runpod lists, a lookup or R2 could not be read; the runs followed
            before stay recorded.
    """
    before = open_runs(context.ports.state)
    if not before:
        return []
    listed = runpod_call(lambda: Listed.read(cloud.pods))
    return [_refresh_run(context, cloud, run.run_id, listed) for run in before]


def _refresh_run(context: RequestContext, cloud: CloudAccess, run_id: str, listed: Listed) -> Refreshed:
    ports = context.ports
    state = ports.state
    clock = ports.clock
    followed = follow_run(state, cloud.pods, run_id, listed, clock.now())
    status = followed.run.status
    if status in FINAL_RUN_STATUSES or status == "launching":
        return Refreshed(run_id, None)
    worker = read_worker(cloud.store, run_id)
    if followed.gone:
        end_run(state, run_id, worker, clock.now())
        return Refreshed(run_id, worker)
    if clock.now() < desktop_deadline(read_payload(state, run_id)):
        return Refreshed(run_id, worker)
    stop_error = stop_at_deadline(state, cloud.pods, run_id, worker, clock)
    return Refreshed(run_id, worker, stop_error)
