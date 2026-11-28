"""The steps of publishing a manifest: finish a pending withdrawal, move the batch to `ready` (`upload/publish.py`).

A withdrawal runs under the batch lock and the runs lock, while no live lease lists the batch.
"""

import logging

from transcribe_contracts.layout import deleting_key, done_key, manifest_key, parse_result_key, results_prefix
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.constants import UPLOADING_PHASES
from transcribe_ui.batches.payloads import PendingWithdrawal
from transcribe_ui.batches.phases import load_batch, touch_batch
from transcribe_ui.state.database import compare_and_set_phase
from transcribe_ui.state.errors import LockOrderError
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.leftover import own_manifest

logger = logging.getLogger(__name__)


def finish_withdrawal(context: RequestContext, store: ObjectStore, batch_id: str) -> None:
    """Carry out the batch's pending withdrawal, if any; the caller holds both locks and no live lease lists the batch.

    Raises:
        StorageError: R2 failed (the withdrawal stays pending; calling again resumes it).
        LockOrderError: The calling thread does not hold the runs lock (a programming error).
    """
    state = context.ports.state
    if not state.locks.holds_runs():
        msg = "A withdrawal needs the runs lock (no worker may read the batch meanwhile)."
        raise LockOrderError(msg)
    with state.session() as session:
        stored = load_batch(session, batch_id).pending_withdrawal
        own = own_manifest(session, batch_id)
    if stored is None:
        return
    pending = PendingWithdrawal.model_validate(stored)
    # First: another computer's interrupted cleanup must not take a manifest gone midway for its
    # point of no return and purge the prefix this batch reclaims.
    store.delete(deleting_key(batch_id))
    body = store.read(manifest_key(batch_id))
    if body is not None and own.owns(body):
        _delete_results(store, batch_id, frozenset(pending.item_ids))
        store.delete(done_key(batch_id))
        store.delete(manifest_key(batch_id))
    with state.session() as session:
        now = context.ports.clock.now()
        touch_batch(session, batch_id, UPLOADING_PHASES, now=now, also_set={"pending_withdrawal": None})


def move_to_ready(context: RequestContext, batch_id: str) -> None:
    """Move the batch `uploading` → `ready` by compare-and-set.

    Raises:
        PhaseConflictError: The batch is no longer `uploading`.
    """
    with context.ports.state.session() as session:
        now = context.ports.clock.now()
        compare_and_set_phase(session, batch_id, "uploading", "ready", now=now)


def _delete_results(store: ObjectStore, batch_id: str, item_ids: frozenset[str]) -> None:
    for found in store.list_objects(results_prefix(batch_id)):
        parts = parse_result_key(found.key)
        if parts is not None and parts.item_id in item_ids:
            store.delete(found.key)


def finish_after_change(context: RequestContext, store: ObjectStore, batch_id: str) -> bool:
    """Carry out the withdrawal the change just recorded; return whether it is still pending.

    The change is committed, so an R2 failure here does not fail the request: it is logged and
    the withdrawal stays pending for the next exclusion, re-review, `run_upload` or manifest step.
    """
    try:
        finish_withdrawal(context, store, batch_id)
    except StorageError as error:
        logger.warning("Withdrawing the leftover manifest of %s failed (%s); it stays pending", batch_id, error.code)
        return True
    return False
