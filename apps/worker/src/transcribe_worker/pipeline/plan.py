"""Discovery and ordering: which jobs this run does, and in which order (architecture §5.3).

The run processes only the batches its lease (`runs/<run_id>/launch.json`, written by the
launching desktop) lists (A21, architecture §3.9 "Run scope"): whatever another computer
publishes later is never processed by it, nor its `_done.json` written or its backups restored.
Without a readable lease the run processes nothing and fails. A listed batch whose manifest is
gone (cleaned up between the launch and the start) is skipped.
"""

import logging
from dataclasses import dataclass, replace
from typing import Literal

from transcribe_contracts import layout
from transcribe_contracts.classes import AudioObject, JobRef, Manifest, ManifestItem, TranscribeJob
from transcribe_contracts.errors import ContractError
from transcribe_contracts.reading import read_job_result, read_manifest, read_run_lease
from transcribe_worker.pipeline.backup import Backup
from transcribe_worker.pipeline.storage import ObjectStore

logger = logging.getLogger(__name__)

type JobSource = Literal["transcribe", "backup"]


class RunLeaseError(Exception):
    """The run's lease is missing or unreadable: the run processes nothing."""


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
    def ref(self) -> JobRef:
        """Return the reference shown in the run status."""
        return JobRef(
            batch_id=self.batch_id,
            item_id=self.manifest_item.item_id,
            language=self.job.language,
        )

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


def plan_run(store: ObjectStore, backup: Backup, run_id: str) -> RunPlan:
    """List the lease's batches that are not done and their jobs without a result in R2.

    Unreadable manifests (invalid, or another schema major version) are logged and skipped, and
    so are listed batches without a manifest.

    Raises:
        RunLeaseError: The run's lease is missing or unreadable.
    """
    jobs: list[PlannedJob] = []
    batches: list[str] = []
    for batch_id in sorted(_leased_batches(store, run_id)):
        manifest = _load_pending_manifest(store, batch_id)
        if manifest is None:
            continue
        batches.append(manifest.batch_id)
        done = set(store.list_keys(layout.results_prefix(manifest.batch_id)))
        jobs.extend(_pending_jobs(manifest, done, backup))
    jobs.sort(key=lambda planned: planned.sort_key)
    logger.info("plan: %d pending jobs in %d batches", len(jobs), len(batches))
    return RunPlan(jobs=tuple(jobs), batches=tuple(batches))


def _leased_batches(store: ObjectStore, run_id: str) -> tuple[str, ...]:
    """Return the batches the run's lease lists."""
    key = layout.run_lease_key(run_id)
    body = store.read(key)
    if body is None:
        msg = f"the run has no lease ({key}): nothing to process"
        raise RunLeaseError(msg)
    try:
        lease = read_run_lease(key, body)
    except ContractError as error:
        msg = f"the run's lease ({key}) is unreadable: {error}"
        raise RunLeaseError(msg) from error
    logger.info("lease: %d batches", len(lease.batch_ids))
    return lease.batch_ids


def _load_pending_manifest(store: ObjectStore, batch_id: str) -> Manifest | None:
    key = layout.manifest_key(batch_id)
    if store.read(layout.done_key(batch_id)) is not None:
        return None
    body = store.read(key)
    if body is None:
        logger.warning("skipping batch %s: listed by the lease, but its manifest is gone", batch_id)
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
            if _backed_up(planned, backup):
                planned = replace(planned, source="backup")
            pending.append(planned)
    return pending


def _backed_up(planned: PlannedJob, backup: Backup) -> bool:
    """Return True if the backup holds the job's subtitle and a valid result JSON."""
    body = backup.load(planned.json_key)
    if body is None or backup.load(planned.srt_key) is None:
        return False
    try:
        read_job_result(planned.json_key, body)
    except ContractError:
        logger.warning("removing the invalid backup of %s", planned.json_key)
        backup.remove(planned.json_key)
        return False
    return True
