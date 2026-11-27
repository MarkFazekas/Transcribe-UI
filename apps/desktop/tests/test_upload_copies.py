"""The copy variant of the upload (retry batches, M5.14) and ambiguous R2 outcomes (plan m5 M5.10)."""

import contextlib
import itertools
from collections.abc import Iterator
from pathlib import Path
from typing import override

import pytest

from support.batch_backend import error_code
from support.batches import FILM, FIRST_BATCH_ID, SECOND_BATCH_ID, batch_request, seeded_keys
from support.buckets import create_bucket
from support.credentials import TEST_BUCKET, moto_bucket
from support.fakes import FIXED_NOW
from support.interrupt import Crash, TripStore, Tripwire, patch_upload_store
from support.runs import RunsWorld, seed_uploaded
from support.storage import store_client
from support.upload import bucket_state, outcome_of, recorded_keys, review, upload, upload_world
from transcribe_contracts.layout import audio_key, manifest_key
from transcribe_contracts.reading import read_manifest
from transcribe_ui.api.classes import Response
from transcribe_ui.api.ports import CancelToken
from transcribe_ui.batches.payloads import BatchPayload, ItemPayload, JobPayload
from transcribe_ui.state.models import BatchRow, ItemRow, JobRow
from transcribe_ui.storage.constants import ERROR_R2_FAILED
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.multipart import UploadProgress
from transcribe_ui.storage.r2 import ObjectInfo, R2ObjectStore
from transcribe_ui.upload.payloads import AudioObjectPayload, CopySource

# The retry item (a new item of the retry batch).
RETRY_ITEM = "01JDZZZZZZZZZZZZZZZZZZZZ03"
# The audio track of the film the seeded batch transcribes.
FILM_TRACK = 1
# Duration and size of the seeded film audio (`support/batches.py`).
FILM_SECONDS_SEEDED = 5400
SEEDED_BYTES = 16
# A copy path has at least these external calls (check, sweep, copy, verify, manifest).
MIN_COPY_CALLS = 5
# The retry upload request.
RETRY_UPLOAD = batch_request("run_upload", SECOND_BATCH_ID)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunsWorld]:
    """A backend on the upload tree over moto and the fake Runpod API (fixed clock)."""
    with moto_bucket(), upload_world(tmp_path / "leg") as created:
        yield created


def _seed_retry(world: RunsWorld) -> str:
    """Seed the uploaded source batch and a retry batch copying its film audio; return the source key."""
    ids = seed_uploaded(world)
    source_key = next(key for key in seeded_keys(world.ports.state) if ids[FILM] in key)
    with world.ports.state.session() as session:
        source = session.get(BatchRow, FIRST_BATCH_ID)
        film = session.get(ItemRow, ids[FILM])
        assert source is not None
        assert film is not None
        film_payload = ItemPayload.model_validate(film.payload)
        audio = AudioObjectPayload(
            track_index=FILM_TRACK,
            extension="mka",
            codec="opus",
            duration_s=FILM_SECONDS_SEEDED,
            size_bytes=SEEDED_BYTES,
        )
        # Another size than the file's: a copy never reads the file, so no source check may refuse it.
        copied = film_payload.model_copy(
            update={"copy_sources": (CopySource(source_key=source_key, audio=audio),), "size_bytes": 1}
        )
        session.add(
            BatchRow(
                batch_id=SECOND_BATCH_ID,
                phase="uploading",
                created_at=FIXED_NOW,
                updated_at=FIXED_NOW,
                payload=BatchPayload.model_validate(source.payload).model_dump(mode="json"),
            ),
        )
        session.flush()
        session.add(
            ItemRow(
                item_id=RETRY_ITEM, batch_id=SECOND_BATCH_ID, phase="reviewed", payload=copied.model_dump(mode="json")
            )
        )
        session.flush()
        session.add(
            JobRow(
                item_id=RETRY_ITEM,
                batch_id=SECOND_BATCH_ID,
                language="en",
                kind="transcribe",
                status="pending",
                payload=JobPayload(mode="careful", track_index=FILM_TRACK).model_dump(mode="json"),
            ),
        )
    return source_key


def _retry(world: RunsWorld) -> Response:
    return world.leg.backend.execute_json(RETRY_UPLOAD)


