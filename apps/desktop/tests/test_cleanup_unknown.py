"""`cleanup_cloud` of a batch this database does not know: another computer's, or an earlier database's (Q28)."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from support.batch_backend import error_code, error_of
from support.cleanup import AUDIO, CLEANUP, EVERYTHING, cleanup_document, keys_under, seed_world, send
from support.credentials import moto_bucket
from support.launch import FOREIGN_BATCH_ID, ok_as, write_foreign
from support.runs import JSON_TYPE, RunsWorld, seed_run
from support.upload import upload_world
from transcribe_contracts.layout import audio_key, done_key, manifest_key, result_json_key
from transcribe_ui.api.classes import NeedsConfirmation
from transcribe_ui.cleanup.classes import CleanupCloudResponse

# Objects of the other computer's batch: manifest, audio, result and `_done.json`.
FOREIGN_OBJECTS = 4
# An item of another computer's batch.
FOREIGN_ITEM = "01JDZZZZZZZZZZZZZZZZZZZZ09"
# A batch id neither this database nor R2 has.
MISSING_BATCH_ID = "batch_20200101_000000"


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunsWorld]:
    """A backend over moto and the fake Runpod API (fixed clock) with a known batch and another computer's."""
    with moto_bucket(), upload_world(tmp_path / "leg") as created:
        seed_world(created)
        yield created


def _seed_foreign(world: RunsWorld) -> list[str]:
    """Publish another computer's batch with its audio, a result and `_done.json`; return its keys."""
    write_foreign(world.store)
    audio = audio_key(FOREIGN_BATCH_ID, FOREIGN_ITEM, "flac", None)
    job_result = result_json_key(FOREIGN_BATCH_ID, FOREIGN_ITEM, "de")
    for key in (audio, job_result, done_key(FOREIGN_BATCH_ID)):
        world.store.write(key, b"{}", JSON_TYPE)
    return keys_under(world.store, FOREIGN_BATCH_ID)


def _foreign(**fields: object) -> str:
    return cleanup_document(EVERYTHING, FOREIGN_BATCH_ID, **fields)


def test_unknown_batch_cleaned_up_once_it_reads(world: RunsWorld) -> None:
    """Another computer's batch: one confirmation naming its files, then every object, the manifest last."""
    assert len(_seed_foreign(world)) == FOREIGN_OBJECTS
    asked = ok_as(send(world, _foreign()), NeedsConfirmation)
    assert asked.confirmations[0].details == {"batch_id": FOREIGN_BATCH_ID, "files": 1, "known": False}
    cleaned = ok_as(send(world, _foreign(confirm=True)), CleanupCloudResponse)
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
    """Only `everything`, and not during a run; a batch nobody has is `batch_not_found`."""
    kept = _seed_foreign(world)
    audio = cleanup_document(AUDIO, FOREIGN_BATCH_ID, confirm=True)
    assert error_code(send(world, audio)) == "batch_not_found"
    seed_run(world, "queued")
    assert error_code(send(world, _foreign(confirm=True))) == "run_active"
    assert keys_under(world.store, FOREIGN_BATCH_ID) == kept
    missing = cleanup_document(EVERYTHING, MISSING_BATCH_ID, confirm=True)
    assert error_code(send(world, missing)) == "batch_not_found"
