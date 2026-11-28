"""`refresh_status`: follow the runs, read the results, collect run objects (plan m5 M5.12, §3.7, §3.9).

The GUI sends it at start-up and periodically while a run is not final or results are awaited
(`awaiting_results`), with or without the Runpod key. Everything runs under the runs lock, and
no database transaction is open across a Runpod or R2 call. In order:

1. With a Runpod key, every run that is not final is followed (`runs/refreshing.py`):
   `launching` runs are resolved by their name, a queued pod is followed to the pod it deploys, a
   pod read records its status, price and first `RUNNING` sighting, and the worker's
   `status.json` is read. A run whose pod is gone ends (`finished` when the worker reported so,
   else `failed`; not while R2 cannot be read); past its desktop deadline its pod is stopped,
   verified, and the run fails, whatever R2 does. Only the Runpod key is needed up to here: R2
   is opened on first use (`LazyStore`), and an R2 error is raised once every run was handled.
   The lease of every run that became final is deleted (`runs/lease_release.py`). Without a key
   (an R2-only installation, architecture §3.9 "Roles") nothing is followed: a run left open
   records `credentials_missing` as its `stop_error`.
2. Every `ready` batch gets its job results and, with `_done.json`, moves to `transcribed`
   (`runs/results.py`), and the live run leases are kept in memory with the refresh time
   (`waits_for_current_run`); then each followed run's progress and remaining time are computed
   from the jobs done and stored (`runs/remaining.py`).
3. With a key, the objects of final runs are kept locally and deleted (`runs/collect.py`), except
   while a live straggler carries a run's name (`runs/lingering.py`). The Runpod lists are read
   once, when a run is open or a final run waits for collection.
"""

from contextlib import ExitStack

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runs.classes import RefreshStatusRequest
from transcribe_ui.runs.cloud import CloudAccess, LazyStore, open_runpod, runpod_key_set
from transcribe_ui.runs.lease_release import releasing_leases
from transcribe_ui.runs.leases import remember_leases
from transcribe_ui.runs.lingering import collect_settled, read_lists
from transcribe_ui.runs.progress import runs_progress
from transcribe_ui.runs.records import note_key_missing, open_runs
from transcribe_ui.runs.refreshing import Refreshed, refresh_open_runs
from transcribe_ui.runs.remaining import record_progress
from transcribe_ui.runs.responses import RefreshStatusResponse
from transcribe_ui.runs.results import check_ready_batches
from transcribe_ui.runs.settling import Listed


def refresh_status(_request: RefreshStatusRequest, context: RequestContext) -> RefreshStatusResponse:
    """Follow the open runs (with a Runpod key), record the `ready` batches' results, collect run objects.

    Raises:
        DomainError: The Runpod lists, a lookup or R2 could not be read (`cloud_unreachable`, a
            key or an `r2_*` error), or an R2 credential is missing; what was recorded before
            stays, and re-sending resumes.
        RunsBusyError: Another request held the runs lock for the whole wait.
    """
    state = context.ports.state
    with state.locks.runs(context.cancel.is_cancelled), ExitStack() as stack:
        store = LazyStore(context)
        client = stack.enter_context(open_runpod(context)) if runpod_key_set(context) else None
        open_ids = [run.run_id for run in open_runs(state)]
        refreshed, listed = _follow_runs(context, client, store)
        checked = check_ready_batches(context, store.get())
        remember_leases(state, store.get(), context.ports.clock.now())
        record_progress(context, refreshed)
        if client is not None:
            cloud = CloudAccess(store.get(), client.pods)
            collect_settled(state, cloud, listed, context.ports.clock.now())
        runs = runs_progress(state, open_ids)
    return RefreshStatusResponse(
        runs=runs,
        batches=checked.batches,
        busy=checked.busy,
        awaiting_results=checked.awaiting,
    )


def _follow_runs(
    context: RequestContext,
    client: RunpodClient | None,
    store: LazyStore,
) -> tuple[list[Refreshed], Listed | None]:
    """Follow the open runs and release the leases of those that end; without a key, note why nothing is followed.

    Raises:
        DomainError: Runpod or R2 failed (see `refresh_open_runs`).
    """
    state = context.ports.state
    if client is None:
        note_key_missing(state, context.ports.clock.now())
        return [], None
    with releasing_leases(context, store):
        listed = read_lists(state, client.pods)
        return refresh_open_runs(context, client.pods, store, listed), listed
