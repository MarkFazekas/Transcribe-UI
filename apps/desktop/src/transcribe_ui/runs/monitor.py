"""`refresh_status`: follow the runs, read the results, publish held manifests (plan m5 M5.12, §3.7).

The GUI sends it periodically while a run is active and once at start-up. Everything runs under
the runs lock, and no database transaction is open across a Runpod or R2 call. In order:

1. Every run that is not final is followed (`runs/refreshing.py`): `launching` runs are resolved
   by their name, a queued pod is followed to the pod it deploys, a pod read records its status,
   price and first `RUNNING` sighting, and the worker's `status.json` is read. A run whose pod is
   gone ends (`finished` when the worker reported so, else `failed`; not while R2 cannot be
   read); past its desktop deadline its pod is stopped, verified, and the run fails, whatever R2
   does. Only the Runpod key is needed up to here: R2 is opened on first use (`LazyStore`), and
   an R2 error is raised once every run was handled. The lease of every run that became final
   is deleted (`runs/lease_release.py`).
2. Every `ready` batch gets its job results and, with `_done.json`, moves to `transcribed`
   (`runs/results.py`), and the live run leases are kept in memory with the refresh time
   (`waits_for_current_run`); then each followed run's progress and remaining time are computed from
   the jobs done and stored (`runs/remaining.py`).
3. The objects of final runs are kept locally and deleted (`runs/collect.py`), except while a
   live straggler carries a run's name (`runs/lingering.py`). The Runpod lists are read once,
   when a run is open or a final run waits for collection.
4. Once no local run is open, `publish_held_manifests` publishes the held manifests (it checks
   the run activity itself, so a straggler or a recent heartbeat keeps them held).
"""

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runs.classes import RefreshStatusRequest
from transcribe_ui.runs.cloud import CloudAccess, LazyStore, open_runpod
from transcribe_ui.runs.lease_release import releasing_leases
from transcribe_ui.runs.leases import remember_leases
from transcribe_ui.runs.lingering import collect_settled, read_lists
from transcribe_ui.runs.progress import runs_progress
from transcribe_ui.runs.records import open_runs
from transcribe_ui.runs.refreshing import refresh_open_runs
from transcribe_ui.runs.remaining import record_progress
from transcribe_ui.runs.responses import RefreshStatusResponse
from transcribe_ui.runs.results import check_ready_batches
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
    state = context.ports.state
    with state.locks.runs(context.cancel.is_cancelled), open_runpod(context) as client:
        store = LazyStore(context)
        with releasing_leases(context, store) as ended:
            listed = read_lists(state, client.pods)
            refreshed = refresh_open_runs(context, client.pods, store, listed)
            ended.release()
        cloud = CloudAccess(store.get(), client.pods)
        checked = check_ready_batches(context, cloud.store)
        remember_leases(state, cloud.store, context.ports.clock.now())
        record_progress(context, refreshed)
        collect_settled(state, cloud, listed, context.ports.clock.now())
        published = _publish(context, cloud)
        runs = runs_progress(state, [each.run_id for each in refreshed])
    return RefreshStatusResponse(
        runs=runs,
        batches=checked.batches,
        busy=checked.busy,
        published=published.published,
        held=_still_held(published),
    )


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
