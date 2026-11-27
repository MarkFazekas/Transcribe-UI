"""`create_retry_batch`: copy, resume, idempotency and refusals (plan m5 M5.14).

The source batch is seeded as a finished run leaves it (`support/cleanup.py`); the retry batch
copies the film's audio server side in moto, and its manifest step uses the fake Runpod API.
"""

import contextlib
import itertools
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from support.batch_backend import error_code, error_of
from support.batches import FIRST_BATCH_ID, SECOND_BATCH_ID, batch_request
from support.buckets import create_bucket
from support.cleanup import (
    AUDIO,
    EVERYTHING,
    FILM_ID,
    PODCAST_ID,
    batch_ids,
    cleanup_document,
    confirmed,
    flags,
    keys_under,
    patch_store,
    retry_document,
    seed_world,
    send,
)
from support.credentials import moto_bucket
from support.interrupt import Crash, TripStore, Tripwire
from support.launch import ok_as
from support.runs import RunsWorld
from support.upload import set_phase, upload_world
from transcribe_contracts.layout import audio_key, manifest_key
from transcribe_contracts.reading import read_manifest
from transcribe_ui.api.classes import Response
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.payloads import BatchPayload, StoredComposition
from transcribe_ui.batches.responses import GetBatchResponse
from transcribe_ui.cleanup.classes import CleanupCloudResponse, CreateRetryBatchResponse
from transcribe_ui.cleanup.origins import ExistingRetry
from transcribe_ui.cleanup.planning import RetryPlan
from transcribe_ui.cleanup.retry import retry_batch_id
from transcribe_ui.discovery.classes import ScanSpec
from transcribe_ui.state.models import BatchRow
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.responses import RunUploadResponse

# The id a retry batch gets when the source's id and `SECOND_BATCH_ID` are taken.
THIRD_BATCH_ID = "batch_20261127_130000_3"
# Extension and track of the seeded film audio (`support/batches.py`).
FILM_EXTENSION = "mka"
FILM_TRACK = 1
# A copy path makes at least these R2 calls (id check, abort, sweep, delete, copy, verify, manifest).
MIN_RETRY_CALLS = 8
# Keys of a finished retry batch: its manifest and its one copied audio object.
RETRY_KEYS = 2
# The source's ids: the retry batch's id comes next.
BOTH_BATCHES = (FIRST_BATCH_ID, SECOND_BATCH_ID)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunsWorld]:
    """A backend over moto and the fake Runpod API (fixed clock) with the source batch seeded."""
    with moto_bucket(), upload_world(tmp_path / "leg") as created:
        seed_world(created)
        yield created


def _retry(world: RunsWorld, *item_ids: str) -> Response:
    return send(world, retry_document(*item_ids))


def _retried(world: RunsWorld) -> CreateRetryBatchResponse:
    return ok_as(_retry(world), CreateRetryBatchResponse)


def _copied_key(world: RunsWorld, batch_id: str = SECOND_BATCH_ID) -> str:
    """Return the key of the retry item's copied audio."""
    audio = [key for key in keys_under(world.store, batch_id) if "/audio/" in key]
    assert len(audio) == 1, audio
    return audio[0]


def test_retry_copies_the_flagged_job_carefully(world: RunsWorld) -> None:
    """The failed film job goes to a new batch: audio copied server side, manifest with a `careful` job."""
    retried = _retried(world)
    assert (retried.created, retried.outcome, retried.copied_objects) == (True, "ready", 1)
    assert retried.batch.batch_id == SECOND_BATCH_ID
    body = world.store.read(manifest_key(SECOND_BATCH_ID))
    assert body is not None
    manifest = read_manifest(manifest_key(SECOND_BATCH_ID), body)
    jobs = [(job.language, job.mode, job.audio_object_key) for entry in manifest.items for job in entry.jobs]
    assert jobs == [("en", "careful", _copied_key(world))]
    source_audio = audio_key(FIRST_BATCH_ID, FILM_ID, FILM_EXTENSION, FILM_TRACK)
    assert world.store.head(source_audio) is not None


def test_retry_batch_keeps_no_composition(world: RunsWorld) -> None:
    """A retry batch is not composed from a selection: even from a source that keeps one, it has none."""
    selection = StoredComposition(scan=ScanSpec(root="/media"), operations=(), languages=("en",), item_languages=())
    with world.ports.state.session() as session:
        row = session.get(BatchRow, FIRST_BATCH_ID)
        assert row is not None
        source = BatchPayload.model_validate(row.payload).model_copy(update={"composition": selection})
        row.payload = source.model_dump(mode="json")
    assert _retry(world).status == "ok"
    details = [send(world, batch_request("get_batch", batch_id)) for batch_id in BOTH_BATCHES]
    compositions = [ok_as(detail, GetBatchResponse).batch.composition for detail in details]
    assert compositions == [selection, None]


def test_repeated_retry_returns_the_same_batch(world: RunsWorld) -> None:
    """The same source and job set again: the existing retry batch, nothing copied, no second batch."""
    _retried(world)
    again = _retried(world)
    assert (again.created, again.outcome, again.copied_objects) == (False, "past_upload", 0)
    assert again.batch.batch_id == SECOND_BATCH_ID
    assert batch_ids(world) == list(BOTH_BATCHES)


