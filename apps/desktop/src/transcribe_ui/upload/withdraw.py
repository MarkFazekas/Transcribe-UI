"""Withdrawing a leftover manifest (architecture §3.3 "Withdrawing a leftover manifest").

An `uploading` batch has a manifest in R2 only when the manifest step was interrupted between
writing it and moving the batch to `ready`. Before exclusion or re-review changes such a batch,
`withdrawal_guard` checks whether the object at its manifest key is this database's
(`upload/leftover.py`); another computer's batch with the same id is never touched. If it is
ours, the guard takes the runs lock (bounded wait: the caller holds the batch lock) and lets the
change go ahead only while no live run lease lists the batch (`runs/leases.py`; R2 alone, so it
works on an installation without the Runpod key). The change records a `PendingWithdrawal` in its own
transaction, then `upload/steps.py` `finish_after_change` carries it out (`finish_withdrawal`:
the affected items' results, `_done.json`, the manifest last, then the record is cleared). If
that fails, the request still succeeds (`withdrawal_pending`) and the withdrawal is finished,
always under the runs lock while no live lease lists the batch, by whichever comes first: the
next exclusion or re-review, the start of `run_upload`, the manifest step or the extract-only
transition. A launch never lists the leftover meanwhile: its scope counts a manifest whose
digest a pending withdrawal recorded as absent. Results of the other items stay, so the worker
redoes only the jobs whose result is gone.
"""

from collections.abc import Collection, Iterator
from contextlib import contextmanager

from pydantic import JsonValue
from sqlalchemy.orm import Session

from transcribe_contracts.layout import manifest_key
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.payloads import PendingWithdrawal
from transcribe_ui.batches.phases import load_batch
from transcribe_ui.runs.leases import refuse_if_leased
from transcribe_ui.state.errors import LockOrderError
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.leftover import manifest_digest, own_manifest


@contextmanager
def withdrawal_guard(context: RequestContext, store: ObjectStore, batch_id: str) -> Iterator[str | None]:
    """Yield the digest of this database's leftover manifest in R2 (None when there is none).

    The caller holds the batch lock and is about to change an `uploading` batch. With a leftover
    manifest of ours, the block runs under the runs lock and only while no live lease lists the batch.

    Raises:
        RunActiveError: A live lease lists the batch (nothing was changed).
        StorageError: R2 could not be read (fail closed; nothing was changed).
        RunsBusyError: The runs lock stayed held for the whole wait (nothing was changed).
    """
    state = context.ports.state
    if state.locks.holds_runs():
        msg = "The withdrawal guard takes the runs lock itself; it must not be held already."
        raise LockOrderError(msg)
    with state.session() as session:
        own = own_manifest(session, batch_id)
    body = store.read(manifest_key(batch_id))
    if body is None or not own.owns(body):
        yield None
        return
    with state.locks.runs(context.cancel.is_cancelled):
        refuse_if_leased(store, batch_id, context.ports.clock.now())
        yield manifest_digest(body)


def recorded_withdrawal(session: Session, batch_id: str, digest: str, item_ids: Collection[str]) -> JsonValue:
    """Return the batch's pending withdrawal with these items added, as stored (for `touch_batch`)."""
    stored = load_batch(session, batch_id).pending_withdrawal
    earlier = () if stored is None else PendingWithdrawal.model_validate(stored).item_ids
    affected = tuple(sorted({*earlier, *item_ids}))
    return PendingWithdrawal(manifest_sha256=digest, item_ids=affected).model_dump(mode="json")
