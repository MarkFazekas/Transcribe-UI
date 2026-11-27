"""The manifest step and `publish_held_manifests` (plan m5 §1 "Manifest held back during a run", Q27).

The manifest is the batch's readiness marker: uploaded last, it makes the batch `ready` for the
worker. It is never uploaded while a worker run is active, so a running worker never gets work
its `MAX_RUNTIME_S` and cost estimate did not include: the batch then stays `uploading` with
`manifest_held` set, and `publish_held_manifests` (called by `launch_worker` and
`refresh_status`) uploads it once no run is active. Both run under the runs lock, which is also
held by every request that starts a run, so no run can start between the activity check and
the upload.

Order: the manifest object is written first, then the batch moves to `ready` by compare-and-set.
A crash in between leaves a complete manifest for an `uploading` batch (the worker may process
it, which is correct); the next manifest step writes the same bytes again and moves the batch.
Exclusion and re-review withdraw such a manifest before deleting audio.
"""

from typing import Literal

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.constants import UPLOADING_PHASES
from transcribe_ui.batches.phases import load_batch, require_phase, touch_batch
from transcribe_ui.runs.activity import check_activity
from transcribe_ui.runs.cloud import CloudAccess
from transcribe_ui.runs.errors import ActivityUnknownError
from transcribe_ui.state.database import compare_and_set_phase
from transcribe_ui.state.errors import LockOrderError
from transcribe_ui.state.models import BatchRow
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.classes import HeldPublication, ManifestStep
from transcribe_ui.upload.errors import NothingToTranscribeError, UploadIncompleteError
from transcribe_ui.upload.manifest import build_manifest, held_batch_ids, write_manifest

# What happened to one held batch: published, skipped as incomplete, or no longer held (moved on).
type HeldOutcome = Literal["published", "incomplete", "moved"]


def manifest_step(context: RequestContext, cloud: CloudAccess, batch_id: str) -> ManifestStep:
    """Upload the batch's manifest (→ `ready`), or hold it back while a run is active.

    The caller holds the batch's lock and not the runs lock (lock order runs → batch: the runs
    lock is taken here through its bounded wait). The manifest is built from the database; the
    upload must be complete. When the activity check cannot read the cloud, or the manifest
    cannot be written, the batch is marked held and the error is raised: re-sending, or the
    next `publish_held_manifests`, resumes.

    Raises:
        RunsBusyError: The runs lock stayed held for the whole wait; nothing was changed.
        CancelledError: Cancelled while waiting for the runs lock; nothing was changed.
        WrongPhaseError: The batch is not `uploading`.
        UploadIncompleteError: An item is in review or an audio object is not verified.
        NothingToTranscribeError: The batch has no `transcribe` job (extract-only path).
        ActivityUnknownError: The Runpod lists or R2 could not be read (the manifest is held).
        StorageError: The manifest could not be written (the manifest is held).
        LockOrderError: The calling thread already holds the runs lock (a programming error:
            launch and refresh use `publish_held_manifests`).
    """
    state = context.ports.state
    if state.locks.holds_runs():
        msg = "manifest_step takes the runs lock itself; under it, use publish_held_manifests."
        raise LockOrderError(msg)
    with state.locks.runs(context.cancel.is_cancelled):
        with state.session() as session:
            require_phase(load_batch(session, batch_id), UPLOADING_PHASES)
            manifest = build_manifest(session, batch_id)
        try:
            activity = check_activity(state, cloud, context.ports.clock.now())
        except ActivityUnknownError:
            _hold(context, batch_id)
            raise
        if activity.active:
            _hold(context, batch_id)
            return ManifestStep(batch_id=batch_id, outcome="held", activity=activity.sources)
        try:
            write_manifest(cloud.store, manifest)
        except StorageError:
            _hold(context, batch_id)
            raise
        _move_to_ready(context, batch_id, held_only=False)
    return ManifestStep(batch_id=batch_id, outcome="published")


def publish_held_manifests(context: RequestContext, cloud: CloudAccess) -> HeldPublication:
    """Upload the held manifests (→ `ready`) if no run is active; the caller holds the runs lock.

    Each held batch's lock is only tried (lock order runs → batch, never waiting): a busy batch
    is skipped until the next call. Each manifest is built from the database at publish time
    and published only when the upload is complete (no item in review, every object verified).

    Raises:
        ActivityUnknownError: The Runpod lists or R2 could not be read (nothing was published).
        StorageError: A manifest could not be written (the ones before it are published).
        LockOrderError: The calling thread does not hold the runs lock (a programming error).
    """
    state = context.ports.state
    if not state.locks.holds_runs():
        msg = "publish_held_manifests needs the runs lock; take it first."
        raise LockOrderError(msg)
    with state.session() as session:
        held = held_batch_ids(session)
    if not held:
        return HeldPublication()
    activity = check_activity(state, cloud, context.ports.clock.now())
    if activity.active:
        return HeldPublication(held=tuple(held), activity=activity.sources)
    outcomes: dict[str, list[str]] = {"published": [], "incomplete": [], "moved": [], "busy": []}
    for batch_id in held:
        with state.locks.try_batch(batch_id) as taken:
            outcome = _publish_held(context, cloud.store, batch_id) if taken else "busy"
        outcomes[outcome].append(batch_id)
    return HeldPublication(
        published=tuple(outcomes["published"]),
        busy=tuple(outcomes["busy"]),
        incomplete=tuple(outcomes["incomplete"]),
    )


def _publish_held(context: RequestContext, store: ObjectStore, batch_id: str) -> HeldOutcome:
    """Publish one held batch whose lock this thread holds."""
    with context.ports.state.session() as session:
        row = load_batch(session, batch_id)
        if row.phase != "uploading" or not row.manifest_held:
            return "moved"
        try:
            manifest = build_manifest(session, batch_id)
        except (UploadIncompleteError, NothingToTranscribeError):
            return "incomplete"
    write_manifest(store, manifest)
    _move_to_ready(context, batch_id, held_only=True)
    return "published"


def _hold(context: RequestContext, batch_id: str) -> None:
    with context.ports.state.session() as session:
        now = context.ports.clock.now()
        touch_batch(session, batch_id, UPLOADING_PHASES, now=now, also_set={"manifest_held": True})


def _move_to_ready(context: RequestContext, batch_id: str, *, held_only: bool) -> None:
    conditions = (BatchRow.manifest_held.is_(True),) if held_only else ()
    with context.ports.state.session() as session:
        now = context.ports.clock.now()
        also_set = {"manifest_held": False}
        compare_and_set_phase(
            session, batch_id, "uploading", "ready", now=now, conditions=conditions, also_set=also_set
        )
