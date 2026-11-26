"""Discovery and ordering: which jobs this run does, and in which order (architecture §5.3)."""

import logging
from dataclasses import dataclass, replace
from typing import Literal

from transcribe_contracts import layout
from transcribe_contracts.classes import AudioObject, Manifest, ManifestItem, TranscribeJob
from transcribe_contracts.constants import BATCH_METADATA_PREFIX
from transcribe_contracts.errors import ContractError
from transcribe_contracts.reading import read_manifest
from transcribe_worker.pipeline.backup import Backup
from transcribe_worker.pipeline.storage import ObjectStore

logger = logging.getLogger(__name__)

type JobSource = Literal["transcribe", "backup"]


@dataclass(frozen=True)
class PlannedJob:
    """One pending job: transcribe it, or upload its result from the backup."""

    batch_id: str
    manifest_item: ManifestItem
    job: TranscribeJob
    audio: AudioObject
    source: JobSource

    @property
    def json_key(self) -> str:
        """Return the key of the job's result JSON (its "done" marker)."""
        return layout.result_json_key(self.batch_id, self.manifest_item.item_id, self.job.language)

    @property
    def srt_key(self) -> str:
        """Return the key of the job's subtitle."""
        return layout.result_srt_key(self.batch_id, self.manifest_item.item_id, self.job.language)

    @property
    def sort_key(self) -> tuple[str, str, str]:
        """Order of processing: language first, so each alignment model is loaded once."""
        return (self.job.language, self.batch_id, self.manifest_item.item_id)


@dataclass(frozen=True)
class RunPlan:
    """Pending jobs in processing order, and the batches this run must finish."""

    jobs: tuple[PlannedJob, ...]
    batches: tuple[str, ...]

    def pending_per_batch(self) -> dict[str, int]:
        """Return the number of pending jobs of every batch (0 for one only missing `_done`)."""
        counts = dict.fromkeys(self.batches, 0)
        for planned in self.jobs:
            counts[planned.batch_id] += 1
        return counts


def plan_run(store: ObjectStore, backup: Backup) -> RunPlan:
    """List the ready batches that are not done and their jobs without a result in R2.

    Unreadable manifests (invalid, or another schema major version) are logged and skipped.
    """
    jobs: list[PlannedJob] = []
    batches: list[str] = []
    for key in sorted(store.list_keys(BATCH_METADATA_PREFIX)):
        manifest = _load_pending_manifest(store, key)
        if manifest is None:
            continue
        batches.append(manifest.batch_id)
        done = set(store.list_keys(layout.results_prefix(manifest.batch_id)))
        jobs.extend(_pending_jobs(manifest, done, backup))
    jobs.sort(key=lambda planned: planned.sort_key)
    logger.info("plan: %d pending jobs in %d batches", len(jobs), len(batches))
    return RunPlan(jobs=tuple(jobs), batches=tuple(batches))


def _load_pending_manifest(store: ObjectStore, key: str) -> Manifest | None:
    batch_id = layout.batch_id_from_manifest_key(key)
    if batch_id is None:
        logger.warning("ignoring %s: not a manifest key", key)
        return None
    if store.read(layout.done_key(batch_id)) is not None:
        return None
    body = store.read(key)
    if body is None:
        return None
    try:
        return read_manifest(key, body)
    except ContractError:
        logger.exception("skipping batch %s: unreadable manifest", batch_id)
        return None


def _pending_jobs(manifest: Manifest, done: set[str], backup: Backup) -> list[PlannedJob]:
    pending: list[PlannedJob] = []
    for manifest_item in manifest.items:
        audio_by_key = {audio.object_key: audio for audio in manifest_item.audio}
        for job in manifest_item.jobs:
            planned = PlannedJob(
                batch_id=manifest.batch_id,
                manifest_item=manifest_item,
                job=job,
                audio=audio_by_key[job.audio_object_key],
                source="transcribe",
            )
            if planned.json_key in done:
                continue
            if backup.load(planned.json_key) is not None:
                planned = replace(planned, source="backup")
            pending.append(planned)
    return pending