def test_retry_item_is_copied_server_side(world: RunsWorld) -> None:
    """A retry item's audio is copied from the source batch, verified, and published with mode `careful`."""
    _seed_retry(world)
    assert outcome_of(_retry(world)) == "ready"
    target = audio_key(SECOND_BATCH_ID, RETRY_ITEM, "mka", FILM_TRACK)
    listed, body = bucket_state(world.store, SECOND_BATCH_ID)
    assert listed == ((target, SEEDED_BYTES),)
    assert body is not None
    manifest = read_manifest(manifest_key(SECOND_BATCH_ID), body)
    assert [(job.mode, job.audio_object_key) for entry in manifest.items for job in entry.jobs] == [("careful", target)]


def _killed_then_resumed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wire: Tripwire) -> bool:
    """Seed a retry batch on a fresh bucket, kill its upload at the wire, resume; return whether the wire fired."""
    bucket = f"retry-{wire.at}-{int(wire.after)}"
    create_bucket(bucket)
    with upload_world(tmp_path / bucket, bucket) as world:
        _seed_retry(world)
        patch_upload_store(monkeypatch, lambda store: TripStore(store, wire))
        with contextlib.suppress(Crash):
            _retry(world)
        patch_upload_store(monkeypatch, lambda store: store)
        if not wire.fired:
            return False
        assert outcome_of(_retry(world)) == "ready", wire
        target = audio_key(SECOND_BATCH_ID, RETRY_ITEM, "mka", FILM_TRACK)
        assert bucket_state(world.store, SECOND_BATCH_ID)[0] == ((target, SEEDED_BYTES),), wire
    return True


@pytest.mark.parametrize("after", [False, True])
def test_retry_interrupted_at_every_call_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, after: bool
) -> None:
    """A kill before or after any call of the copy path: re-sending reaches the uninterrupted state."""
    fired = 0
    with moto_bucket():
        for at in itertools.count(1):
            if not _killed_then_resumed(tmp_path, monkeypatch, Tripwire(at=at, after=after)):
                break
            fired += 1
    assert fired > MIN_COPY_CALLS


def test_retry_with_its_source_gone_is_refused(world: RunsWorld) -> None:
    """The source object is gone: `r2_object_missing`, nothing is recorded for the retry item."""
    source_key = _seed_retry(world)
    world.store.delete(source_key)
    assert error_code(_retry(world)) == "r2_object_missing"
    assert [key for key in recorded_keys(world) if RETRY_ITEM in key] == []


class _LostResponse(R2ObjectStore):
    """The test bucket, but an upload fails after writing (a lost response) or before writing."""

    def __init__(self, *, written: bool) -> None:
        """Write the object before failing, or not."""
        super().__init__(store_client(), TEST_BUCKET)
        self.written = written

    @override
    def upload_file(
        self,
        key: str,
        source: Path,
        content_type: str,
        progress: UploadProgress,
        cancel: CancelToken,
    ) -> int:
        if self.written:
            super().upload_file(key, source, content_type, progress, cancel)
        raise StorageError(ERROR_R2_FAILED, "upload", "Injected")


def test_lost_upload_response_taken_if_object_there(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """An upload that fails after writing (the response lost) counts: HEAD finds the object with its size."""
    review(world)
    patch_upload_store(monkeypatch, lambda _store: _LostResponse(written=True))
    assert outcome_of(upload(world)) == "ready"


def test_failed_upload_fails_then_resumes(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """An upload that wrote nothing fails the request; re-sending uploads again."""
    review(world)
    patch_upload_store(monkeypatch, lambda _store: _LostResponse(written=False))
    assert error_code(upload(world)) == ERROR_R2_FAILED
    patch_upload_store(monkeypatch, lambda store: store)
    assert outcome_of(upload(world)) == "ready"


class _WrongSize(R2ObjectStore):
    """The test bucket, but HEAD reports one byte more than stored."""

    def __init__(self) -> None:
        """Use the test bucket."""
        super().__init__(store_client(), TEST_BUCKET)

    @override
    def head(self, key: str) -> ObjectInfo | None:
        found = super().head(key)
        return None if found is None else ObjectInfo(found.key, found.size + 1)


def test_object_of_another_size_is_not_recorded(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verification by size: a mismatch gives `upload_unverified` and nothing is recorded."""
    review(world)
    patch_upload_store(monkeypatch, lambda _store: _WrongSize())
    assert error_code(upload(world)) == "upload_unverified"
    assert recorded_keys(world) == []
