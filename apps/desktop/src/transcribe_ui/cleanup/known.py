"""Cleaning up a batch this database knows (architecture §3.3 "Cloud cleanup").

The caller holds the runs lock and the batch's lock, and the batch passed `cleanup/targets.py`.

- The run activity is checked (refused while a run is active) unless the batch was never
  published and R2 holds no manifest for it: a batch in `uploading` is invisible to workers, so
  its cleanup is allowed during a run.
- The intent is recorded first, in one transaction: `audio_deleted` (both scopes, so no retry can
  start from audio being deleted), the payload's `cleanup_pending` (its scope: `launch_worker`
  and `plan_launch` refuse while any batch has it, since its manifest or audio may still be in
  R2), and for `everything` the phase `cleaned`, with `manifest_held` and a pending withdrawal
  cleared (nothing may publish the batch again).
- Then the runs lock is released: no worker can start while the marker is set, and the batch
  lock (still held) and the phase keep the manifest step and `publish_held_manifests` out, so
  a long delete never blocks `refresh_status` (the deadline stop) or `cancel_worker`.
- The deletes run (`cleanup/stages.py`), for `everything` the batch's temp folder is emptied,
  and a last transaction clears the marker. Repeating an interrupted cleanup (`everything` is
  accepted again in `cleaned`) finishes it; missing objects are not errors.

No R2 or Runpod call is made inside a database transaction.
"""

from collections.abc import Callable

from sqlalchemy.orm import Session

from transcribe_contracts.layout import manifest_key
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.payloads import BatchPayload, CleanupScope
from transcribe_ui.batches.phases import load_batch, touch_batch
from transcribe_ui.batches.records import stored_items, summary_of
from transcribe_ui.cleanup.classes import CleanupCloudRequest, CleanupCloudResponse
from transcribe_ui.cleanup.constants import CLEANED, PUBLISHED_PHASES
from transcribe_ui.cleanup.stages import Purge, delete_audio, delete_everything
from transcribe_ui.cleanup.targets import CleanupTarget
from transcribe_ui.runs.cloud import open_cloud
from transcribe_ui.runs.guards import require_no_run
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.temp import clear_temp


def cleanup_known(
    request: CleanupCloudRequest,
    context: RequestContext,
    target: CleanupTarget,
    release_runs: Callable[[], None],
) -> CleanupCloudResponse:
    """Record the cleanup, release the runs lock, then delete the batch's audio or all its cloud data.

    The caller holds the runs lock and the batch's lock; `release_runs` gives the runs lock up.

    Raises:
        RunActiveError: A run is active and the batch may be visible to it (nothing changed).
        ActivityUnknownError: The run activity cannot be checked (nothing changed).
        DomainError: The credentials, R2 or the database failed, or the request was cancelled;
            repeating it resumes (the batch keeps `cleanup_pending` until then).
    """
    everything = request.scope == "everything"
    phase = CLEANED if everything else target.phase
    with open_cloud(context) as cloud:
        if _visible_to_workers(request, target, cloud.store):
            require_no_run(context, cloud)
        _record(context, request, target)
        release_runs()
        purge = Purge(cloud.store, context, request.action)
        delete = delete_everything if everything else delete_audio
        aborted = delete(purge, target.batch_id)
    if everything:
        clear_temp(context.ports.state.paths, target.batch_id)
    now = context.ports.clock.now()
    with context.ports.state.session() as session:
        touch_batch(session, target.batch_id, (phase,), now=now)
        _mark(session, target.batch_id, None)
        row = load_batch(session, target.batch_id)
        summary = summary_of(row, stored_items(session, target.batch_id))
    return CleanupCloudResponse(
        batch_id=target.batch_id,
        scope=request.scope,
        batch=summary,
        deleted_objects=purge.deleted,
        aborted_uploads=aborted,
    )


def _visible_to_workers(request: CleanupCloudRequest, target: CleanupTarget, store: ObjectStore) -> bool:
    """Return whether a worker may read the batch: it was published, or R2 holds a manifest for it.

    Raises:
        StorageError: R2 could not be read.
    """
    if request.scope == "audio" or target.phase in PUBLISHED_PHASES:
        return True
    return store.head(manifest_key(target.batch_id)) is not None


def _record(context: RequestContext, request: CleanupCloudRequest, target: CleanupTarget) -> None:
    """Record the cleanup before any delete: `audio_deleted`, the marker, and for `everything` the phase `cleaned`.

    Raises:
        PhaseConflictError: The batch left its phase meanwhile (it cannot under the batch lock).
    """
    changes: dict[str, object] = {"audio_deleted": True}
    if request.scope == "everything":
        changes.update({"phase": CLEANED, "manifest_held": False, "pending_withdrawal": None})
    with context.ports.state.session() as session:
        now = context.ports.clock.now()
        touch_batch(session, target.batch_id, (target.phase,), now=now, also_set=changes)
        _mark(session, target.batch_id, request.scope)


def _mark(session: Session, batch_id: str, scope: CleanupScope | None) -> None:
    """Set (or clear, with None) the batch payload's `cleanup_pending`."""
    row = load_batch(session, batch_id)
    payload = BatchPayload.model_validate(row.payload).model_copy(update={"cleanup_pending": scope})
    row.payload = payload.model_dump(mode="json")
