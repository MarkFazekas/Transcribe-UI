"""`refresh_status`: follow the runs, read the results, publish held manifests (plan m5 M5.12, §3.7).

The GUI sends it periodically while a run is active and once at start-up. Everything runs under
the runs lock, and no database transaction is open across a Runpod or R2 call. In order:

1. Every run that is not final is followed (`runs/refreshing.py`): `launching` runs are resolved
   by their name, a queued pod is followed to the pod it deploys, a pod read records its status,
   price and first `RUNNING` sighting, and the worker's `status.json` is read. A run whose pod is
   gone ends (`finished` when the worker reported so, else `failed`; not while R2 cannot be
   read); past its desktop deadline its pod is stopped, verified, and the run fails, whatever R2
   does. Only the Runpod key is needed up to here: R2 is opened on first use (`LazyStore`), and
   an R2 error is raised once every run was handled.
2. Every `ready` batch gets its job results and, with `_done.json`, moves to `transcribed`
   (`runs/results.py`).
3. The objects of final runs are kept locally and deleted (`runs/collect.py`).
4. Once no local run is open, `publish_held_manifests` publishes the held manifests (it checks
   the run activity itself, so a straggler or a recent heartbeat keeps them held).
"""

from datetime import datetime

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.classes import RefreshStatusRequest
from transcribe_ui.runs.cloud import CloudAccess, LazyStore, open_runpod
from transcribe_ui.runs.collect import collect_run_objects
from transcribe_ui.runs.progress import run_progress
from transcribe_ui.runs.records import open_runs
from transcribe_ui.runs.refreshing import Refreshed, first_failure, refresh_runs
from transcribe_ui.runs.responses import RefreshStatusResponse, RunProgress
from transcribe_ui.runs.results import check_ready_batches
from transcribe_ui.state.store import LocalState
from transcribe_ui.upload.classes import HeldPublication
from transcribe_ui.upload.manifest import held_batch_ids
from transcribe_ui.upload.publish import publish_held_manifests


def refresh_status(_request: RefreshStatusRequest, context: RequestContext) -> RefreshStatusResponse:
    """Follow the open runs, record the `ready` batches' results, collect run objects, publish held manifests.

    Raises:
        DomainError: The Runpod lists, a lookup or R2 could not be read (`cloud_unreachable`, a
            key or an `r2_*` error), or a credential is missing; what was recorded before stays,
            and re-sending resumes.
        RunsBusyError: Another request held the runs lock for the whole wait.
    """
    ports = context.ports
    state = ports.state
    with state.locks.runs(context.cancel.is_cancelled), open_runpod(context) as client:
        store = LazyStore(context)
        refreshed = _runs_refreshed(context, client.pods, store)
        cloud = CloudAccess(store.get(), client.pods)
        checked = check_ready_batches(context, cloud.store)
        collect_run_objects(state, cloud.store, ports.clock.now())
        published = _publish(context, cloud)
        now = ports.clock.now()
        runs = tuple(_progress(state, each, now) for each in refreshed)
    return RefreshStatusResponse(
        runs=runs,
        batches=checked.batches,
        busy=checked.busy,
        published=published.published,
        held=_still_held(published),
    )


def _runs_refreshed(context: RequestContext, pods: PodsApi, store: LazyStore) -> list[Refreshed]:
    """Follow the open runs; raise the first R2 failure once every run was handled.

    Raises:
        DomainError: Runpod or R2 (or the R2 credentials) failed.
    """
    refreshed = refresh_runs(context, pods, store)
    failure = first_failure(refreshed)
    if failure is not None:
        raise failure
    return refreshed


def _publish(context: RequestContext, cloud: CloudAccess) -> HeldPublication:
    """Publish the held manifests once no local run is open (the activity check runs inside)."""
    state = context.ports.state
    if open_runs(state):
        with state.session() as session:
            return HeldPublication(held=tuple(held_batch_ids(session)))
    return publish_held_manifests(context, cloud)


def _still_held(published: HeldPublication) -> tuple[str, ...]:
    """Return the batches whose manifest stays held: a run is active, or the batch was busy or incomplete."""
    still = {*published.held, *published.busy}
    still.update(published.incomplete)
    return tuple(sorted(still))


def _progress(state: LocalState, refreshed: Refreshed, now: datetime) -> RunProgress:
    return run_progress(state, refreshed.run_id, refreshed.worker, refreshed.stop_error, now)
