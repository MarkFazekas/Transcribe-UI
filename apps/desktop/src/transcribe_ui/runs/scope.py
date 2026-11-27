"""The launch scope (plan m5 §1 "Run scope"): what the next worker run would process.

The worker processes every manifest under `batch_metadata/` without `_done.json` (§5.3),
including manifests this database does not know (another computer, a deleted database), so the
scope is read from R2 the way the worker plans its run (`runs/manifests.py`). Batches whose
manifest is still held back locally are added (`launch_worker` publishes them before the run
starts). The fingerprint lets `launch_worker` check that the scope it launches is the one the
user confirmed.
"""

import hashlib
import json
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_contracts.classes import Manifest
from transcribe_ui.runs.classes import LaunchScope, PendingJob, ScopeBatch
from transcribe_ui.runs.constants import FINGERPRINT_DURATION_DECIMALS
from transcribe_ui.runs.manifests import published_batches, scope_batch, total_duration
from transcribe_ui.state.models import BatchRow
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.errors import NothingToTranscribeError, UploadIncompleteError
from transcribe_ui.upload.manifest import build_manifest, held_batch_ids


def read_launch_scope(state: LocalState, store: ObjectStore) -> LaunchScope:
    """Return the scope of the next run: the pending manifests in R2 plus the held ones.

    The database is read first (one transaction), then R2 (outside any transaction). A held
    batch whose manifest is in R2 already (an interrupted manifest step) is counted from R2 when
    it is pending or finished there; an unreadable or vanished copy counts the held version.

    Raises:
        StorageError: R2 could not be read.
    """
    known, held = _local_view(state)
    published = published_batches(store, known)
    waiting = [scope_batch(manifest, (), known=True, held=True) for manifest in held]
    in_r2 = {batch.batch_id for batch in published.batches} | published.finished_ids
    fresh = [batch for batch in waiting if batch.batch_id not in in_r2]
    batches = sorted((*published.batches, *fresh), key=_batch_id)
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


def _local_view(state: LocalState) -> tuple[frozenset[str], list[Manifest]]:
    """Return the ids of every batch this database knows and the manifests held back."""
    with state.session() as session:
        known = frozenset(session.scalars(select(BatchRow.batch_id)))
        built = [_held_manifest(session, batch_id) for batch_id in held_batch_ids(session)]
    return known, [manifest for manifest in built if manifest is not None]


def _held_manifest(session: Session, batch_id: str) -> Manifest | None:
    """Return a held batch's manifest, or None if its upload is not complete (it is not published then)."""
    try:
        return build_manifest(session, batch_id)
    except (UploadIncompleteError, NothingToTranscribeError):
        return None
