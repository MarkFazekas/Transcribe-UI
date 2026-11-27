"""Seeds and helpers of the retry and cleanup tests (plan m5 M5.14).

The source batch is seeded through the state store as a finished run leaves it: its audio, its
manifest, its job results (the film `failed`, the podcast `ok`) and `_done.json` in the bucket,
and the batch `transcribed` with the job flags recorded.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path

import pytest
from sqlalchemy import select

from support.batches import FIRST_BATCH_ID, SEEDED_OBJECT_BYTES, seed_uploading_batch, seeded_keys
from support.fakes import FIXED_NOW
from support.interrupt import StoreWrap
from support.monitor import write_done, write_result
from support.runs import RUN_ID, RunsWorld
from transcribe_contracts.classes import JobStatus as ResultStatus
from transcribe_contracts.layout import batch_prefix, manifest_key
from transcribe_ui.api.classes import Response
from transcribe_ui.api.ports import CancelFlag, RequestContext
from transcribe_ui.batches.payloads import BatchPayload, JobPayload
from transcribe_ui.runs.cloud import CloudAccess, open_cloud
from transcribe_ui.state.models import BatchRow, JobRow
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.r2 import R2ObjectStore
from transcribe_ui.upload.manifest import build_manifest, write_manifest

# Item ids of the seeded source batch (`seed_uploading_batch`).
FILM_ID = "01JDZZZZZZZZZZZZZZZZZZZZ01"
PODCAST_ID = "01JDZZZZZZZZZZZZZZZZZZZZ02"
# Why the film's job failed (the simulated worker's error).
FILM_ERROR = "decoding failed"
# Request actions.
RETRY = "create_retry_batch"
CLEANUP = "cleanup_cloud"
# Cleanup scopes.
AUDIO = "audio"
EVERYTHING = "everything"


def seed_transcribed(
    state: LocalState,
    root: Path,
    store: R2ObjectStore,
    phase: str = "transcribed",
    podcast: ResultStatus = "ok",
) -> None:
    """Seed the source batch finished by a run: the film failed, the podcast `ok` (or `podcast`), `_done.json`."""
    seed_uploading_batch(state, root, manifest_held=False)
    for key in seeded_keys(state):
        store.write(key, b"a" * SEEDED_OBJECT_BYTES, "audio/x-matroska")
    with state.session() as session:
        write_manifest(store, build_manifest(session, FIRST_BATCH_ID))
    write_result(store, FIRST_BATCH_ID, FILM_ID, RUN_ID, FIXED_NOW, status="failed")
    write_result(store, FIRST_BATCH_ID, PODCAST_ID, RUN_ID, FIXED_NOW, status=podcast)
    write_done(store, FIRST_BATCH_ID, RUN_ID, FIXED_NOW)
    with state.session() as session:
        batch = session.get(BatchRow, FIRST_BATCH_ID)
        assert batch is not None
        batch.phase = phase
        for job in session.scalars(select(JobRow).where(JobRow.batch_id == FIRST_BATCH_ID)):
            failed = job.item_id == FILM_ID
            job.status = "failed" if failed else podcast
            reason = FILM_ERROR if failed else None
            stored = JobPayload.model_validate(job.payload).model_copy(update={"result_error": reason})
            job.payload = stored.model_dump(mode="json")


def seed_world(world: RunsWorld, phase: str = "transcribed") -> None:
    """Seed the source batch into a world."""
    seed_transcribed(world.ports.state, world.leg.root, world.store, phase)


def retry_document(*item_ids: str, batch_id: str = FIRST_BATCH_ID) -> str:
    """Return a `create_retry_batch` request for the English jobs of the items (the film by default)."""
    jobs = [{"item_id": item_id, "language": "en"} for item_id in item_ids or (FILM_ID,)]
    return json.dumps({"action": RETRY, "batch_id": batch_id, "jobs": jobs})


def cleanup_document(scope: str, batch_id: str = FIRST_BATCH_ID, **fields: object) -> str:
    """Return a `cleanup_cloud` request."""
    return json.dumps({"action": CLEANUP, "batch_id": batch_id, "scope": scope, **fields})


def confirmed(scope: str, batch_id: str = FIRST_BATCH_ID) -> str:
    """Return a `cleanup_cloud` request with every confirmation set."""
    return cleanup_document(scope, batch_id, confirm=True, confirm_not_downloaded=True)


def send(world: RunsWorld, document: str, cancel: CancelFlag | None = None) -> Response:
    """Send a request document to the world's backend."""
    return world.leg.backend.execute_json(document, cancel=cancel)


def keys_under(store: R2ObjectStore, batch_id: str = FIRST_BATCH_ID) -> list[str]:
    """Return every key of the batch: its manifest (if any) and the objects under its prefix."""
    found = [manifest_key(batch_id)]
    if store.head(found[0]) is None:
        found.clear()
    found.extend(listed.key for listed in store.list_objects(batch_prefix(batch_id)))
    return found


def batch_ids(world: RunsWorld) -> list[str]:
    """Return every batch id of the database."""
    with world.ports.state.session() as session:
        return list(session.scalars(select(BatchRow.batch_id).order_by(BatchRow.batch_id)))


def flags(world: RunsWorld, batch_id: str = FIRST_BATCH_ID) -> tuple[str, bool]:
    """Return the batch's phase and `audio_deleted`."""
    with world.ports.state.session() as session:
        row = session.get(BatchRow, batch_id)
        assert row is not None
        return row.phase, row.audio_deleted


def patch_store(monkeypatch: pytest.MonkeyPatch, module: str, wrap: StoreWrap) -> None:
    """Make the handlers of `transcribe_ui.cleanup.<module>` use a wrapped object store."""
    monkeypatch.setattr(f"transcribe_ui.cleanup.{module}.open_cloud", partial(_wrapped_cloud, wrap))


@contextmanager
def _wrapped_cloud(wrap: StoreWrap, context: RequestContext) -> Iterator[CloudAccess]:
    with open_cloud(context) as cloud:
        yield CloudAccess(wrap(cloud.store), cloud.pods)


def cleanup_pending(world: RunsWorld, batch_id: str = FIRST_BATCH_ID) -> str | None:
    """Return the scope of the batch's interrupted cleanup (None: none)."""
    with world.ports.state.session() as session:
        row = session.get(BatchRow, batch_id)
        assert row is not None
        return BatchPayload.model_validate(row.payload).cleanup_pending
