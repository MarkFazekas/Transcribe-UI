"""`cleanup_cloud`: scopes, confirmations, refusals, interruption and unknown batches (plan m5 M5.14)."""

import contextlib
import itertools
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select

from support.batch_backend import error_code, error_of
from support.batches import FIRST_BATCH_ID, batch_request
from support.buckets import create_bucket, shared_client
from support.cleanup import (
    AUDIO,
    EVERYTHING,
    PODCAST_ID,
    cleanup_document,
    cleanup_pending,
    confirmed,
    flags,
    keys_under,
    patch_store,
    seed_world,
    send,
)
from support.credentials import TEST_BUCKET, moto_bucket
from support.interrupt import Crash, TripStore, Tripwire
from support.launch import ok_as
from support.runs import RUN_ID, RunsWorld, seed_run, seed_uploaded
from support.upload import open_uploads, set_phase, temp_files, upload_world
from transcribe_contracts.layout import done_key, manifest_key
from transcribe_ui.api.classes import NeedsConfirmation, Response
from transcribe_ui.cleanup.classes import CleanupCloudResponse
from transcribe_ui.state.models import JobRow
from transcribe_ui.upload.manifest import build_manifest, write_manifest
from transcribe_ui.upload.temp import temp_dir

# Objects of the seeded source batch: two audio objects, the manifest, `_done.json`, two results of two objects.
SOURCE_OBJECTS = 8
# Audio objects of the seeded source batch.
SOURCE_AUDIO = 2
# The deletes of `everything` make at least these R2 calls (abort, heads, deletes, lists, activity).
MIN_CLEANUP_CALLS = 10
# A batch phase of the tests.
TRANSCRIBED = "transcribed"


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunsWorld]:
    """A backend over moto and the fake Runpod API (fixed clock) with the transcribed source batch."""
    with moto_bucket(), upload_world(tmp_path / "leg") as created:
        seed_world(created)
        yield created


def _cleaned(world: RunsWorld, document: str) -> CleanupCloudResponse:
    return ok_as(send(world, document), CleanupCloudResponse)


def _asked(response: Response) -> NeedsConfirmation:
    return ok_as(response, NeedsConfirmation)


def _fields(response: Response) -> list[str]:
    """Return the confirmation fields a `needs_confirmation` response asks for."""
    return [confirmation.field for confirmation in _asked(response).confirmations]


def test_everything_needs_two_confirmations(world: RunsWorld) -> None:
    """Results not downloaded: `confirm` and `confirm_not_downloaded`; then every object goes, phase `cleaned`."""
    unconfirmed = _fields(send(world, cleanup_document(EVERYTHING)))
    half = _fields(send(world, cleanup_document(EVERYTHING, confirm=True)))
    assert (unconfirmed, half) == (["confirm", "confirm_not_downloaded"], ["confirm_not_downloaded"])
    assert len(keys_under(world.store)) == SOURCE_OBJECTS
    assert _cleaned(world, confirmed(EVERYTHING)).deleted_objects == SOURCE_OBJECTS
    assert (keys_under(world.store), flags(world)) == ([], ("cleaned", True))
    assert not _cleaned(world, confirmed(EVERYTHING)).deleted_objects


def test_audio_cleanup_keeps_the_results_and_the_phase(world: RunsWorld) -> None:
    """`audio` deletes the audio only and sets the flag; the batch stays `transcribed`."""
    assert _fields(send(world, cleanup_document(AUDIO))) == ["confirm"]
    assert _cleaned(world, confirmed(AUDIO)).deleted_objects == SOURCE_AUDIO
    left = keys_under(world.store)
    assert {manifest_key(FIRST_BATCH_ID), done_key(FIRST_BATCH_ID)} <= set(left)
    assert all("/audio/" not in key for key in left)
    assert flags(world) == (TRANSCRIBED, True)


def test_audio_cleanup_waits_for_every_result(tmp_path: Path) -> None:
    """A job without a result still needs its audio: `results_pending`, nothing deleted."""
    with moto_bucket(), upload_world(tmp_path / "leg") as world:
        seed_world(world, phase="ready")
        with world.ports.state.session() as session:
            job = session.scalars(select(JobRow).where(JobRow.item_id == PODCAST_ID)).one()
            job.status = "pending"
        pending = ("results_pending", {"batch_id": FIRST_BATCH_ID, "pending_jobs": 1})
        assert error_of(send(world, confirmed(AUDIO))) == pending
        assert len(keys_under(world.store)) == SOURCE_OBJECTS


