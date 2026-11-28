"""`cleanup_cloud` of a batch this database does not know: another computer's, or an earlier database's (Q28)."""

import contextlib
import itertools
import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from support.batch_backend import error_code, error_of
from support.batches import FIRST_BATCH_ID
from support.buckets import create_bucket
from support.cleanup import AUDIO, CLEANUP, EVERYTHING, cleanup_document, keys_under, patch_store, seed_world, send
from support.confirm import confirmed_document
from support.credentials import moto_bucket
from support.interrupt import Crash, TripStore, Tripwire
from support.launch import FOREIGN_BATCH_ID, foreign_manifest, ok_as, write_foreign
from support.runs import JSON_TYPE, RUN_ID, RunsWorld
from support.upload import upload_world
from transcribe_contracts.layout import audio_key, deleting_key, done_key, manifest_key, result_json_key
from transcribe_ui.api.classes import NeedsConfirmation
from transcribe_ui.cleanup.classes import CleanupCloudResponse
from transcribe_ui.runs.leases import write_lease

# Objects of the other computer's batch: manifest, audio, result and `_done.json`.
FOREIGN_OBJECTS = 4
# Objects of the other computer's batch not transcribed yet: manifest and audio.
NEW_JOB_OBJECTS = 2
# An item of another computer's batch.
FOREIGN_ITEM = "01JDZZZZZZZZZZZZZZZZZZZZ09"
# The question of an interrupted cleanup's leftovers, and of another computer's batch.
LEFTOVER = "cleanup_unknown_leftover"
UNKNOWN_ELSEWHERE = "cleanup_unknown_batch_uploaded_elsewhere"
# A batch id neither this database nor R2 has.
MISSING_BATCH_ID = "batch_20200101_000000"


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunsWorld]:
    """A backend over moto and the fake Runpod API (fixed clock) with a known batch and another computer's."""
    with moto_bucket(), upload_world(tmp_path / "leg") as created:
        seed_world(created)
        yield created


def _seed_foreign(world: RunsWorld, *, transcribed: bool = True) -> list[str]:
    """Publish another computer's batch with its audio (and, `transcribed`, a result and `_done.json`); return its keys."""
    write_foreign(world.store)
    keys = [audio_key(FOREIGN_BATCH_ID, FOREIGN_ITEM, "flac", None)]
    if transcribed:
        keys.extend((result_json_key(FOREIGN_BATCH_ID, FOREIGN_ITEM, "de"), done_key(FOREIGN_BATCH_ID)))
    for key in keys:
        world.store.write(key, b"{}", JSON_TYPE)
    return keys_under(world.store, FOREIGN_BATCH_ID)


def _left(world: RunsWorld) -> list[str]:
    return keys_under(world.store, FOREIGN_BATCH_ID)


def _foreign(**fields: object) -> str:
    return cleanup_document(EVERYTHING, FOREIGN_BATCH_ID, **fields)


def test_unknown_batch_cleaned_up_once_it_reads(world: RunsWorld) -> None:
    """Another computer's batch: one confirmation naming its files, then every object (the marker is not counted)."""
    assert len(_seed_foreign(world)) == FOREIGN_OBJECTS
    asked = ok_as(send(world, _foreign()), NeedsConfirmation)
    assert asked.confirmations[0].details == {"batch_id": FOREIGN_BATCH_ID, "files": 1, "known": False}
    cleaned = ok_as(send(world, confirmed_document(_foreign(), asked)), CleanupCloudResponse)
    assert (cleaned.batch, cleaned.deleted_objects) == (None, FOREIGN_OBJECTS)
    assert not keys_under(world.store, FOREIGN_BATCH_ID)
    assert error_code(send(world, _foreign(confirm=True))) == "batch_not_found"


def test_unknown_batch_with_an_unreadable_manifest(world: RunsWorld) -> None:
    """A manifest that does not read (here: not JSON): `manifest_unreadable`, nothing deleted."""
    kept = _seed_foreign(world)
    world.store.write(manifest_key(FOREIGN_BATCH_ID), b"{not json", JSON_TYPE)
    assert error_of(send(world, _foreign(confirm=True))) == ("manifest_unreadable", {"batch_id": FOREIGN_BATCH_ID})
    assert keys_under(world.store, FOREIGN_BATCH_ID) == kept


@pytest.mark.parametrize("batch_id", ["batch_../x", "batch_2026", "runs", ""])
def test_unknown_batch_with_an_invalid_id_is_refused(world: RunsWorld, batch_id: str) -> None:
    """The id must be a batch id of the R2 layout: `invalid_request` before anything is read."""
    document = json.dumps({"action": CLEANUP, "batch_id": batch_id, "scope": EVERYTHING, "confirm": True})
    assert error_code(send(world, document)) == "invalid_request"


