"""Sources that change during the upload, and a manifest published during a run (plan m5 M5.10, M6.13)."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from support.batch_backend import item_phase, run_ok
from support.batches import FILM, FIRST_BATCH_ID, PODCAST, batch_request
from support.credentials import moto_bucket
from support.interrupt import EditDuringConversion, TripMedia, trip_media
from support.runs import RunsWorld, seed_run
from support.upload import (
    audio_keys,
    change_source,
    outcome_of,
    phase_of,
    recorded_keys,
    review,
    upload,
    upload_world,
)
from transcribe_contracts.layout import audio_prefix, manifest_key
from transcribe_contracts.reading import read_manifest


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunsWorld]:
    """A backend on the upload tree over moto and the fake Runpod API (fixed clock)."""
    with moto_bucket(), upload_world(tmp_path / "leg", wrap=trip_media) as created:
        yield created


# The film's audio objects: its Hungarian and its English track.
FILM_OBJECTS = 2
# Key of the batch's manifest.
MANIFEST = manifest_key(FIRST_BATCH_ID)


def _manifest_items(world: RunsWorld) -> list[str]:
    body = world.store.read(MANIFEST)
    assert body is not None
    return [entry.item_id for entry in read_manifest(MANIFEST, body).items]


def _under(world: RunsWorld, item_id: str) -> list[str]:
    prefix = audio_prefix(FIRST_BATCH_ID, item_id)
    return [key for key in audio_keys(world.store) if key.startswith(prefix)]


def test_changed_source_back_to_review_then_uploaded(world: RunsWorld) -> None:
    """A file edited after its review goes back to review; once probed again it is uploaded and published."""
    film = review(world)[FILM]
    change_source(world.leg.root, FILM)
    first = upload(world)
    in_review = first.model_dump(mode="json")["items_in_review"]
    assert (outcome_of(first), in_review, item_phase(world.leg, film), _under(world, film)) == (
        "items_in_review",
        [film],
        "reviewing",
        [],
    )
    assert phase_of(world) == "uploading"
    run_ok(world.leg, batch_request("review_batch"))
    assert outcome_of(upload(world)) == "ready"
    assert (film in _manifest_items(world), len(_under(world, film))) == (True, FILM_OBJECTS)


def test_changed_source_excluded_then_published(world: RunsWorld) -> None:
    """A file edited after its review can be excluded instead; the manifest then goes without it."""
    ids = review(world)
    film = ids[FILM]
    change_source(world.leg.root, FILM)
    assert outcome_of(upload(world)) == "items_in_review"
    run_ok(world.leg, batch_request("exclude_items", confirm=True, item_ids=[film]))
    assert outcome_of(upload(world)) == "ready"
    listed = _manifest_items(world)
    assert (film in listed, ids[PODCAST] in listed, _under(world, film)) == (False, True, [])


def test_source_changing_during_upload_back_to_review(world: RunsWorld) -> None:
    """The check runs again after each read: a file edited mid-item loses its objects and goes back to review."""
    film = review(world)[FILM]
    media = world.ports.media
    assert isinstance(media, TripMedia)
    media.inner = EditDuringConversion(media.inner, world.leg.root)
    response = upload(world)
    assert (outcome_of(response), response.model_dump(mode="json")["items_in_review"]) == ("items_in_review", [film])
    assert (_under(world, film), [key for key in recorded_keys(world) if film in key]) == ([], [])


def test_published_during_a_run(world: RunsWorld) -> None:
    """A run active when the upload completes does not keep the manifest back (it is not in that run's lease)."""
    review(world)
    seed_run(world, "running")
    published = upload(world)
    assert (outcome_of(published), phase_of(world), world.store.head(MANIFEST) is not None) == ("ready", "ready", True)
