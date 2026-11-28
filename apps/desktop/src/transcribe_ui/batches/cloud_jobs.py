"""`list_cloud_batches`: every job in the cloud, this computer's or another's (architecture §3.9 "Download of another installation's job").

R2 alone (no Runpod key): every manifest under `batch_metadata/` that reads, with its id, whether
this database knows it (the known rule, `upload/leftover.py` `is_known`), its files, jobs and
audio length, and its state: `waiting` (no result yet), `transcribing` (some results, no
`_done.json`) or `transcribed` (`_done.json`), with the results done so far. Objects that do not
read as a manifest are left out (the launch plan lists them as unreadable).
"""

import math
from collections.abc import Iterator
from typing import Literal

from transcribe_contracts.classes import Manifest
from transcribe_contracts.constants import BATCH_METADATA_PREFIX, JSON_SUFFIX
from transcribe_contracts.errors import ContractError
from transcribe_contracts.layout import batch_id_from_manifest_key, done_key, parse_result_key, results_prefix
from transcribe_contracts.reading import read_manifest
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.classes import ListCloudBatchesRequest
from transcribe_ui.batches.cloud_responses import CloudBatch, ListCloudBatchesResponse
from transcribe_ui.runs.cloud import open_store
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.leftover import is_known

# Where a job stands in the cloud (`CloudBatch.state`).
type CloudBatchState = Literal["waiting", "transcribing", "transcribed"]


def list_cloud_batches(_request: ListCloudBatchesRequest, context: RequestContext) -> ListCloudBatchesResponse:
    """Return every readable manifest in R2 as a cloud job, by batch id.

    Raises:
        DomainError: The R2 credentials are missing or invalid, or R2 could not be read.
    """
    store = open_store(context)
    keys = sorted(found.key for found in store.list_objects(BATCH_METADATA_PREFIX))
    found = [_cloud_batch(context, store, key) for key in keys]
    return ListCloudBatchesResponse(batches=tuple(batch for batch in found if batch is not None))


def _cloud_batch(context: RequestContext, store: ObjectStore, key: str) -> CloudBatch | None:
    batch_id = batch_id_from_manifest_key(key)
    body = None if batch_id is None else store.read(key)
    if batch_id is None or body is None:
        return None
    try:
        manifest = read_manifest(key, body)
    except ContractError:
        return None
    with context.ports.state.session() as session:
        known = is_known(session, batch_id, body)
    finished = _finished_jobs(store, manifest)
    return CloudBatch(
        batch_id=batch_id,
        known=known,
        files=len(manifest.items),
        jobs=sum(len(entry.jobs) for entry in manifest.items),
        done_jobs=finished,
        audio_s=_audio_s(manifest),
        state=_state(store, batch_id, finished=finished),
    )


def _state(store: ObjectStore, batch_id: str, *, finished: int) -> CloudBatchState:
    if store.head(done_key(batch_id)) is not None:
        return "transcribed"
    return "transcribing" if finished else "waiting"


def _finished_jobs(store: ObjectStore, manifest: Manifest) -> int:
    """Return how many of the manifest's jobs have a result JSON."""
    wanted = set(_manifest_jobs(manifest))
    listed = store.list_objects(results_prefix(manifest.batch_id))
    done = {_job_of(found.key) for found in listed}
    done.discard(None)
    return len(wanted & done)


def _manifest_jobs(manifest: Manifest) -> Iterator[tuple[str, str]]:
    """Yield the (item id, language) of every job of the manifest."""
    for entry in manifest.items:
        for job in entry.jobs:
            yield entry.item_id, job.language


def _job_of(key: str) -> tuple[str, str] | None:
    """Return the (item id, language) of a result JSON key, None for any other key."""
    parts = parse_result_key(key)
    if parts is None or parts.suffix != JSON_SUFFIX:
        return None
    return parts.item_id, parts.language


def _audio_s(manifest: Manifest) -> float:
    """Return the audio length of the manifest's files (each audio object once)."""
    durations = [entry.audio[0].duration_s for entry in manifest.items]
    return math.fsum(sorted(durations))