def test_unknown_batch_refusals(world: RunsWorld) -> None:
    """Only `everything`, and not while a run's lease lists it; a batch nobody has is `batch_not_found`."""
    kept = _seed_foreign(world)
    audio = cleanup_document(AUDIO, FOREIGN_BATCH_ID, confirm=True)
    assert error_code(send(world, audio)) == "batch_not_found"
    write_lease(world.store, RUN_ID, (FOREIGN_BATCH_ID,), world.now() + timedelta(hours=1))
    assert error_code(send(world, _foreign(confirm=True))) == "run_active"
    assert keys_under(world.store, FOREIGN_BATCH_ID) == kept
    missing = cleanup_document(EVERYTHING, MISSING_BATCH_ID, confirm=True)
    assert error_code(send(world, missing)) == "batch_not_found"


def _unknown_killed_then_resumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wire: Tripwire,
    *,
    transcribed: bool,
) -> bool:
    """Kill an unknown batch's cleanup at the wire, check the manifest or the marker stays, repeat; return whether it fired."""
    bucket = f"unknown-{wire.at}-{int(wire.after)}-{int(transcribed)}"
    create_bucket(bucket)
    with upload_world(tmp_path / bucket, bucket) as world:
        _seed_foreign(world, transcribed=transcribed)
        patch_store(monkeypatch, "unknown", lambda store: TripStore(store, wire))
        with contextlib.suppress(Crash):
            send(world, _foreign(confirm=True))
        patch_store(monkeypatch, "unknown", lambda store: store)
        if not wire.fired:
            return False
        left = keys_under(world.store, FOREIGN_BATCH_ID)
        proofs = {manifest_key(FOREIGN_BATCH_ID), deleting_key(FOREIGN_BATCH_ID)}
        assert not left or proofs & set(left), (wire, left)
        if left:
            ok_as(send(world, _foreign(confirm=True)), CleanupCloudResponse)
        assert not keys_under(world.store, FOREIGN_BATCH_ID), wire
    return True


@pytest.mark.parametrize("transcribed", [True, False])
@pytest.mark.parametrize("after", [False, True])
def test_unknown_cleanup_interrupted_at_every_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, after: bool, transcribed: bool
) -> None:
    """A kill before or after any R2 call, transcribed or not: the manifest or the marker stays while data is left."""
    fired = 0
    with moto_bucket():
        for at in itertools.count(1):
            wire = Tripwire(at=at, after=after)
            if not _unknown_killed_then_resumed(tmp_path, monkeypatch, wire, transcribed=transcribed):
                break
            fired += 1
    floor = FOREIGN_OBJECTS if transcribed else NEW_JOB_OBJECTS
    assert fired >= floor


def test_a_new_job_is_resumed_after_its_manifest(world: RunsWorld) -> None:
    """Interrupted after the manifest delete (the marker and the audio are left): its own question, then the rest."""
    _seed_foreign(world, transcribed=False)
    world.store.write(deleting_key(FOREIGN_BATCH_ID), b"{}", JSON_TYPE)
    world.store.delete(manifest_key(FOREIGN_BATCH_ID))
    asked = ok_as(send(world, _foreign()), NeedsConfirmation)
    assert [(question.code, question.field) for question in asked.confirmations] == [(LEFTOVER, "confirm")]
    cleaned = ok_as(send(world, confirmed_document(_foreign(), asked)), CleanupCloudResponse)
    assert (cleaned.deleted_objects, _left(world)) == (1, [])


def test_a_prefix_without_marker_is_not_touched(world: RunsWorld) -> None:
    """No manifest and no marker (perhaps an upload in progress elsewhere): `batch_not_found`, `_done.json` or not."""
    kept = _seed_foreign(world)
    world.store.delete(manifest_key(FOREIGN_BATCH_ID))
    assert error_code(send(world, _foreign(confirm=True))) == "batch_not_found"
    assert _left(world) == [key for key in kept if key != manifest_key(FOREIGN_BATCH_ID)]


def test_another_batch_under_a_known_id_asks_again(world: RunsWorld) -> None:
    """The known batch's questions do not cover another computer's batch under its id: one more, `confirm_unknown`."""
    body = foreign_manifest(FIRST_BATCH_ID).model_dump_json().encode()
    world.store.write(manifest_key(FIRST_BATCH_ID), body, JSON_TYPE)
    document = cleanup_document(EVERYTHING, FIRST_BATCH_ID, confirm=True, confirm_not_downloaded=True)
    asked = ok_as(send(world, document), NeedsConfirmation)
    assert [(question.code, question.field) for question in asked.confirmations] == [
        (UNKNOWN_ELSEWHERE, "confirm_unknown")
    ]
    assert world.store.read(manifest_key(FIRST_BATCH_ID)) == body
    cleaned = ok_as(send(world, confirmed_document(document, asked)), CleanupCloudResponse)
    assert (cleaned.batch, world.store.read(manifest_key(FIRST_BATCH_ID))) == (None, None)
