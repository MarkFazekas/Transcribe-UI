"""Withdrawing a leftover manifest (architecture §3.3 "Order and interruption").

An `uploading` batch has a manifest in R2 only when the manifest step was interrupted between
writing it and moving the batch to `ready`. Before exclusion or re-review changes such a batch,
`withdrawal_guard` checks whether the object at its manifest key is this database's manifest
(the same bytes `build_manifest` gives, or the ones a pending withdrawal recorded); another
computer's batch with the same id is never touched. If it is ours, the guard takes the runs lock
(bounded wait: the caller holds the batch lock) and lets the change go ahead only while no run
is active, so no worker reads the batch during the withdrawal. The change records a
`PendingWithdrawal` in its own transaction; `finish_withdrawal` (`upload/steps.py`) then
deletes the results of the affected items, `_done.json` and the manifest last, and clears the
record. A failure leaves the
record, and the next manifest step (or `publish_held_manifests`) finishes it first. Results of
the other items stay, so the worker redoes only the jobs whose result is gone.
"""

from collections.abc import Collection, Iterator
from contextlib import contextmanager

from pydantic import JsonValue
from sqlalchemy.orm import Session

from transcribe_contracts.layout import manifest_key
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.payloads import PendingWithdrawal
from transcribe_ui.batches.phases import load_batch
from transcribe_ui.runs.activity import check_activity
from transcribe_ui.runs.cloud import open_cloud
from transcribe_ui.runs.errors import RunActiveError
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.errors import NothingToTranscribeError, UploadIncompleteError
from transcribe_ui.upload.manifest import build_manifest, manifest_body
from transcribe_ui.upload.steps import manifest_digest


@contextmanager
def withdrawal_guard(context: RequestContext, store: ObjectStore, batch_id: str) -> Iterator[str | None]:
    """Yield the digest of this database's leftover manifest in R2 (None when there is none).

    The caller holds the batch lock and is about to change an `uploading` batch. With a leftover
    manifest of ours, the block runs under the runs lock and only while no run is active.

    Raises:
        RunActiveError: A run is active (nothing was changed).
        ActivityUnknownError: The cloud could not be checked (fail closed; nothing was changed).
        RunsBusyError: The runs lock stayed held for the whole wait (nothing was changed).
    """
    state = context.ports.state
    with state.session() as session:
        expected = _expected_digest(session, batch_id)
    body = None if expected is None else store.read(manifest_key(batch_id))
    if body is None or manifest_digest(body) != expected:
        yield None
        return
    with state.locks.runs(context.cancel.is_cancelled), open_cloud(context) as cloud:
        activity = check_activity(state, cloud, context.ports.clock.now())
        if activity.active:
            raise RunActiveError(activity.sources)
        yield expected


def recorded_withdrawal(session: Session, batch_id: str, digest: str, item_ids: Collection[str]) -> JsonValue:
    """Return the batch's pending withdrawal with these items added, as stored (for `touch_batch`)."""
    stored = load_batch(session, batch_id).pending_withdrawal
    earlier = () if stored is None else PendingWithdrawal.model_validate(stored).item_ids
    affected = tuple(sorted({*earlier, *item_ids}))
    return PendingWithdrawal(manifest_sha256=digest, item_ids=affected).model_dump(mode="json")


def _expected_digest(session: Session, batch_id: str) -> str | None:
    """Return the digest of the manifest this database would have written, or None if it could write none."""
    stored = load_batch(session, batch_id).pending_withdrawal
    if stored is not None:
        return PendingWithdrawal.model_validate(stored).manifest_sha256
    try:
        manifest = build_manifest(session, batch_id)
    except (UploadIncompleteError, NothingToTranscribeError):
        return None
    return manifest_digest(manifest_body(manifest))
