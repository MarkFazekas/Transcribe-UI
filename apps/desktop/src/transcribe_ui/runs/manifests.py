"""The manifests in R2 as the worker sees them (§5.3), for the launch scope.

A key under `batch_metadata/` that is not a manifest key, a manifest of another schema major
version or an invalid one is unreadable: the worker skips it, so it is listed, not counted. A
batch with `_done.json` is finished and skipped; a job is pending while its result JSON is
missing.
"""

import math
from collections.abc import Collection, Iterable

from transcribe_contracts.classes import Manifest, ManifestItem, TranscribeJob
from transcribe_contracts.constants import BATCH_METADATA_PREFIX
from transcribe_contracts.errors import ContractError, UnsupportedSchemaError
from transcribe_contracts.layout import batch_id_from_manifest_key, done_key, result_json_key, results_prefix
from transcribe_contracts.reading import read_manifest
from transcribe_ui.runs.classes import PendingJob, ScopeBatch, UnreadableManifest
from transcribe_ui.storage.r2 import ObjectStore

# What one object under `batch_metadata/` adds to the scope; None for a finished batch.
type ScopeEntry = ScopeBatch | UnreadableManifest | None


# The pending batches in R2 and the unreadable objects under `batch_metadata/`.
type PublishedView = tuple[list[ScopeBatch], list[UnreadableManifest]]


def published_batches(store: ObjectStore, known: Collection[str]) -> PublishedView:
    """Return the pending batches in R2 and the unreadable objects under `batch_metadata/` (by key).

    Raises:
        StorageError: R2 could not be listed or read.
    """
    batches: list[ScopeBatch] = []
    unreadable: list[UnreadableManifest] = []
    for listed in store.list_objects(BATCH_METADATA_PREFIX):
        entry = _published_entry(store, listed.key, known)
        if isinstance(entry, ScopeBatch):
            batches.append(entry)
        elif entry is not None:
            unreadable.append(entry)
    return batches, unreadable


def scope_batch(manifest: Manifest, finished: Collection[str], *, known: bool, held: bool) -> ScopeBatch:
    """Return the manifest's batch with its jobs whose result JSON is not among `finished` (keys)."""
    done = frozenset(finished)
    pending: list[PendingJob] = []
    for entry in manifest.items:
        pending.extend(_pending_jobs(manifest.batch_id, entry, done))
    return ScopeBatch(
        batch_id=manifest.batch_id,
        known=known,
        held=held,
        files=len(manifest.items),
        pending_jobs=tuple(pending),
        pending_duration_s=total_duration(job.duration_s for job in pending),
    )


def total_duration(durations: Iterable[float]) -> float:
    """Return the exact sum of the durations, independent of their order."""
    return math.fsum(sorted(durations))


def _pending_jobs(batch_id: str, entry: ManifestItem, done: frozenset[str]) -> list[PendingJob]:
    durations = {audio.object_key: audio.duration_s for audio in entry.audio}
    pending: list[PendingJob] = []
    for job in entry.jobs:
        if result_json_key(batch_id, entry.item_id, job.language) not in done:
            duration = durations[job.audio_object_key]
            pending.append(_pending_job(entry.item_id, job, duration))
    return pending


def _pending_job(item_id: str, job: TranscribeJob, duration_s: float) -> PendingJob:
    return PendingJob(item_id=item_id, language=job.language, mode=job.mode, duration_s=duration_s)


def _published_entry(store: ObjectStore, key: str, known: Collection[str]) -> ScopeEntry:
    batch_id = batch_id_from_manifest_key(key)
    if batch_id is None:
        return UnreadableManifest(key=key, reason="not_a_manifest")
    if store.head(done_key(batch_id)) is not None:
        return None
    manifest = _read_manifest(store, key)
    if not isinstance(manifest, Manifest):
        return manifest
    finished = [found.key for found in store.list_objects(results_prefix(batch_id))]
    return scope_batch(manifest, finished, known=batch_id in known, held=False)


def _read_manifest(store: ObjectStore, key: str) -> Manifest | UnreadableManifest | None:
    """Return the manifest, why it is unreadable, or None if it was deleted since the listing."""
    body = store.read(key)
    if body is None:
        return None
    try:
        return read_manifest(key, body)
    except UnsupportedSchemaError:
        return UnreadableManifest(key=key, reason="unsupported_schema")
    except ContractError:
        return UnreadableManifest(key=key, reason="invalid")