def test_retry_refuses_jobs_that_are_not_flagged(world: RunsWorld) -> None:
    """Only failed or no-speech transcribe jobs of the source: nothing is created otherwise."""
    assert error_of(_retry(world, PODCAST_ID)) == ("retry_job_invalid", {"job": 0, "reason": "not_flagged"})
    unknown = "01JDZZZZZZZZZZZZZZZZZZZZ77"
    assert error_of(_retry(world, FILM_ID, unknown))[1] == {"job": 1, "reason": "no_such_job"}
    assert error_code(_retry(world, FILM_ID, FILM_ID)) == "invalid_request"
    assert batch_ids(world) == [FIRST_BATCH_ID]


def test_retry_of_a_batch_without_results_is_refused(tmp_path: Path) -> None:
    """A source still `ready` has no flags to retry: `wrong_phase`."""
    with moto_bucket(), upload_world(tmp_path / "leg") as world:
        seed_world(world, phase="ready")
        assert error_code(_retry(world)) == "wrong_phase"


def test_retry_refused_after_audio_cleanup(world: RunsWorld) -> None:
    """Once the source's audio is cleaned up, a retry is refused with `audio_deleted`."""
    ok_as(send(world, confirmed(AUDIO)), CleanupCloudResponse)
    assert error_of(_retry(world)) == ("audio_deleted", {"batch_id": FIRST_BATCH_ID})
    assert batch_ids(world) == [FIRST_BATCH_ID]


def test_retry_skips_an_id_taken_in_r2(world: RunsWorld) -> None:
    """Another computer's data under the next free id: the retry batch takes the one after it."""
    foreign = f"{SECOND_BATCH_ID}/audio/foreign.mka"
    world.store.write(foreign, b"x", "audio/x-matroska")
    assert _retried(world).batch.batch_id == THIRD_BATCH_ID
    assert keys_under(world.store, SECOND_BATCH_ID) == [foreign]


def _killed_then_resumed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wire: Tripwire) -> bool:
    """Kill a retry at the wire on a fresh bucket, then repeat the request; return whether the wire fired."""
    bucket = f"retry-{wire.at}-{int(wire.after)}"
    create_bucket(bucket)
    with upload_world(tmp_path / bucket, bucket) as world:
        seed_world(world)
        patch_store(monkeypatch, "services", lambda store: TripStore(store, wire))
        with contextlib.suppress(Crash):
            _retry(world)
        patch_store(monkeypatch, "services", lambda store: store)
        if not wire.fired:
            return False
        resumed = _retried(world)
        assert (resumed.outcome, resumed.batch.batch_id) == ("ready", SECOND_BATCH_ID), wire
        assert batch_ids(world) == list(BOTH_BATCHES), wire
        assert len(keys_under(world.store, SECOND_BATCH_ID)) == RETRY_KEYS, wire
    return True


@pytest.mark.parametrize("after", [False, True])
def test_retry_interrupted_at_every_call_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, after: bool
) -> None:
    """A kill before or after any R2 call: repeating the request finishes the one retry batch."""
    fired = 0
    with moto_bucket():
        for at in itertools.count(1):
            if not _killed_then_resumed(tmp_path, monkeypatch, Tripwire(at=at, after=after)):
                break
            fired += 1
    assert fired >= MIN_RETRY_CALLS


def _crash(*_args: object) -> None:
    raise Crash


def _created_only(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Send a retry killed right after its batch was created (nothing copied yet)."""
    monkeypatch.setattr("transcribe_ui.cleanup.services.continue_upload", _crash)
    with contextlib.suppress(Crash):
        _retry(world)
    monkeypatch.undo()


def test_interrupted_retry_resumes_through_run_upload(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """A retry killed after its batch was created is finished by `run_upload` on the retry batch too."""
    _created_only(world, monkeypatch)
    assert flags(world, SECOND_BATCH_ID) == ("uploading", False)
    uploaded = ok_as(send(world, batch_request("run_upload", SECOND_BATCH_ID)), RunUploadResponse)
    assert (uploaded.outcome, uploaded.uploaded_objects) == ("ready", 1)
    assert _retried(world).outcome == "past_upload"


def test_source_cleanup_waits_for_a_copying_retry(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """A retry batch still copying keeps its source's audio: both scopes refused, naming the retry batch."""
    _created_only(world, monkeypatch)
    refusal = ("retry_copying", {"batch_id": FIRST_BATCH_ID, "retry_batch_id": SECOND_BATCH_ID})
    requests = (confirmed(AUDIO), confirmed(EVERYTHING), cleanup_document(AUDIO))
    for request in requests:
        assert error_of(send(world, request)) == refusal
    assert _retried(world).outcome == "ready"
    ok_as(send(world, confirmed(AUDIO)), CleanupCloudResponse)
    assert flags(world) == ("transcribed", True)
    assert world.store.head(_copied_key(world)) is not None


@dataclass(frozen=True)
class _CleanedMeanwhile:
    """`retry_batch_id`, but the retry batch is cleaned up right after it was found."""

    world: RunsWorld

    def __call__(self, context: RequestContext, store: ObjectStore, target: ExistingRetry | RetryPlan) -> str:
        """Return the retry batch's id after moving it to `cleaned`."""
        retry_id = retry_batch_id(context, store, target)
        set_phase(self.world, "cleaned", retry_id)
        return retry_id


def test_retry_cleaned_up_meanwhile_is_not_copied(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """A retry batch cleaned up between the lookup and its lock is refused; nothing is copied into it."""
    _created_only(world, monkeypatch)
    monkeypatch.setattr("transcribe_ui.cleanup.services.retry_batch_id", _CleanedMeanwhile(world))
    code, details = error_of(_retry(world))
    assert (code, details["phase"]) == ("wrong_phase", "cleaned")
    assert not keys_under(world.store, SECOND_BATCH_ID)
