"""The orphan sweep, pending withdrawals and the extract-only path of `run_upload` (plan m5 M5.10).

A leftover manifest is one an interrupted manifest step wrote; a pending withdrawal records it
(its digest and the affected items) when R2 failed after an exclusion or a re-review. Here both
are seeded directly, with the batch put into `uploading` after its review.
"""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from support.batch_backend import error_code, run_ok
from support.batches import FILM, FIRST_BATCH_ID, MEDIA_ROOT, SILENT, batch_request, create_request
from support.credentials import moto_bucket
from support.runs import JSON_TYPE, RUN_ID, RunsWorld, app_pod_name, seed_run
from support.upload import HU, extracted_count, outcome_of, phase_of, review, set_phase, upload, upload_world
from support.withdrawal import all_present, none_present, pending_of, result_keys
from transcribe_contracts.layout import audio_key, done_key, manifest_key
from transcribe_contracts.reading import read_manifest
from transcribe_ui.batches.payloads import PendingWithdrawal
from transcribe_ui.runpod.constants import POD_TERMINATED
from transcribe_ui.state.models import BatchRow

# Files of the batch with `transcribe` jobs (the silent video only extracts).
TRANSCRIBED_FILES = 3
# Key of the batch's manifest.
MANIFEST = manifest_key(FIRST_BATCH_ID)
# Bytes of the seeded leftover manifest; the pending withdrawal records their digest, so it is ours.
LEFTOVER = b'{"leftover": "a manifest of an earlier state"}'
# An item this database no longer knows (excluded while its objects could not be deleted).
GONE_ITEM = "01JDZZZZZZZZZZZZZZZZZZZZ09"
# The requests of a batch with only the silent video, whose Hungarian job extracts a subtitle.
SILENT_ONLY = (
    create_request(HU, paths=(f"{MEDIA_ROOT}/Movies/{SILENT}",)),
    batch_request("start_review"),
    batch_request("review_batch"),
)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunsWorld]:
    """A backend on the upload tree over moto and the fake Runpod API (fixed clock)."""
    with moto_bucket(), upload_world(tmp_path / "leg") as created:
        yield created


def _seed_leftover(world: RunsWorld, affected: str) -> tuple[str, ...]:
    """Put the batch in `uploading` with a leftover manifest of ours, `_done.json` and the affected item's results."""
    set_phase(world, "uploading")
    world.store.write(MANIFEST, LEFTOVER, JSON_TYPE)
    worker_keys = (done_key(FIRST_BATCH_ID), *result_keys(FIRST_BATCH_ID, affected))
    for key in worker_keys:
        world.store.write(key, b"{}", JSON_TYPE)
    pending = PendingWithdrawal(manifest_sha256=hashlib.sha256(LEFTOVER).hexdigest(), item_ids=(affected,))
    with world.ports.state.session() as session:
        row = session.get(BatchRow, FIRST_BATCH_ID)
        assert row is not None
        row.pending_withdrawal = pending.model_dump(mode="json")
    return (MANIFEST, *worker_keys)


def _seed_orphans(world: RunsWorld, film: str) -> tuple[str, str]:
    """Write an object of a forgotten item and an unrecorded one of the film; return their keys."""
    orphans = (audio_key(FIRST_BATCH_ID, GONE_ITEM, "mka", 0), audio_key(FIRST_BATCH_ID, film, "flac", 1))
    for key in orphans:
        world.store.write(key, b"orphan", "audio/x-matroska")
    return orphans


def test_extract_only_batch_is_transcribed(world: RunsWorld) -> None:
    """Only `extract` jobs: the SRT is stored, nothing is uploaded, the batch is `transcribed` without a manifest."""
    review(world, *SILENT_ONLY)
    response = upload(world)
    jobs = run_ok(world.leg, batch_request("get_batch"))["batch"]["items"][0]["jobs"]
    assert (outcome_of(response), phase_of(world), extracted_count(world)) == ("transcribed", "transcribed", 1)
    assert ([job["status"] for job in jobs], world.store.list_objects(f"{FIRST_BATCH_ID}/")) == (["extracted"], ())
    assert world.store.head(MANIFEST) is None


def test_extract_only_finishes_pending_withdrawal(world: RunsWorld) -> None:
    """A leftover manifest of an extract-only batch is withdrawn before it becomes `transcribed`."""
    ids = review(world, *SILENT_ONLY)
    seeded = _seed_leftover(world, ids[SILENT])
    assert outcome_of(upload(world)) == "transcribed"
    assert (none_present(world, *seeded), pending_of(world)) == (True, None)


def test_extract_only_waits_for_a_run_to_withdraw(world: RunsWorld) -> None:
    """During a run the leftover cannot be withdrawn: `run_active`, the batch stays `uploading`, R2 untouched."""
    ids = review(world, *SILENT_ONLY)
    seeded = _seed_leftover(world, ids[SILENT])
    seed_run(world, "running")
    assert error_code(upload(world)) == "run_active"
    assert (phase_of(world), all_present(world, *seeded), pending_of(world) is not None) == ("uploading", True, True)


def test_sweep_withdraws_first_then_deletes_orphans(world: RunsWorld) -> None:
    """Orphans go only after the pending withdrawal is finished; the upload then publishes a fresh manifest."""
    ids = review(world)
    seeded = _seed_leftover(world, GONE_ITEM)
    orphans = _seed_orphans(world, ids[FILM])
    response = upload(world)
    document = response.model_dump(mode="json")
    assert (outcome_of(response), document["deleted_objects"], document["sweep_skipped"]) == ("ready", 2, False)
    assert none_present(world, *orphans, *seeded[1:])
    body = world.store.read(MANIFEST)
    assert body is not None
    assert body != LEFTOVER
    assert len(read_manifest(MANIFEST, body).items) == TRANSCRIBED_FILES


def test_sweep_put_off_during_a_run(world: RunsWorld) -> None:
    """With a pending withdrawal and a live pod the orphans stay (the manifest is held); after the run they go."""
    ids = review(world)
    seeded = _seed_leftover(world, GONE_ITEM)
    orphans = _seed_orphans(world, ids[FILM])
    pod = world.cloud.api.pods.add(app_pod_name(RUN_ID, world.now()))
    held = upload(world)
    document = held.model_dump(mode="json")
    assert (outcome_of(held), document["sweep_skipped"], document["deleted_objects"]) == ("held", True, 0)
    assert all_present(world, *orphans, *seeded)
    pod["status"] = POD_TERMINATED
    done = upload(world)
    assert (outcome_of(done), done.model_dump(mode="json")["deleted_objects"]) == ("ready", 2)
    assert none_present(world, *orphans, *seeded[1:])
