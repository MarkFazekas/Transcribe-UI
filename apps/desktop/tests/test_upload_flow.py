"""`run_upload` through the CLI and the backend (plan m5 M5.10; parity goldens, fake media).

The flow: create (the film has an English sidecar, so existing languages are confirmed) → start
the review → review (fake probes) → upload → get → upload again (refused: the batch is `ready`).
Every leg has its own moto bucket and fake Runpod API.
"""

from pathlib import Path

import pytest

from support.batches import FILM, FIRST_BATCH_ID, PODCAST, SILENT, batch_request, create_request, response_of
from support.buckets import UploadLegs, bucket_store
from support.credentials import moto_bucket
from support.golden import assert_golden
from support.parity import Exchange, run_parity
from support.upload import EN, HU, REVIEWED, UPLOAD, BucketState, bucket_state, build_upload_tree
from transcribe_contracts.constants import BATCH_METADATA_PREFIX
from transcribe_contracts.layout import audio_key, manifest_key
from transcribe_contracts.reading import read_manifest
from transcribe_ui.cli.constants import EXIT_ERROR, EXIT_OK

# Names of the flow steps, in order.
STEP_NAMES = ("create", "start_review", "review", "upload", "get", "upload_again")
# Episode: one untagged track used by both languages; the film: Hungarian track 0, English track 1.
EPISODE = "Episode.mkv"
# Key name of the film's English track (its Hungarian track is under `FILM`).
ENGLISH_FILM = "Film.mkv English"


def _run(tmp_path: Path, templates: list[str], legs: UploadLegs) -> list[Exchange]:
    return run_parity(tmp_path, templates, prepare=build_upload_tree, ports=legs)


def _ids(exchange: Exchange) -> dict[str, str]:
    batch = response_of(exchange)["batch"]
    assert isinstance(batch, dict)
    return {member["name"]: member["item_id"] for member in batch["items"]}


def test_flow_matches_goldens(tmp_path: Path) -> None:
    """Each step gives the same response and progress through the CLI and the backend, as recorded."""
    legs = UploadLegs()
    with moto_bucket():
        exchanges = _run(tmp_path, [*REVIEWED, UPLOAD, batch_request("get_batch"), UPLOAD], legs)
        states = [bucket_state(bucket_store(bucket)) for bucket in legs.buckets]
    flow = dict(zip(STEP_NAMES, exchanges, strict=True))
    for number, name in enumerate(STEP_NAMES, start=1):
        assert_golden(f"upload_flow_{number:02d}_{name}.json", flow[name].golden())
    assert (flow["upload"].exit_code, flow["upload_again"].exit_code) == (EXIT_OK, EXIT_ERROR)
    _check_uploaded(_ids(flow["get"]), states)


def _check_uploaded(ids: dict[str, str], states: list[BucketState]) -> None:
    """Both legs hold one object per used track, and the manifest's jobs point at them (nothing for the silent video)."""
    keys = {
        EPISODE: audio_key(FIRST_BATCH_ID, ids[EPISODE], "mka", 0),
        FILM: audio_key(FIRST_BATCH_ID, ids[FILM], "mka", 0),
        ENGLISH_FILM: audio_key(FIRST_BATCH_ID, ids[FILM], "mka", 1),
        PODCAST: audio_key(FIRST_BATCH_ID, ids[PODCAST], "mp3", None),
    }
    expected = sorted(keys.values())
    assert [[key for key, _size in listed] for listed, _manifest in states] == [expected, expected]
    body = states[0][1]
    assert body is not None
    manifest = read_manifest(manifest_key(FIRST_BATCH_ID), body)
    jobs = {(entry.item_id, job.language, job.audio_object_key) for entry in manifest.items for job in entry.jobs}
    assert jobs == {
        (ids[EPISODE], EN, keys[EPISODE]),
        (ids[EPISODE], HU, keys[EPISODE]),
        (ids[FILM], HU, keys[FILM]),
        (ids[FILM], EN, keys[ENGLISH_FILM]),
        (ids[PODCAST], EN, keys[PODCAST]),
        (ids[PODCAST], HU, keys[PODCAST]),
    }
    assert ids[SILENT] not in {entry.item_id for entry in manifest.items}


# A batch whose silent video keeps an English job it cannot resolve (no audio, no English subtitle).
UNRESOLVED = (
    create_request(EN, HU, confirm_existing_languages=True),
    batch_request("start_review"),
    batch_request("review_batch"),
)


@pytest.mark.parametrize(
    ("name", "templates", "seed"),
    [
        ("review_incomplete", [*UNRESOLVED, UPLOAD], ()),
        ("wrong_phase", [create_request(EN, confirm_existing_languages=True), UPLOAD], ()),
        ("batch_id_taken_by_manifest", [*REVIEWED, UPLOAD], (manifest_key(FIRST_BATCH_ID),)),
        ("batch_id_taken_by_objects", [*REVIEWED, UPLOAD], (f"{FIRST_BATCH_ID}/_done.json",)),
    ],
)
def test_refusals(tmp_path: Path, name: str, templates: list[str], seed: tuple[str, ...]) -> None:
    """Refusals change nothing and look the same through the CLI and the backend."""
    legs = UploadLegs(seed=seed)
    with moto_bucket():
        exchanges = _run(tmp_path, [*templates, batch_request("get_batch")], legs)
        listings = [bucket_state(bucket_store(bucket))[0] for bucket in legs.buckets]
    refused = exchanges[-2]
    assert_golden(f"upload_refused_{name}.json", refused.golden())
    batch = response_of(exchanges[-1])["batch"]
    assert isinstance(batch, dict)
    seeded = tuple(key for key in seed if not key.startswith(BATCH_METADATA_PREFIX))
    assert (refused.exit_code, batch["summary"]["phase"] in {"composing", "reviewing"}) == (EXIT_ERROR, True)
    assert [tuple(key for key, _size in listed) for listed in listings] == [seeded, seeded]
