"""The manifest step (architecture §3.3 "Manifest step", §3.9 "Run scope").

The manifest is the batch's readiness marker: uploaded last, it makes the batch `ready`. It is
published as soon as the upload is verified, on any installation, without an activity check: a
run processes only the batches its lease lists (A21), so a batch published during a run waits
for the next one (`waits_for_current_run`).

Order: a pending withdrawal of a leftover manifest is finished first (under the runs lock, while
no live lease lists the batch), then the manifest object is written, then the batch moves to
`ready` by compare-and-set. A crash in between leaves a complete manifest for an `uploading`
batch; the next manifest step writes the same bytes again and moves the batch.
"""

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.constants import UPLOADING_PHASES
from transcribe_ui.batches.phases import load_batch, require_phase
from transcribe_ui.runs.leases import refuse_if_leased
from transcribe_ui.state.errors import LockOrderError
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.manifest import build_manifest, write_manifest
from transcribe_ui.upload.steps import finish_withdrawal, move_to_ready


def manifest_step(context: RequestContext, store: ObjectStore, batch_id: str) -> None:
    """Upload the batch's manifest and move the batch to `ready`.

    The caller holds the batch's lock and not the runs lock (lock order runs → batch: the runs
    lock is taken here, through its bounded wait, only to finish a pending withdrawal). The
    manifest is built from the database; the upload must be complete.

    Raises:
        WrongPhaseError: The batch is not `uploading`.
        UploadIncompleteError: An item is in review or an audio object is not verified.
        NothingToTranscribeError: The batch has no `transcribe` job (extract-only path).
        RunActiveError: A pending withdrawal cannot be finished: a live lease lists the batch.
        RunsBusyError: The runs lock stayed held for the whole wait (nothing was changed).
        StorageError: R2 failed; re-sending resumes.
        LockOrderError: The calling thread already holds the runs lock (a programming error).
    """
    state = context.ports.state
    if state.locks.holds_runs():
        msg = "manifest_step takes the runs lock itself; it must not be held already."
        raise LockOrderError(msg)
    with state.session() as session:
        row = load_batch(session, batch_id)
        require_phase(row, UPLOADING_PHASES)
        pending = row.pending_withdrawal is not None
        manifest = build_manifest(session, batch_id)
    if pending:
        with state.locks.runs(context.cancel.is_cancelled):
            refuse_if_leased(store, batch_id, context.ports.clock.now())
            finish_withdrawal(context, store, batch_id)
    write_manifest(store, manifest)
    move_to_ready(context, batch_id)