def test_cleanup_refused_during_an_active_run(world: RunsWorld) -> None:
    """A published batch cannot be cleaned up while a run is active: `run_active`, nothing changes."""
    seed_run(world, "running")
    for scope in (AUDIO, EVERYTHING):
        code, details = error_of(send(world, confirmed(scope)))
        assert (code, details["run_ids"]) == ("run_active", [RUN_ID])
    assert len(keys_under(world.store)) == SOURCE_OBJECTS
    assert flags(world) == (TRANSCRIBED, False)


@pytest.fixture
def uploading(tmp_path: Path) -> Iterator[RunsWorld]:
    """An `uploading` batch with its audio in the bucket, an open multipart upload, a temp file and a run active."""
    with moto_bucket(), upload_world(tmp_path / "leg") as created:
        seed_uploaded(created)
        shared_client().create_multipart_upload(Bucket=TEST_BUCKET, Key=f"{FIRST_BATCH_ID}/audio/partial.mka")
        folder = temp_dir(created.ports.state.paths, FIRST_BATCH_ID)
        folder.mkdir(parents=True)
        (folder / "left.a1.mka").write_bytes(b"partial")
        seed_run(created, "running")
        yield created


def test_uploading_batch_is_cleaned_up_during_a_run(uploading: RunsWorld) -> None:
    """No manifest, so no worker sees it: allowed during a run; uploads aborted, objects and temp file gone."""
    assert _fields(send(uploading, cleanup_document(EVERYTHING))) == ["confirm"]
    cleaned = _cleaned(uploading, cleanup_document(EVERYTHING, confirm=True))
    assert (cleaned.aborted_uploads, cleaned.deleted_objects) == (1, SOURCE_AUDIO)
    assert (keys_under(uploading.store), open_uploads(), temp_files(uploading)) == ([], 0, [])
    assert flags(uploading) == ("cleaned", True)
    assert error_code(send(uploading, batch_request("run_upload"))) == "wrong_phase"


def test_leftover_manifest_waits_for_the_run(uploading: RunsWorld) -> None:
    """A manifest left in R2 by an interrupted manifest step may be read by the run: `run_active`."""
    with uploading.ports.state.session() as session:
        write_manifest(uploading.store, build_manifest(session, FIRST_BATCH_ID))
    assert error_code(send(uploading, cleanup_document(EVERYTHING, confirm=True))) == "run_active"
    assert (flags(uploading), open_uploads()) == (("uploading", False), 1)


def test_cleaned_batch_with_a_leftover_manifest_waits(uploading: RunsWorld) -> None:
    """A batch already `cleaned` whose manifest is still in R2 (an interrupted cleanup): `run_active`."""
    with uploading.ports.state.session() as session:
        write_manifest(uploading.store, build_manifest(session, FIRST_BATCH_ID))
    set_phase(uploading, "cleaned")
    assert error_code(send(uploading, confirmed(EVERYTHING))) == "run_active"
    assert manifest_key(FIRST_BATCH_ID) in keys_under(uploading.store)


def test_scopes_refused_in_the_wrong_phase(uploading: RunsWorld) -> None:
    """`audio` needs a published batch; a batch in review is deleted, not cleaned up."""
    assert error_code(send(uploading, confirmed(AUDIO))) == "wrong_phase"
    set_phase(uploading, "reviewing")
    code, details = error_of(send(uploading, confirmed(EVERYTHING)))
    assert (code, details["phase"]) == ("wrong_phase", "reviewing")
    assert flags(uploading) == ("reviewing", False)


def _killed_then_resumed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wire: Tripwire) -> bool:
    """Kill an `everything` cleanup at the wire on a fresh bucket, then repeat it; return whether it fired."""
    bucket = f"cleanup-{wire.at}-{int(wire.after)}"
    create_bucket(bucket)
    with upload_world(tmp_path / bucket, bucket) as world:
        seed_world(world)
        patch_store(monkeypatch, "known", lambda store: TripStore(store, wire))
        with contextlib.suppress(Crash):
            send(world, confirmed(EVERYTHING))
        patch_store(monkeypatch, "known", lambda store: store)
        if not wire.fired:
            return False
        _cleaned(world, confirmed(EVERYTHING))
        assert (keys_under(world.store), flags(world)) == ([], ("cleaned", True)), wire
        assert cleanup_pending(world) is None, wire
    return True


@pytest.mark.parametrize("after", [False, True])
def test_cleanup_interrupted_at_every_call_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, after: bool
) -> None:
    """A kill before or after any R2 call of the cleanup: repeating it deletes what is left."""
    fired = 0
    with moto_bucket():
        for at in itertools.count(1):
            if not _killed_then_resumed(tmp_path, monkeypatch, Tripwire(at=at, after=after)):
                break
            fired += 1
    assert fired >= MIN_CLEANUP_CALLS
