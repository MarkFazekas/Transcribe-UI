"""The launch scope (plan m5 §1 "Run scope"): what the next worker run would process.

A run processes the batches its lease lists (A21); the launch lists every manifest under
`batch_metadata/` without `_done.json`, including manifests this database does not know (another
computer's, a deleted database's: `known` false, architecture §3.9 "Known batches"), read the way
the worker plans its run (`runs/manifests.py`). The fingerprint lets `launch_worker` check that
the scope it launches is the one the user confirmed.
"""

import hashlib
import json
from collections.abc import Iterable
from functools import partial

from transcribe_ui.runs.classes import LaunchScope, PendingJob, ScopeBatch
from transcribe_ui.runs.constants import FINGERPRINT_DURATION_DECIMALS
from transcribe_ui.runs.manifests import LocalBatches, published_batches, total_duration
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.leftover import is_known, withdrawing_digests


def read_launch_scope(state: LocalState, store: ObjectStore) -> LaunchScope:
    """Return the scope of the next run: the pending manifests in R2.

    The pending withdrawals are read first (one transaction), then R2 (outside any transaction;
    each manifest's recognition opens its own short transaction). A leftover a pending withdrawal
    will delete counts as absent, so no lease lists it.

    Raises:
        StorageError: R2 could not be read.
    """
    with state.session() as session:
        withdrawing = withdrawing_digests(session)
    published = published_batches(store, LocalBatches(partial(_known, state), withdrawing))
    batches = sorted(published.batches, key=_batch_id)
    durations = [job.duration_s for batch in batches for job in batch.pending_jobs]
    return LaunchScope(
        batches=tuple(batches),
        unreadable=tuple(published.unreadable),
        pending_jobs=len(durations),
        pending_duration_s=total_duration(durations),
        fingerprint=scope_fingerprint(batches),
    )


def scope_fingerprint(batches: Iterable[ScopeBatch]) -> str:
    """Return the SHA-256 (hex) of the covered batch ids, the pending jobs (key and mode) and the total duration."""
    covered = sorted(batches, key=_batch_id)
    jobs = [(batch.batch_id, job) for batch in covered for job in batch.pending_jobs]
    keys = sorted(_job_key(batch_id, job) for batch_id, job in jobs)
    total = total_duration(job.duration_s for _, job in jobs)
    document = {
        "batches": [batch.batch_id for batch in covered],
        "jobs": keys,
        "duration_s": f"{total:.{FINGERPRINT_DURATION_DECIMALS}f}",
    }
    canonical = json.dumps(document, sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def _job_key(batch_id: str, job: PendingJob) -> str:
    return f"{batch_id}/{job.item_id}/{job.language}/{job.mode}"


def _batch_id(batch: ScopeBatch) -> str:
    return batch.batch_id


def _known(state: LocalState, batch_id: str, body: bytes) -> bool:
    with state.session() as session:
        return is_known(session, batch_id, body)
