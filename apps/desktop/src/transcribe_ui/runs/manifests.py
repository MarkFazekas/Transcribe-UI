"""The manifests in R2 as the worker sees them (§5.3), for the launch scope.

A key under `batch_metadata/` that is not a manifest key, a manifest of another schema major
version or an invalid one is unreadable: the worker skips it, so it is listed, not counted. A
batch with `_done.json` is finished and skipped; a job is pending while its result JSON is
missing.
"""

import math
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import NamedTuple

from transcribe_contracts.classes import Manifest, ManifestItem, TranscribeJob
from transcribe_contracts.constants import BATCH_METADATA_PREFIX
from transcribe_contracts.errors import ContractError, UnsupportedSchemaError
from transcribe_contracts.layout import batch_id_from_manifest_key, done_key, result_json_key, results_prefix
from transcribe_contracts.reading import read_manifest
from transcribe_ui.runs.classes import PendingJob, ScopeBatch, UnreadableManifest
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.leftover import manifest_digest


class FinishedBatch(NamedTuple):
    """A manifest whose batch has `_done.json`: not in the scope."""

    batch_id: str


# What one object under `batch_metadata/` adds to the scope; None for one deleted since the listing.
type ScopeEntry = ScopeBatch | UnreadableManifest | FinishedBatch | None


class LocalBatches(NamedTuple):
    """What this database says about the batches in R2."""

    # Whether a manifest (batch id, bytes) is this database's batch (`upload/leftover.py` `is_known`).
    known: Callable[[str, bytes], bool]
    # Batch id -> digest of its leftover manifest that a pending withdrawal will delete.
    withdrawing: Mapping[str, str]


class PublishedView(NamedTuple):
    """What `batch_metadata/` holds, as the launch scope needs it."""

    # The pending batches, by batch id.
    batches: list[ScopeBatch]
    # The objects the worker skips, by key.
    unreadable: list[UnreadableManifest]
    # The batches whose manifest has `_done.json`.
    finished_ids: frozenset[str]


def published_batches(store: ObjectStore, local: LocalBatches) -> PublishedView:
    """Return the pending batches in R2, the unreadable objects under `batch_metadata/` and the finished batches.

    A leftover manifest a pending withdrawal will delete (same digest) counts as absent, and so
    does one deleted since the listing.

    Raises:
        StorageError: R2 could not be listed or read.
    """
    keys = [found.key for found in store.list_objects(BATCH_METADATA_PREFIX)]
    entries = [_published_entry(store, key, local) for key in keys]
    batches = [entry for entry in entries if isinstance(entry, ScopeBatch)]
    unreadable = [entry for entry in entries if isinstance(entry, UnreadableManifest)]
    finished = frozenset(entry.batch_id for entry in entries if isinstance(entry, FinishedBatch))
    return PublishedView(batches, unreadable, finished)


def scope_batch(manifest: Manifest, finished: Collection[str], *, known: bool) -> ScopeBatch:
    """Return the manifest's batch with its jobs whose result JSON is not among `finished` (keys)."""
    done = frozenset(finished)
    pending: list[PendingJob] = []
    for entry in manifest.items:
        pending.extend(_pending_jobs(manifest.batch_id, entry, done))
    return ScopeBatch(
        batch_id=manifest.batch_id,
        known=known,
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


def _published_entry(store: ObjectStore, key: str, local: LocalBatches) -> ScopeEntry:
    batch_id = batch_id_from_manifest_key(key)
    if batch_id is None:
        return UnreadableManifest(key=key, reason="not_a_manifest")
    body = store.read(key)
    if body is None or _being_withdrawn(body, local.withdrawing.get(batch_id)):
        return None
    if store.head(done_key(batch_id)) is not None:
        return FinishedBatch(batch_id)
    manifest = _read_manifest(key, body)
    if not isinstance(manifest, Manifest):
        return manifest
    finished = [found.key for found in store.list_objects(results_prefix(batch_id))]
    return scope_batch(manifest, finished, known=local.known(batch_id, body))


def _being_withdrawn(body: bytes, digest: str | None) -> bool:
    return digest is not None and manifest_digest(body) == digest


def _read_manifest(key: str, body: bytes) -> Manifest | UnreadableManifest:
    """Return the manifest, or why it is unreadable."""
    try:
        return read_manifest(key, body)
    except UnsupportedSchemaError:
        return UnreadableManifest(key=key, reason="unsupported_schema")
    except ContractError:
        return UnreadableManifest(key=key, reason="invalid")
